"""Exercises the real yahoo-db client (`yahoodb-api.py`) against a stub of the yahoo-db REST API."""

from __future__ import annotations

import json
import socket
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs, urlsplit

import pytest

from finance_aggregator.core import (
    DataNotFoundError,
    DateRange,
    HealthState,
    ProviderError,
    ProviderUnavailableError,
)
from finance_aggregator.providers.registry import ProviderContext
from finance_aggregator.providers.yahoo_db_api import YahooDbApiProvider

AAPL = {
    "symbol": "AAPL", "short_name": "Apple Inc.", "long_name": "Apple Inc.", "sector": "Technology",
    "market_cap": 3.0e12, "currency": "USD", "exchange": "NMS", "quote_type": "EQUITY",
    "created_at": "2026-01-30 12:31:09", "updated_at": "2026-10-29 09:00:00",
}
SEARCH_ROWS = [
    {"symbol": "AAPL", "short_name": "Apple Inc.", "long_name": "Apple Inc.", "exchange": "NMS", "quote_type": "EQUITY", "score": 75},
    {"symbol": "APLE", "short_name": "Apple Hospitality", "long_name": "Apple Hospitality REIT, Inc.", "exchange": "NYQ", "quote_type": "EQUITY", "score": 75},
]


class _StubYahooDb(BaseHTTPRequestHandler):
    requests_seen: list[str] = []
    search_enabled = True  # False mimics a yahoo-db server that predates /api/search

    def do_GET(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        _StubYahooDb.requests_seen.append(self.path)
        query = parse_qs(url.query)
        if url.path == "/api/search":
            if not _StubYahooDb.search_enabled:
                self._send_html(404, "<!doctype html><title>404 Not Found</title>")
                return
            q = query["q"][0].lower()
            rows = [r for r in SEARCH_ROWS if q in r["symbol"].lower() or q in (r["long_name"] or "").lower()]
            self._send(200, {"status": "success", "query": q, "count": len(rows), "data": rows})
            return
        if url.path == "/api/tickers":
            self._send(200, {"status": "success", "count": 4, "tickers": ["AAPL", "AAPB", "MSFT", "XAAP"]})
            return
        if url.path == "/status":
            self._send(200, {"sync_running": False, "sync_status": "completed", "ticker_count": 11884,
                             "last_sync_time": "2026-10-29T09:22:04", "progress": {}})
        elif url.path == "/api/ticker/AAPL":
            self._send(200, {"status": "success", "data": AAPL})
        elif url.path == "/api/ticker/AAPL/history":
            data = [
                {"date": "2026-01-02", "open": 1, "close": 2, "high": 3, "low": 0.5, "volume": 10, "adj_close": 2},
                {"date": "2026-01-05", "open": 2, "close": 3, "high": 4, "low": 1.5, "volume": 20, "adj_close": 3},
            ]
            start, end = query["start_date"][0], query["end_date"][0]
            self._send(200, {"status": "success", "data": [d for d in data if start <= d["date"] <= end]})
        elif url.path.startswith("/api/ticker/BOOM"):
            self._send(500, {"status": "error", "error": "database exploded"})
        elif url.path.startswith("/api/ticker/BUSY"):
            self._send(503, {"status": "error", "error": "maintenance"})
        else:
            self._send(404, {"status": "error", "error": "Ticker not found"})

    def _send(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, code: int, html: str) -> None:
        payload = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def stub_server() -> Iterator[tuple[str, int]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubYahooDb)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "127.0.0.1", server.server_address[1]
    server.shutdown()
    server.server_close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_provider(yahoo_db_dir: Path, host: str, port: int, tmp_path: Path) -> YahooDbApiProvider:
    return YahooDbApiProvider.from_config(
        "api",
        {"host": host, "port": port, "timeout_seconds": 3, "client_source_dir": str(yahoo_db_dir)},
        ProviderContext(base_dir=tmp_path),
    )


@pytest.fixture
def provider(yahoo_db_dir: Path, stub_server: tuple[str, int], tmp_path: Path) -> YahooDbApiProvider:
    return make_provider(yahoo_db_dir, *stub_server, tmp_path)


RANGE = DateRange(date(2026, 1, 1), date(2026, 1, 31))


def test_ticker_info(provider: YahooDbApiProvider) -> None:
    info = provider.get_ticker_info("aapl")
    assert (info.symbol, info.long_name, info.source) == ("AAPL", "Apple Inc.", "api")


def test_history_passes_explicit_range(provider: YahooDbApiProvider) -> None:
    history = provider.get_price_history("AAPL", DateRange(date(2026, 1, 3), date(2026, 1, 31)))
    assert [b.date for b in history.bars] == [date(2026, 1, 5)]
    assert any("start_date=2026-01-03" in r and "end_date=2026-01-31" in r for r in _StubYahooDb.requests_seen)


def test_404_is_not_found(provider: YahooDbApiProvider) -> None:
    with pytest.raises(DataNotFoundError):
        provider.get_ticker_info("MSFT")
    with pytest.raises(DataNotFoundError):
        provider.get_price_history("MSFT", RANGE)


def test_empty_history_is_not_found(provider: YahooDbApiProvider) -> None:
    with pytest.raises(DataNotFoundError):
        provider.get_price_history("AAPL", DateRange(date(2026, 3, 1), date(2026, 3, 31)))


def test_500_is_provider_error_but_not_unavailable(provider: YahooDbApiProvider) -> None:
    with pytest.raises(ProviderError) as caught:
        provider.get_ticker_info("BOOM")
    assert not isinstance(caught.value, ProviderUnavailableError)


def test_503_is_unavailable(provider: YahooDbApiProvider) -> None:
    with pytest.raises(ProviderUnavailableError):
        provider.get_ticker_info("BUSY")


def test_connection_refused_is_unavailable(yahoo_db_dir: Path, tmp_path: Path) -> None:
    provider = make_provider(yahoo_db_dir, "127.0.0.1", _free_port(), tmp_path)
    with pytest.raises(ProviderUnavailableError):
        provider.get_ticker_info("AAPL")
    assert provider.check_status().state is HealthState.UNAVAILABLE


def test_status(provider: YahooDbApiProvider) -> None:
    status = provider.check_status()
    assert status.state is HealthState.AVAILABLE
    assert status.details["ticker_count"] == 11884


def test_search_uses_yahoo_db_search_endpoint(provider: YahooDbApiProvider) -> None:
    _StubYahooDb.search_enabled = True
    matches = provider.search_symbols("apple", 5)
    assert [(m.symbol, m.name, m.source) for m in matches] == [
        ("AAPL", "Apple Inc.", "api"), ("APLE", "Apple Hospitality REIT, Inc.", "api"),
    ]


def test_search_falls_back_to_symbol_list_on_old_servers(provider: YahooDbApiProvider) -> None:
    _StubYahooDb.search_enabled = False
    try:
        matches = provider.search_symbols("aap", 5)
        assert [m.symbol for m in matches] == ["AAPB", "AAPL", "XAAP"]  # prefix matches before contains
        before = len([r for r in _StubYahooDb.requests_seen if r.startswith("/api/search")])
        provider.search_symbols("msft", 5)
        after = len([r for r in _StubYahooDb.requests_seen if r.startswith("/api/search")])
        assert after == before, "a missing endpoint is remembered instead of being retried every keystroke"
    finally:
        _StubYahooDb.search_enabled = True


def test_client_handles_non_json_error_pages(yahoo_db_loader, stub_server) -> None:  # type: ignore[no-untyped-def]
    module = yahoo_db_loader.load_api_client_module()
    client = module.YahooDB(host=stub_server[0], port=stub_server[1], timeout=3)
    _StubYahooDb.search_enabled = False
    try:
        with pytest.raises(module.YahooDBError) as caught:
            client.search_tickers("aap")
        assert caught.value.status_code == 404
    finally:
        _StubYahooDb.search_enabled = True


def test_missing_client_sources_raise_unavailable(tmp_path: Path) -> None:
    with pytest.raises(ProviderUnavailableError, match="yahoodb-api.py"):
        YahooDbApiProvider.from_config(
            "api", {"client_source_dir": str(tmp_path / "nowhere")}, ProviderContext(base_dir=tmp_path)
        )
