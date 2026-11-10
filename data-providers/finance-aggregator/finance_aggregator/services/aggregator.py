"""Aggregation service: one StockDataSource backed by an ordered chain of providers.

Fallback rules (per request):
  1. Providers are tried in priority order.
  2. A provider whose cheap `is_available()` check fails is skipped (UNAVAILABLE).
  3. DataNotFoundError, ProviderUnavailableError and ProviderError all move on to the next provider.
  4. Price history ending more than `history_max_staleness_days` before the requested end is
     INCOMPLETE: kept as a candidate while later providers are tried; returned if nobody does better.
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
from typing import Callable, Sequence, TypeVar

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
    #: None disables the freshness check.
    history_max_staleness_days: int | None = 5

    def is_complete(self, history: PriceHistory) -> bool:
        if self.history_max_staleness_days is None or history.last_date is None:
            return True
        gap = (history.date_range.end - history.last_date).days
        return gap <= self.history_max_staleness_days


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
        return self._first_answer(
            f"price history for {normalized} in {date_range}",
            lambda p: p.get_price_history(normalized, date_range),
            is_complete=self._policy.is_complete,
        )

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

    def _first_answer(
        self,
        what: str,
        call: Callable[[DataProvider], T],
        is_complete: Callable[[T], bool] | None = None,
    ) -> T:
        attempts: list[ProviderAttempt] = []
        candidate: T | None = None

        for provider in self._providers:
            if not provider.is_available():
                attempts.append(
                    ProviderAttempt(provider.name, AttemptOutcome.UNAVAILABLE, provider.unavailable_reason())
                )
                logger.debug("Skipping %s for %s: %s", provider.name, what, provider.unavailable_reason())
                continue

            started = time.perf_counter()
            outcome, detail, result = self._call(provider, call)
            elapsed = round((time.perf_counter() - started) * 1000, 1)

            if outcome is AttemptOutcome.SUCCESS and is_complete is not None and not is_complete(result):  # type: ignore[arg-type]
                outcome, detail = AttemptOutcome.INCOMPLETE, "data older than freshness limit"
                if candidate is None:
                    candidate = result
            attempts.append(ProviderAttempt(provider.name, outcome, detail, elapsed))

            if outcome is AttemptOutcome.SUCCESS:
                logger.info("%s served by %s (%.1f ms)", what, provider.name, elapsed)
                self._log_fallbacks(what, attempts)
                return result  # type: ignore[return-value]
            log = logger.warning if outcome in (AttemptOutcome.UNAVAILABLE, AttemptOutcome.ERROR) else logger.info
            log("%s: provider %s -> %s %s", what, provider.name, outcome.value, detail)

        if candidate is not None:
            logger.info("%s: returning best available (incomplete) data; attempts: %s",
                        what, "; ".join(map(str, attempts)))
            return candidate

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
