"""Crypto, currency and government bond ticker lists (run: python -m pytest tests -q from the yahoo-db folder)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yahoo_finance  # noqa: E402
from yahoo_finance import CRYPTO_FALLBACK, SYMBOL_RE, YahooFinanceClient  # noqa: E402


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self.payload


def fake_screener(pages: list, fail_at: int | None = None):
    """A YfData stand-in serving `pages` (lists of (symbol, market cap)) by the 'start' offset."""
    requests = []

    class FakeData:
        def get(self, url, params):
            requests.append(params)
            index = len(requests) - 1
            if index == fail_at:
                raise ConnectionError("network down")
            quotes = [{"symbol": s, "marketCap": cap} for s, cap in pages[index]] if index < len(pages) else []
            total = sum(len(page) for page in pages)
            return FakeResponse({"finance": {"result": [{"total": total, "quotes": quotes}]}})

    return FakeData, requests


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(yahoo_finance.time, "sleep", lambda _: None)


def test_crypto_pages_through_the_screener(monkeypatch):
    data, requests = fake_screener([[("BTC-USD", 2e12), ("ETH-USD", 5e11)], [("DOGE-USD", 3e10), ("ZERO-USD", 0)]])
    monkeypatch.setattr(yahoo_finance, "YfData", data)
    assert YahooFinanceClient().get_crypto_tickers() == ["BTC-USD", "ETH-USD", "DOGE-USD", "ZERO-USD"]
    assert [r["start"] for r in requests] == [0, 2]


def test_crypto_limits_and_filters(monkeypatch):
    data, _ = fake_screener([[("BTC-USD", 2e12), ("WEIRD COIN-USD", 1e9), ("BTC-EUR", 1e9), ("BTC-USD", 2e12)],
                             [("DOGE-USD", 3e10), ("TINY-USD", 10)]])
    monkeypatch.setattr(yahoo_finance, "YfData", data)
    client = YahooFinanceClient()
    assert client.get_crypto_tickers(min_market_cap=1e6) == ["BTC-USD", "DOGE-USD"]
    data, requests = fake_screener([[("BTC-USD", 2e12), ("ETH-USD", 5e11)], [("DOGE-USD", 3e10)]])
    monkeypatch.setattr(yahoo_finance, "YfData", data)
    assert client.get_crypto_tickers(max_count=1) == ["BTC-USD"]
    assert len(requests) == 1


def test_crypto_falls_back_when_yahoo_is_down(monkeypatch):
    data, _ = fake_screener([], fail_at=0)
    monkeypatch.setattr(yahoo_finance, "YfData", data)
    assert YahooFinanceClient().get_crypto_tickers() == list(CRYPTO_FALLBACK)


def test_crypto_keeps_pages_already_loaded(monkeypatch):
    data, _ = fake_screener([[("BTC-USD", 2e12)], [("ETH-USD", 5e11)]], fail_at=1)
    monkeypatch.setattr(yahoo_finance, "YfData", data)
    assert YahooFinanceClient().get_crypto_tickers() == ["BTC-USD"]


def test_currencies_and_bonds_are_valid_symbols():
    client = YahooFinanceClient()
    currencies = client.get_currency_tickers()
    assert "ILS=X" in currencies and "EUR=X" in currencies and "USD=X" not in currencies
    assert len(currencies) == len(set(currencies)) >= 150
    bonds = client.get_government_bond_tickers()
    assert "^TNX" in bonds
    assert all(SYMBOL_RE.match(s) for s in currencies + bonds + list(CRYPTO_FALLBACK))
