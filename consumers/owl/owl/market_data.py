"""Owl's link to its data provider: the finance aggregator, embedded in-process or over HTTP.

Both variants implement the aggregator's consumer contract (`StockDataSource` + `FinanceService`),
so the rest of owl does not know which one it is talking to.
"""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from typing import Any, Mapping, Protocol
from urllib.parse import quote, urlencode

from finance_aggregator.config import AppConfig
from finance_aggregator.core import (
    DataNotFoundError,
    DateRange,
    FinanceService,
    HealthState,
    InvalidRequestError,
    PriceBar,
    PriceHistory,
    ProviderError,
    ProviderStatus,
    ProviderUnavailableError,
    ServiceStatus,
    StockDataSource,
    SymbolMatch,
    TickerInfo,
    normalize_limit,
    normalize_query,
    normalize_symbol,
)

from .config import AggregatorLink

logger = logging.getLogger(__name__)


class MarketData(Protocol):
    def get_ticker_info(self, symbol: str) -> TickerInfo: ...

    def get_price_history(self, symbol: str, date_range: DateRange) -> PriceHistory: ...

    def search_symbols(self, query: str, limit: int = 10) -> list[SymbolMatch]: ...

    def check_status(self) -> ServiceStatus: ...

    def close(self) -> None: ...


class RemoteAggregatorSource(FinanceService, StockDataSource):
    """Client for the aggregator's `serve` HTTP API."""

    def __init__(self, base_url: str, *, api_key: str | None = None, timeout_seconds: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds

    @property
    def name(self) -> str:
        return f"aggregator@{self._base_url}"

    def get_ticker_info(self, symbol: str) -> TickerInfo:
        normalized = normalize_symbol(symbol)
        body = self._get(f"/api/ticker/{quote(normalized, safe='')}")
        data = dict(body.get("data") or {})
        return TickerInfo.from_mapping(data, source=str(data.get("source") or body.get("source") or ""))

    def get_price_history(self, symbol: str, date_range: DateRange) -> PriceHistory:
        normalized = normalize_symbol(symbol)
        query = urlencode({"start_date": date_range.start.isoformat(), "end_date": date_range.end.isoformat()})
        body = self._get(f"/api/ticker/{quote(normalized, safe='')}/history?{query}")
        bars = tuple(PriceBar.from_mapping(row) for row in body.get("data") or ())
        return PriceHistory(normalized, date_range, bars, str(body.get("source") or ""))

    def search_symbols(self, query: str, limit: int = 10) -> list[SymbolMatch]:
        params = urlencode({"q": normalize_query(query), "limit": normalize_limit(limit)})
        body = self._get(f"/api/search?{params}")
        return [SymbolMatch.from_mapping(row) for row in body.get("data") or ()]

    def check_status(self) -> ServiceStatus:
        try:
            data = self._get("/api/status").get("data") or {}
        except (ProviderError, DataNotFoundError) as exc:
            return ServiceStatus(self.name, HealthState.UNAVAILABLE, (), str(exc))
        providers = tuple(
            ProviderStatus(
                name=str(p.get("name", "?")),
                kind=str(p.get("kind", "?")),
                state=_state(p.get("state")),
                detail=str(p.get("detail", "")),
                latency_ms=p.get("latency_ms"),
                details=p.get("details") or {},
            )
            for p in data.get("providers") or ()
        )
        return ServiceStatus(self.name, _state(data.get("state")), providers, str(data.get("detail", "")))

    def _get(self, path: str) -> Mapping[str, Any]:
        request = urllib.request.Request(self._base_url + path, headers={"Accept": "application/json"})
        if self._api_key:
            request.add_header("X-API-Key", self._api_key)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise _translate_http(exc.code, _error_message(exc), self._base_url) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ProviderUnavailableError(
                f"aggregator {self._base_url} unreachable ({_network_reason(exc)})"
            ) from exc
        except ValueError as exc:
            raise ProviderError(f"invalid JSON from aggregator {self._base_url}: {exc}") from exc


def _state(value: Any) -> HealthState:
    try:
        return HealthState(str(value))
    except ValueError:
        return HealthState.UNAVAILABLE


def _network_reason(exc: BaseException) -> str:
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ConnectionRefusedError):
        return "connection refused - is the aggregator running?"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "timed out"
    if isinstance(reason, socket.gaierror):
        return "host name not found"
    if isinstance(reason, ConnectionResetError):
        return "connection reset"
    return str(reason)


def _error_message(exc: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(exc.read().decode("utf-8")).get("error") or exc.reason)
    except (ValueError, OSError, AttributeError):
        return str(exc.reason)


def _translate_http(status: int, message: str, base_url: str) -> Exception:
    if status == 404:
        return DataNotFoundError(message)
    if status == 400:
        return InvalidRequestError(message)
    if status in (502, 503, 504):
        return ProviderUnavailableError(f"aggregator: {message}")
    return ProviderError(f"aggregator {base_url} returned HTTP {status}: {message}")


def create_market_data(link: AggregatorLink) -> MarketData:
    if link.mode == "remote":
        logger.info("Using remote aggregator %s", link.url)
        return RemoteAggregatorSource(link.url, api_key=link.api_key, timeout_seconds=link.timeout_seconds)
    from finance_aggregator.app import create_aggregator

    assert link.config_file is not None  # guaranteed by config validation
    logger.info("Using embedded aggregator configured by %s", link.config_file)
    return create_aggregator(AppConfig.load(link.config_file))
