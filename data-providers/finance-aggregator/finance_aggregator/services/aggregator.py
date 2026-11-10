"""Aggregation service: one StockDataSource backed by an ordered chain of providers.

Fallback rules (per request):
  1. Providers are tried in priority order.
  2. A provider whose cheap `is_available()` check fails is skipped (UNAVAILABLE).
  3. DataNotFoundError, ProviderUnavailableError and ProviderError all move on to the next provider.
  4. Price history is MERGED: bars from a higher-priority provider are kept; if they end more than
     `history_max_staleness_days` before the requested end (e.g. yahoo-db not synced for a while),
     only the missing tail is requested from the next providers. Every bar keeps the name of the
     provider that supplied it (`PriceBar.source`).
  5. When nobody returns data:
       - at least one provider answered "not found"  -> DataNotFoundError
       - otherwise (nobody could answer)               -> ProviderUnavailableError
     Both carry the per-provider attempts for diagnostics.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, NoReturn, Sequence, TypeVar

from ..core.errors import (
    DataNotFoundError,
    InvalidRequestError,
    ProviderError,
    ProviderUnavailableError,
)
from ..core.interfaces import DataProvider, FinanceService, StockDataSource
from ..core.models import (
    AttemptOutcome,
    DateRange,
    HealthState,
    PriceBar,
    PriceHistory,
    ProviderAttempt,
    ServiceStatus,
    TickerInfo,
    normalize_symbol,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class FallbackPolicy:
    #: None disables tail filling (the first provider with data wins).
    history_max_staleness_days: int | None = 5

    def is_fresh(self, last_date: date, requested_end: date) -> bool:
        if self.history_max_staleness_days is None:
            return True
        return (requested_end - last_date).days <= self.history_max_staleness_days


class AggregatorService(FinanceService, StockDataSource):
    def __init__(
        self,
        providers: Sequence[DataProvider],
        *,
        policy: FallbackPolicy | None = None,
        name: str = "aggregator",
    ) -> None:
        self._providers = tuple(providers)
        self._policy = policy or FallbackPolicy()
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def providers(self) -> tuple[DataProvider, ...]:
        return self._providers

    @property
    def policy(self) -> FallbackPolicy:
        return self._policy

    # ---- StockDataSource -------------------------------------------------------------------

    def get_ticker_info(self, symbol: str) -> TickerInfo:
        normalized = normalize_symbol(symbol)
        return self._first_answer(
            f"ticker info for {normalized}", lambda p: p.get_ticker_info(normalized)
        )

    def get_price_history(self, symbol: str, date_range: DateRange) -> PriceHistory:
        normalized = normalize_symbol(symbol)
        what = f"price history for {normalized} in {date_range}"
        attempts: list[ProviderAttempt] = []
        merged: dict[date, PriceBar] = {}
        contributors: list[str] = []

        for provider in self._providers:
            if merged and self._policy.is_fresh(max(merged), date_range.end):
                break
            # Only ask lower-priority providers for what is still missing at the end.
            request = DateRange(max(merged) + timedelta(days=1), date_range.end) if merged else date_range
            if not provider.is_available():
                self._skip(provider, what, attempts)
                continue

            started = time.perf_counter()
            outcome, detail, result = self._call(provider, lambda p: p.get_price_history(normalized, request))
            elapsed = round((time.perf_counter() - started) * 1000, 1)

            if result is not None:
                added = [b for b in result.bars if request.start <= b.date <= request.end and b.date not in merged]
                for bar in added:
                    merged[bar.date] = bar if bar.source else bar.with_source(provider.name)
                if added:
                    contributors.append(provider.name)
                    fresh = self._policy.is_fresh(max(merged), date_range.end)
                    outcome = AttemptOutcome.SUCCESS if fresh else AttemptOutcome.INCOMPLETE
                    detail = f"{len(added)} bars {added[0].date}..{added[-1].date}"
                else:
                    outcome, detail = AttemptOutcome.NOT_FOUND, f"no bars in {request}"
            attempts.append(ProviderAttempt(provider.name, outcome, detail, elapsed))
            self._log_attempt(what, provider.name, outcome, detail)

        if merged:
            history = PriceHistory(normalized, date_range, tuple(merged.values()), "+".join(contributors))
            logger.info("%s served by %s (%s)", what, history.source,
                        ", ".join(f"{name}: {count}" for name, count in history.source_counts.items()))
            self._log_fallbacks(what, attempts)
            return history
        self._raise_no_data(what, attempts)

    # ---- FinanceService --------------------------------------------------------------------

    def check_status(self) -> ServiceStatus:
        if not self._providers:
            return ServiceStatus(self._name, HealthState.UNAVAILABLE, (), "no providers configured")
        with ThreadPoolExecutor(max_workers=len(self._providers), thread_name_prefix="status") as pool:
            statuses = tuple(pool.map(lambda p: p.check_status(), self._providers))
        available = sum(1 for s in statuses if s.is_available)
        if available == len(statuses):
            state = HealthState.AVAILABLE
        elif available:
            state = HealthState.DEGRADED
        else:
            state = HealthState.UNAVAILABLE
        return ServiceStatus(
            self._name, state, statuses, f"{available}/{len(statuses)} providers available"
        )

    def close(self) -> None:
        for provider in self._providers:
            try:
                provider.close()
            except Exception:  # noqa: BLE001 - closing must not stop the others
                logger.exception("Failed to close provider %s", provider.name)

    # ---- fallback engine -------------------------------------------------------------------

    def _first_answer(self, what: str, call: Callable[[DataProvider], T]) -> T:
        attempts: list[ProviderAttempt] = []
        for provider in self._providers:
            if not provider.is_available():
                self._skip(provider, what, attempts)
                continue

            started = time.perf_counter()
            outcome, detail, result = self._call(provider, call)
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            attempts.append(ProviderAttempt(provider.name, outcome, detail, elapsed))

            if result is not None:
                logger.info("%s served by %s (%.1f ms)", what, provider.name, elapsed)
                self._log_fallbacks(what, attempts)
                return result
            self._log_attempt(what, provider.name, outcome, detail)
        self._raise_no_data(what, attempts)

    @staticmethod
    def _skip(provider: DataProvider, what: str, attempts: list[ProviderAttempt]) -> None:
        reason = provider.unavailable_reason()
        attempts.append(ProviderAttempt(provider.name, AttemptOutcome.UNAVAILABLE, reason))
        logger.debug("Skipping %s for %s: %s", provider.name, what, reason)

    @staticmethod
    def _log_attempt(what: str, provider: str, outcome: AttemptOutcome, detail: str) -> None:
        log = logger.warning if outcome in (AttemptOutcome.UNAVAILABLE, AttemptOutcome.ERROR) else logger.info
        log("%s: provider %s -> %s %s", what, provider, outcome.value, detail)

    @staticmethod
    def _raise_no_data(what: str, attempts: Sequence[ProviderAttempt]) -> NoReturn:
        summary = "; ".join(map(str, attempts)) or "no providers configured"
        if any(a.outcome is AttemptOutcome.NOT_FOUND for a in attempts):
            raise DataNotFoundError(f"No {what} found ({summary})", attempts=attempts)
        raise ProviderUnavailableError(f"No provider could supply {what} ({summary})", attempts=attempts)

    @staticmethod
    def _call(
        provider: DataProvider, call: Callable[[DataProvider], T]
    ) -> tuple[AttemptOutcome, str, T | None]:
        try:
            return AttemptOutcome.SUCCESS, "", call(provider)
        except InvalidRequestError:
            raise
        except DataNotFoundError as exc:
            return AttemptOutcome.NOT_FOUND, str(exc), None
        except ProviderUnavailableError as exc:
            return AttemptOutcome.UNAVAILABLE, str(exc), None
        except ProviderError as exc:
            return AttemptOutcome.ERROR, str(exc), None
        except Exception as exc:  # noqa: BLE001 - a buggy provider must not break the chain
            logger.exception("Provider %s raised an unexpected exception", provider.name)
            return AttemptOutcome.ERROR, f"unexpected {type(exc).__name__}: {exc}", None

    @staticmethod
    def _log_fallbacks(what: str, attempts: Sequence[ProviderAttempt]) -> None:
        if len(attempts) > 1:
            logger.info("%s fallback chain: %s", what, " -> ".join(map(str, attempts)))
