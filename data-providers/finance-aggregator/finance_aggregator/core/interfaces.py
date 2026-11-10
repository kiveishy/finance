"""Abstractions shared by data providers, services and consumers.

    StockDataSource   consumer-facing contract (what applications depend on)
      +-- DataProvider     one concrete backend (local DB, REST API, Yahoo Finance, ...)
      +-- AggregatorService (composite of DataProviders, see services.aggregator)
    FinanceService    any long-lived service exposing health status
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any, Callable, ClassVar, Mapping, TypeVar

from .errors import DataNotFoundError, FinanceError, ProviderError, ProviderUnavailableError
from .models import (
    DateRange,
    HealthState,
    PriceHistory,
    ProviderStatus,
    ServiceStatus,
    TickerInfo,
    normalize_symbol,
)
from .search import SymbolMatch, normalize_limit, normalize_query

logger = logging.getLogger(__name__)

T = TypeVar("T")


class StockDataSource(ABC):
    """Anything a consumer can ask for stock data.

    Implementations raise `DataNotFoundError` when the data does not exist and
    `ProviderUnavailableError`/`ProviderError` when they cannot answer.
    """

    @abstractmethod
    def get_ticker_info(self, symbol: str) -> TickerInfo: ...

    @abstractmethod
    def get_price_history(self, symbol: str, date_range: DateRange) -> PriceHistory: ...

    def search_symbols(self, query: str, limit: int = 10) -> list[SymbolMatch]:
        """Tickers whose symbol or company name matches `query`, nearest first."""
        raise ProviderUnavailableError(f"{type(self).__name__} does not support symbol search")

    def close(self) -> None:
        """Release resources (connections, sessions). Safe to call more than once."""

    def __enter__(self: T) -> T:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class DataProvider(StockDataSource):
    """Base class for a single data backend.

    Subclasses implement the `_fetch_*` and `_probe` hooks. The public template methods validate
    input, enforce the error contract and stamp results with the provider name.
    """

    #: Registry type key used in configuration (``"type": "<kind>"``).
    kind: ClassVar[str] = ""
    #: Set to True and implement `_search` to take part in symbol search.
    supports_search: ClassVar[bool] = False

    def __init__(self, name: str | None = None) -> None:
        self._name = name or self.kind or type(self).__name__

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        """Cheap pre-check (no network I/O). Return False to let callers skip this provider."""
        return True

    def unavailable_reason(self) -> str:
        return "provider reported itself unavailable"

    # ---- public template methods -------------------------------------------------------------

    def get_ticker_info(self, symbol: str) -> TickerInfo:
        normalized = normalize_symbol(symbol)
        info = self._guard("get_ticker_info", self._fetch_ticker_info, normalized)
        return info.with_source(self.name)

    def get_price_history(self, symbol: str, date_range: DateRange) -> PriceHistory:
        normalized = normalize_symbol(symbol)
        history = self._guard("get_price_history", self._fetch_price_history, normalized, date_range)
        if history.is_empty:
            raise DataNotFoundError(
                f"No price history for {normalized} in {date_range}", provider=self.name
            )
        return history.with_source(self.name)

    def search_symbols(self, query: str, limit: int = 10) -> list[SymbolMatch]:
        normalized = normalize_query(query)
        limit = normalize_limit(limit)
        if not self.supports_search:
            return super().search_symbols(normalized, limit)
        matches = self._guard("search_symbols", self._search, normalized, limit)
        return [m.with_source(self.name) for m in matches[:limit]]

    def check_status(self) -> ProviderStatus:
        if not self.is_available():
            return ProviderStatus(self.name, self.kind, HealthState.UNAVAILABLE, self.unavailable_reason())
        started = time.perf_counter()
        try:
            detail, details = self._probe()
            state = HealthState.AVAILABLE
        except FinanceError as exc:
            detail, details, state = str(exc), {}, HealthState.UNAVAILABLE
        except Exception as exc:  # noqa: BLE001 - status must never raise
            logger.exception("Status probe of provider %s failed", self.name)
            detail, details, state = f"unexpected error: {exc}", {}, HealthState.UNAVAILABLE
        latency = round((time.perf_counter() - started) * 1000, 1)
        return ProviderStatus(self.name, self.kind, state, detail, latency, details)

    # ---- hooks for subclasses ------------------------------------------------------------------

    @abstractmethod
    def _fetch_ticker_info(self, symbol: str) -> TickerInfo: ...

    @abstractmethod
    def _fetch_price_history(self, symbol: str, date_range: DateRange) -> PriceHistory: ...

    @abstractmethod
    def _probe(self) -> tuple[str, Mapping[str, Any]]:
        """Health check. Return (human readable detail, extra details) or raise a FinanceError."""

    def _search(self, query: str, limit: int) -> list[SymbolMatch]:
        """Search hook (only called when `supports_search` is True). Order: provider's best first."""
        raise NotImplementedError

    # ---- helpers -------------------------------------------------------------------------------

    def _guard(self, operation: str, func: Callable[..., T], *args: Any) -> T:
        """Let contract errors through; convert anything unexpected into ProviderError."""
        try:
            return func(*args)
        except FinanceError:
            raise
        except Exception as exc:
            logger.exception("Provider %s failed unexpectedly in %s%r", self.name, operation, args)
            raise ProviderError(
                f"{self.name}: unexpected failure in {operation}: {exc}", provider=self.name
            ) from exc

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"


class FinanceService(ABC):
    """A long-lived service (aggregation, analysis, ...) that reports its health."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def check_status(self) -> ServiceStatus: ...

    def close(self) -> None:
        """Release resources."""
