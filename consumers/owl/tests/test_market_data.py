"""RemoteAggregatorSource against the real aggregator HTTP API (finance_aggregator.server)."""

from __future__ import annotations

import socket
from datetime import date
from pathlib import Path
from typing import Iterator

import pytest

from fakes import TODAY, FakeMarket, daily_bars
from finance_aggregator.core import (
    DataNotFoundError,
    DataProvider,
    DateRange,
    HealthState,
    InvalidRequestError,
    PriceHistory,
    ProviderUnavailableError,
    SymbolMatch,
    TickerInfo,
)
from finance_aggregator.server import ApiServer
from finance_aggregator.services import AggregatorService
from owl.config import OwlConfig
from owl.market_data import RemoteAggregatorSource, create_market_data
from finance_aggregator.core.errors import ConfigurationError


class _FakeProvider(DataProvider):
    kind = "fake"
    supports_search = True

    def __init__(self, name: str, market: FakeMarket) -> None:
        super().__init__(name)
        self._market = market

    def _search(self, query: str, limit: int) -> list[SymbolMatch]:
        return self._market.search_symbols(query, limit)

    def _fetch_ticker_info(self, symbol: str) -> TickerInfo:
        return self._market.get_ticker_info(symbol)

    def _fetch_price_history(self, symbol: str, date_range: DateRange) -> PriceHistory:
        return self._market.get_price_history(symbol, date_range)

    def _probe(self):  # type: ignore[no-untyped-def]
        return "ok", {}


@pytest.fixture(scope="module")
def aggregator_url() -> Iterator[str]:
    stale = FakeMarket({"AAPL": daily_bars(date(2026, 9, 1), 20, source="")})
    fresh = FakeMarket({"AAPL": daily_bars(date(2026, 9, 1), 50, source="")})
    aggregator = AggregatorService([_FakeProvider("db", stale), _FakeProvider("yahoo", fresh)])
    with ApiServer(aggregator, aggregator, host="127.0.0.1", port=0) as server:
        yield server.url


def test_ticker_and_history_round_trip_keep_bar_sources(aggregator_url: str) -> None:
    source = RemoteAggregatorSource(aggregator_url, timeout_seconds=5)
    assert source.get_ticker_info("aapl").long_name == "AAPL Corp"
    history = source.get_price_history("AAPL", DateRange(date(2026, 9, 1), TODAY))
    assert history.source == "db+yahoo"
    assert history.source_counts == {"db": 20, "yahoo": 30}


def test_errors_are_translated(aggregator_url: str) -> None:
    source = RemoteAggregatorSource(aggregator_url, timeout_seconds=5)
    with pytest.raises(DataNotFoundError):
        source.get_ticker_info("MSFT")
    with pytest.raises(InvalidRequestError):
        source.get_ticker_info("../x")


def test_status(aggregator_url: str) -> None:
    status = RemoteAggregatorSource(aggregator_url).check_status()
    assert status.state is HealthState.AVAILABLE
    assert [p.name for p in status.providers] == ["db", "yahoo"]


def test_remote_search(aggregator_url: str) -> None:
    matches = RemoteAggregatorSource(aggregator_url).search_symbols("aapl corp", 5)
    assert [m.symbol for m in matches] == ["AAPL"]
    assert matches[0].source == "db+yahoo" and matches[0].name == "AAPL Corp"
    with pytest.raises(InvalidRequestError):
        RemoteAggregatorSource(aggregator_url).search_symbols("<bad>")


def test_unreachable_aggregator() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # Windows reports a refused localhost connect only after ~2 s of SYN retries.
    source = RemoteAggregatorSource(f"http://127.0.0.1:{port}", timeout_seconds=10)
    with pytest.raises(ProviderUnavailableError, match="connection refused"):
        source.get_ticker_info("AAPL")
    assert source.check_status().state is HealthState.UNAVAILABLE


def test_market_data_is_the_aggregator_service(tmp_path: Path) -> None:
    config = OwlConfig.from_dict({"aggregator": {"url": "http://aggregator.lan:5100/", "api_key": "k"}}, base_dir=tmp_path)
    market = create_market_data(config.aggregator)
    assert isinstance(market, RemoteAggregatorSource)
    assert market.name == "aggregator@http://aggregator.lan:5100"


def test_obsolete_embedded_settings_are_ignored(tmp_path: Path) -> None:
    config = OwlConfig.from_dict(
        {"aggregator": {"mode": "embedded", "config_file": "aggregator.json"}, "splits": {"enabled": True}}, base_dir=tmp_path)
    assert config.aggregator.url == "http://127.0.0.1:5100"


@pytest.mark.parametrize(
    "raw",
    [{"aggregator": {"url": "ftp://x"}}, {"aggregator": {"url": "aggregator:5100"}},
     {"aggregator": {"timeout_seconds": "slow"}}, {"server": {"port": 99999}}, {"server": {"cors_origins": ["*"]}}],
)
def test_invalid_config(raw: dict, tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        OwlConfig.from_dict(raw, base_dir=tmp_path)
