"""Yahoo Finance provider (last-resort fallback), backed by the `yfinance` library.

yahoo-db's `YahooFinanceClient` is not wrapped because it converts every failure into
None/[] after sleeping through its retries, so "unknown symbol" and "Yahoo unreachable" look the
same. Field mapping and price semantics (auto-adjusted close, adj_close == close) intentionally
mirror yahoo-db so consumers receive identical data whichever backend answered.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping

from ..core.errors import DataNotFoundError, FinanceError, ProviderError, ProviderUnavailableError
from ..core.interfaces import DataProvider
from ..core.models import DateRange, PriceBar, PriceHistory, TickerInfo
from .registry import ProviderContext, option, register_provider

logger = logging.getLogger(__name__)

# yfinance `info` key -> TickerInfo field (same mapping as yahoo-db YahooFinanceClient.get_ticker_info)
_INFO_FIELDS = {
    "short_name": "shortName",
    "long_name": "longName",
    "sector": "sector",
    "industry": "industry",
    "market_cap": "marketCap",
    "currency": "currency",
    "exchange": "exchange",
    "quote_type": "quoteType",
    "country": "country",
    "website": "website",
    "description": "longBusinessSummary",
    "employees": "fullTimeEmployees",
}
_HTTP_STATUS_RE = re.compile(r"HTTP Error (\d{3})")
_UNAVAILABLE_HTTP = {401, 403, 408, 429, 500, 502, 503, 504}

TickerFactory = Callable[[str], Any]


def _http_status(exc: BaseException) -> int | None:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status
    match = _HTTP_STATUS_RE.search(str(exc))
    return int(match.group(1)) if match else None


@register_provider
class YahooFinanceProvider(DataProvider):
    kind = "yahoo_finance"

    _configure_lock = threading.Lock()
    _configured = False

    def __init__(
        self,
        *,
        name: str | None = None,
        timeout_seconds: float = 15.0,
        cache_dir: Path | None = None,
        proxy: str | None = None,
        probe_symbol: str = "SPY",
        ticker_factory: TickerFactory | None = None,
    ) -> None:
        super().__init__(name)
        self._timeout = timeout_seconds
        self._cache_dir = cache_dir
        self._proxy = proxy
        self._probe_symbol = probe_symbol
        self._ticker_factory = ticker_factory

    @classmethod
    def from_config(
        cls, name: str, options: Mapping[str, Any], context: ProviderContext
    ) -> "YahooFinanceProvider":
        cache_dir = options.get("cache_dir")
        return cls(
            name=name,
            timeout_seconds=option(options, "timeout_seconds", 15.0, float),
            cache_dir=context.resolve_path(cache_dir) if cache_dir else None,
            proxy=option(options, "proxy", "", str) or None,
            probe_symbol=option(options, "probe_symbol", "SPY", str),
        )

    # ---- yfinance plumbing -------------------------------------------------------------------

    def _yf(self) -> ModuleType:
        import yfinance  # deferred: heavy import (pandas), only needed when Yahoo is actually used

        cls = type(self)
        with cls._configure_lock:
            if not cls._configured:
                # Make yfinance raise instead of logging and returning empty results.
                yfinance.config.debug.hide_exceptions = False
                if self._proxy:
                    yfinance.config.network.proxy = {"http": self._proxy, "https": self._proxy}
                if self._cache_dir is not None:
                    try:
                        self._cache_dir.mkdir(parents=True, exist_ok=True)
                        yfinance.set_tz_cache_location(str(self._cache_dir))
                    except OSError as exc:
                        logger.warning("Cannot use yfinance cache dir %s: %s", self._cache_dir, exc)
                cls._configured = True
        return yfinance

    def _ticker(self, symbol: str) -> Any:
        yf = self._yf()
        return self._ticker_factory(symbol) if self._ticker_factory else yf.Ticker(symbol)

    # ---- hooks -------------------------------------------------------------------------------

    def _fetch_ticker_info(self, symbol: str) -> TickerInfo:
        try:
            info = self._ticker(symbol).info
        except Exception as exc:
            raise self._translate(exc, f"ticker {symbol}") from exc
        if not info or not (info.get("quoteType") or info.get("symbol")):
            raise DataNotFoundError(f"Ticker {symbol} not found on Yahoo Finance", provider=self.name)
        mapped: dict[str, Any] = {field: info.get(key) for field, key in _INFO_FIELDS.items()}
        mapped["symbol"] = symbol
        return TickerInfo.from_mapping(mapped)

    def _fetch_price_history(self, symbol: str, date_range: DateRange) -> PriceHistory:
        try:
            frame = self._ticker(symbol).history(
                start=date_range.start.isoformat(),
                # yfinance treats `end` as exclusive
                end=(date_range.end + timedelta(days=1)).isoformat(),
                interval="1d",
                timeout=self._timeout,
            )
        except Exception as exc:
            raise self._translate(exc, f"history of {symbol}") from exc
        bars = []
        if frame is not None and not frame.empty:
            for timestamp, row in frame.iterrows():
                bars.append(
                    PriceBar.from_mapping(
                        {
                            "date": timestamp,
                            "open": row.get("Open"),
                            "high": row.get("High"),
                            "low": row.get("Low"),
                            "close": row.get("Close"),
                            "adj_close": row.get("Close"),
                            "volume": row.get("Volume"),
                        }
                    )
                )
        bars = [b for b in bars if date_range.start <= b.date <= date_range.end]
        return PriceHistory(symbol, date_range, tuple(bars))

    def _probe(self) -> tuple[str, Mapping[str, Any]]:
        history = self.get_price_history(self._probe_symbol, DateRange.last_days(10))
        return (
            f"reachable ({self._probe_symbol} last price date {history.last_date})",
            {"probe_symbol": self._probe_symbol, "last_price_date": str(history.last_date)},
        )

    def _translate(self, exc: Exception, what: str) -> FinanceError:
        if isinstance(exc, FinanceError):
            return exc
        errors = self._yf().exceptions
        if isinstance(exc, errors.YFTickerMissingError):
            return DataNotFoundError(f"{what} not found on Yahoo Finance: {exc}", provider=self.name)
        if isinstance(exc, errors.YFRateLimitError):
            return ProviderUnavailableError(f"Yahoo Finance rate limit hit: {exc}", provider=self.name)
        status = _http_status(exc)
        if status == 404:
            return DataNotFoundError(f"{what} not found on Yahoo Finance", provider=self.name)
        if status in _UNAVAILABLE_HTTP or isinstance(exc, (OSError, TimeoutError)):
            return ProviderUnavailableError(f"Yahoo Finance unreachable: {exc}", provider=self.name)
        logger.exception("Unexpected Yahoo Finance failure for %s", what)
        return ProviderError(f"Yahoo Finance error for {what}: {exc}", provider=self.name)
