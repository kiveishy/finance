from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from finance_aggregator.core import (
    DataNotFoundError,
    DateRange,
    HealthState,
    ProviderError,
    ProviderUnavailableError,
)
from finance_aggregator.providers.local_db import YahooDbLocalProvider, sqlite_readonly_uri
from finance_aggregator.providers.registry import ProviderContext


def test_reads_ticker_info_from_yahoo_db_schema(yahoo_db_file: Path) -> None:
    info = YahooDbLocalProvider(yahoo_db_file, name="local").get_ticker_info("aapl")
    assert info.symbol == "AAPL"
    assert info.long_name == "Apple Inc."
    assert info.market_cap == 3.0e12
    assert info.employees == 164000
    assert info.source == "local"


def test_reads_history_in_range(yahoo_db_file: Path) -> None:
    history = YahooDbLocalProvider(yahoo_db_file).get_price_history(
        "AAPL", DateRange(date(2026, 1, 3), date(2026, 1, 6))
    )
    assert [b.date for b in history.bars] == [date(2026, 1, 5), date(2026, 1, 6)]
    assert history.bars[0].close == 189.5
    assert history.bars[0].volume == 52341200


def test_unknown_symbol_is_not_found(yahoo_db_file: Path) -> None:
    provider = YahooDbLocalProvider(yahoo_db_file)
    with pytest.raises(DataNotFoundError):
        provider.get_ticker_info("MSFT")
    with pytest.raises(DataNotFoundError):
        provider.get_price_history("MSFT", DateRange(date(2026, 1, 1), date(2026, 1, 31)))


def test_missing_file_is_unavailable_not_not_found(tmp_path: Path) -> None:
    provider = YahooDbLocalProvider(tmp_path / "missing.db")
    assert provider.is_available() is False
    with pytest.raises(ProviderUnavailableError):
        provider.get_ticker_info("AAPL")
    assert not (tmp_path / "missing.db").exists(), "read-only provider must never create the file"
    assert provider.check_status().state is HealthState.UNAVAILABLE


def test_non_yahoo_db_file_is_provider_error(tmp_path: Path) -> None:
    db = tmp_path / "other.db"
    sqlite3.connect(db).close()
    with pytest.raises(ProviderError) as caught:
        YahooDbLocalProvider(db).get_ticker_info("AAPL")
    assert not isinstance(caught.value, (ProviderUnavailableError, DataNotFoundError))


def test_connection_is_read_only(yahoo_db_file: Path) -> None:
    conn = sqlite3.connect(sqlite_readonly_uri(yahoo_db_file), uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM tickers")
    finally:
        conn.close()


def test_status_reports_counts(yahoo_db_file: Path) -> None:
    status = YahooDbLocalProvider(yahoo_db_file, name="local").check_status()
    assert status.state is HealthState.AVAILABLE
    assert status.details["ticker_count"] == 1
    assert status.details["latest_price_date"] == "2026-01-06"


def test_readonly_uri_formats() -> None:
    assert sqlite_readonly_uri(Path("/tmp/a b/x.db")).endswith("a%20b/x.db?mode=ro")
    assert sqlite_readonly_uri(Path("/tmp/x.db")).startswith("file:///")


def test_from_config_resolves_relative_path(tmp_path: Path) -> None:
    provider = YahooDbLocalProvider.from_config(
        "local", {"db_path": "../data/stocks.db"}, ProviderContext(base_dir=tmp_path / "app")
    )
    assert provider.db_path == (tmp_path / "data" / "stocks.db").resolve()
