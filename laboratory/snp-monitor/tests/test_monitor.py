import threading

import pytest

from snp_monitor.constituents import Constituent, ConstituentList
from snp_monitor.monitor import SnpMonitor, build_snapshot
from snp_monitor.quotes import Quote, YahooQuoteSource, parse_quote

MEMBERS = ConstituentList((
    Constituent("AAA", "Alpha Corp", "Tech", cik="1"),
    Constituent("GOOGL", "Alphabet Inc. (Class A)", "Comm", cik="2"),
    Constituent("GOOG", "Alphabet Inc. (Class C)", "Comm", cik="2"),
    Constituent("BRK.B", "Berkshire Hathaway", "Fin", cik="3"),
    Constituent("NOQ", "No Quote Inc", "Tech", cik="4"),
), "wikipedia", 0.0)

QUOTES = {
    "AAA": Quote("AAA", 100.0, 98.0, 2.0, 2.04, market_cap=1000.0, market_state="REGULAR", time=10),
    "GOOGL": Quote("GOOGL", 200.0, 201.0, -1.0, -0.5, market_cap=3000.0, market_state="REGULAR", time=20),
    "GOOG": Quote("GOOG", 199.0, 199.0, 0.0, 0.0, market_cap=2990.0, market_state="REGULAR", time=20),
    "BRK-B": Quote("BRK-B", 500.0, 490.0, 10.0, 2.0, market_cap=1000.0, market_state="CLOSED", time=15),
}


def test_price_share_is_price_over_sum_of_prices():
    snap = build_snapshot(MEMBERS, QUOTES, 100.0)
    stocks = {s["symbol"]: s for s in snap["stocks"]}
    assert snap["summary"]["total_price"] == pytest.approx(999.0)
    assert stocks["BRK.B"]["price_share"] == pytest.approx(500 / 999 * 100)
    assert stocks["BRK.B"]["price"] == 500.0  # S&P symbol BRK.B is quoted as BRK-B
    assert stocks["NOQ"]["price"] is None and stocks["NOQ"]["price_share"] is None
    assert sum(s["price_share"] or 0 for s in snap["stocks"]) == pytest.approx(100)


def test_company_share_counts_share_classes_once():
    snap = build_snapshot(MEMBERS, QUOTES, 100.0)
    companies = {c["name"]: c for c in snap["companies"]}
    assert set(companies) == {"Alpha Corp", "Alphabet Inc.", "Berkshire Hathaway", "No Quote Inc"}
    alphabet = companies["Alphabet Inc."]
    assert alphabet["symbols"] == ["GOOGL", "GOOG"]
    assert alphabet["market_cap"] == 3000.0  # not 3000 + 2990
    assert snap["summary"]["total_market_cap"] == 5000.0
    assert alphabet["share"] == pytest.approx(60.0)
    assert snap["companies"][0]["name"] == "Alphabet Inc."  # sorted by value
    stocks = {s["symbol"]: s for s in snap["stocks"]}
    assert stocks["GOOG"]["company_share"] == stocks["GOOGL"]["company_share"] == pytest.approx(60.0)


def test_summary_fields():
    snap = build_snapshot(MEMBERS, QUOTES, 100.0)
    assert snap["summary"]["priced"] == 4
    assert snap["summary"]["companies"] == 4
    assert snap["summary"]["advancers"] == 2 and snap["summary"]["decliners"] == 1
    assert snap["market_state"] == "REGULAR"
    assert snap["constituents"]["count"] == 5


def test_parse_quote_computes_missing_change():
    quote = parse_quote({"symbol": "x", "regularMarketPrice": 110, "regularMarketPreviousClose": 100,
                         "marketCap": 5, "regularMarketTime": 123})
    assert quote.symbol == "X" and quote.change == 10 and quote.change_percent == pytest.approx(10)
    assert parse_quote({"symbol": "Y", "regularMarketPrice": float("nan")}).price is None


def test_yahoo_source_chunks_and_tolerates_a_failed_chunk():
    calls = []

    def get_json(url, params, timeout):
        symbols = params["symbols"].split(",")
        calls.append(symbols)
        if "C" in symbols:
            raise OSError("boom")
        return {"quoteResponse": {"result": [{"symbol": s, "regularMarketPrice": 1.0} for s in symbols]}}

    quotes = YahooQuoteSource(chunk_size=2, get_json=get_json).fetch(["A", "B", "C", "D", "E"])
    assert calls == [["A", "B"], ["C", "D"], ["E"]]
    assert sorted(quotes) == ["A", "B", "E"]


def test_yahoo_source_raises_when_everything_fails():
    def get_json(url, params, timeout):
        raise OSError("offline")

    with pytest.raises(RuntimeError):
        YahooQuoteSource(get_json=get_json).fetch(["A"])


class StaticProvider:
    def get(self):
        return MEMBERS


class FakeQuotes:
    def __init__(self):
        self.fail = False
        self.calls = 0

    def fetch(self, symbols):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Yahoo down")
        return QUOTES


def test_monitor_keeps_last_snapshot_and_reports_refresh_error():
    quotes = FakeQuotes()
    monitor = SnpMonitor(StaticProvider(), quotes, refresh_seconds=60, clock=lambda: 100.0)
    monitor.refresh()
    quotes.fail = True
    with pytest.raises(RuntimeError):
        monitor.refresh()
    snap = monitor.snapshot(wait_seconds=0)
    assert snap["summary"]["priced"] == 4
    assert snap["error"] == "Yahoo down"


def test_monitor_snapshot_raises_before_first_success():
    quotes = FakeQuotes()
    quotes.fail = True
    monitor = SnpMonitor(StaticProvider(), quotes)
    with pytest.raises(RuntimeError, match="still loading"):
        monitor.snapshot(wait_seconds=0)
    with pytest.raises(RuntimeError):
        monitor.refresh()
    with pytest.raises(RuntimeError, match="Yahoo down"):
        monitor.snapshot(wait_seconds=0)


def test_monitor_background_thread_and_forced_refresh_rate_limit():
    clock = [1000.0]
    quotes = FakeQuotes()
    monitor = SnpMonitor(StaticProvider(), quotes, refresh_seconds=3600, clock=lambda: clock[0])
    monitor.start()
    try:
        assert monitor.snapshot(wait_seconds=5)["summary"]["priced"] == 4
        assert monitor.request_refresh() is False  # just refreshed
        clock[0] += 60
        done = threading.Event()
        original = quotes.fetch

        def fetch(symbols):
            result = original(symbols)
            done.set()
            return result

        quotes.fetch = fetch
        assert monitor.request_refresh() is True
        assert done.wait(5)
    finally:
        monitor.close()
