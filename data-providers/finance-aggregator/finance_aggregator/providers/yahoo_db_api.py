"""yahoo-db REST API provider (second priority).

Reuses the official client shipped with yahoo-db (`yahoodb-api.py`), loaded from the yahoo-db
sources, and only adapts its results/errors to the common provider contract.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Mapping, Protocol

from ..core.errors import DataNotFoundError, FinanceError, ProviderError, ProviderUnavailableError
from ..core.interfaces import DataProvider
from ..core.models import DateRange, PriceBar, PriceHistory, TickerInfo
from ..core.search import SymbolMatch, relevance
from .registry import ProviderContext, option, register_provider
from .yahoo_db_bridge import YahooDbModuleLoader, default_search_dirs

logger = logging.getLogger(__name__)


class YahooDbClient(Protocol):
    """Subset of yahoo-db's `YahooDB` client used by this provider."""

    base_url: str

    def get_status(self) -> dict[str, Any]: ...

    def get_ticker_info(self, symbol: str) -> dict[str, Any]: ...

    def get_ticker_history(
        self, symbol: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[dict[str, Any]]: ...

    def get_all_tickers(self) -> list[str]: ...


_UNAVAILABLE_HTTP = {408, 429, 502, 503, 504}
#: How long to remember that the yahoo-db server predates /api/search, and to cache its ticker list.
SEARCH_FALLBACK_TTL_SECONDS = 600.0
SYMBOL_INDEX_TTL_SECONDS = 3600.0


@register_provider
class YahooDbApiProvider(DataProvider):
    kind = "yahoo_db_api"
    supports_search = True

    def __init__(
        self,
        client: YahooDbClient,
        *,
        client_error: type[Exception] = Exception,
        name: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(name)
        self._client = client
        self._client_error = client_error
        self._clock = clock
        self._lock = threading.Lock()
        self._search_missing_until = 0.0
        self._symbol_index: tuple[float, tuple[str, ...]] | None = None

    @classmethod
    def from_config(
        cls, name: str, options: Mapping[str, Any], context: ProviderContext
    ) -> "YahooDbApiProvider":
        source_dir = options.get("client_source_dir")
        loader = YahooDbModuleLoader(
            default_search_dirs(
                context.resolve_path(source_dir) if source_dir else None, context.bundle_dir
            )
        )
        module = loader.load_api_client_module()
        client = module.YahooDB(
            host=option(options, "host", "localhost", str),
            port=option(options, "port", 5000, int),
            timeout=option(options, "timeout_seconds", 10.0, float),
        )
        return cls(client, client_error=module.YahooDBError, name=name)

    @property
    def base_url(self) -> str:
        return getattr(self._client, "base_url", "?")

    # ---- hooks -------------------------------------------------------------------------------

    def _fetch_ticker_info(self, symbol: str) -> TickerInfo:
        try:
            data = self._client.get_ticker_info(symbol)
        except self._client_error as exc:
            raise self._translate(exc, f"ticker {symbol}") from exc
        if not isinstance(data, Mapping) or not data:
            raise ProviderError(f"Unexpected ticker payload from {self.base_url}", provider=self.name)
        payload = dict(data)
        payload["symbol"] = payload.get("symbol") or symbol
        return TickerInfo.from_mapping(payload)

    def _fetch_price_history(self, symbol: str, date_range: DateRange) -> PriceHistory:
        try:
            records = self._client.get_ticker_history(
                symbol, start_date=date_range.start.isoformat(), end_date=date_range.end.isoformat()
            )
        except self._client_error as exc:
            raise self._translate(exc, f"history of {symbol}") from exc
        return PriceHistory(symbol, date_range, tuple(PriceBar.from_mapping(r) for r in records or ()))

    def _search(self, query: str, limit: int) -> list[SymbolMatch]:
        search = getattr(self._client, "search_tickers", None)
        if search is not None and self._clock() >= self._search_missing_until:
            try:
                rows = search(query, limit=limit)
            except self._client_error as exc:
                if getattr(exc, "status_code", None) not in (404, 405):
                    raise self._translate(exc, f"search {query!r}") from exc
                logger.info("%s has no /api/search (older yahoo-db); using symbol-only search", self.base_url)
                self._search_missing_until = self._clock() + SEARCH_FALLBACK_TTL_SECONDS
            else:
                return [
                    SymbolMatch(
                        symbol=str(r["symbol"]),
                        name=r.get("long_name") or r.get("short_name"),
                        exchange=r.get("exchange"),
                        quote_type=r.get("quote_type"),
                        score=float(r.get("score") or 0.0),
                    )
                    for r in rows
                ]
        candidates = [(relevance(query, s, None), s) for s in self._symbols()]
        ranked = sorted((c for c in candidates if c[0] > 0), key=lambda c: (-c[0], len(c[1]), c[1]))
        return [SymbolMatch(symbol, score=score) for score, symbol in ranked[:limit]]

    def _symbols(self) -> tuple[str, ...]:
        with self._lock:
            cached = self._symbol_index
            if cached and self._clock() - cached[0] < SYMBOL_INDEX_TTL_SECONDS:
                return cached[1]
            try:
                symbols = tuple(str(s).upper() for s in self._client.get_all_tickers())
            except self._client_error as exc:
                raise self._translate(exc, "ticker list") from exc
            self._symbol_index = (self._clock(), symbols)
            return symbols

    def _probe(self) -> tuple[str, Mapping[str, Any]]:
        try:
            status = self._client.get_status()
        except self._client_error as exc:
            raise self._translate(exc, "status") from exc
        details = {
            "url": self.base_url,
            "ticker_count": status.get("ticker_count"),
            "last_sync_time": status.get("last_sync_time"),
            "sync_status": status.get("sync_status"),
        }
        detail = (
            f"{self.base_url} - {details['ticker_count']} tickers, "
            f"last sync {details['last_sync_time']} ({details['sync_status']})"
        )
        return detail, details

    def _translate(self, exc: Exception, what: str) -> FinanceError:
        status_code = getattr(exc, "status_code", None)
        if status_code == 404:
            return DataNotFoundError(f"{what} not found in yahoo-db API", provider=self.name)
        if status_code is None or status_code in _UNAVAILABLE_HTTP:
            return ProviderUnavailableError(f"yahoo-db API {self.base_url} unreachable: {exc}", provider=self.name)
        return ProviderError(f"yahoo-db API error {status_code} for {what}: {exc}", provider=self.name)
