from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from fakes import FakeProvider, unavailable
from finance_aggregator.core import (
    InvalidRequestError,
    ProviderUnavailableError,
    SymbolMatch,
    merge_matches,
    normalize_limit,
    normalize_query,
    relevance,
)
from finance_aggregator.providers.local_db import YahooDbLocalProvider
from finance_aggregator.providers.yahoo_finance import YahooFinanceProvider
from finance_aggregator.server.http_api import ApiApplication
from finance_aggregator.services import AggregatorService


class SearchingProvider(FakeProvider):
    supports_search = True

    def __init__(self, name: str, matches: Any = (), *, delay: float = 0.0, **kwargs: Any) -> None:
        super().__init__(name, **kwargs)
        self.matches = matches
        self.delay = delay
        self.queries: list[str] = []

    def _search(self, query: str, limit: int) -> list[SymbolMatch]:
        self.queries.append(query)
        if self.delay:
            time.sleep(self.delay)
        if isinstance(self.matches, BaseException):
            raise self.matches
        return list(self.matches)


class TestCore:
    @pytest.mark.parametrize("raw,expected", [("  apple   inc ", "apple inc"), ("BRK-B", "BRK-B"), ("AT&T", "AT&T")])
    def test_normalize_query(self, raw: str, expected: str) -> None:
        assert normalize_query(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "x" * 65, "drop;table", "<script>"])
    def test_rejects_bad_queries(self, raw: str) -> None:
        with pytest.raises(InvalidRequestError):
            normalize_query(raw)

    def test_limits(self) -> None:
        assert normalize_limit(None) == 10 and normalize_limit("5") == 5
        for bad in (0, 51, "x"):
            with pytest.raises(InvalidRequestError):
                normalize_limit(bad)

    def test_relevance_order(self) -> None:
        q = "app"
        scores = {
            "exact": relevance("AAPL", "AAPL", None),
            "symbol prefix": relevance(q, "APPS", None),
            "name prefix": relevance(q, "XYZ", "Applied Materials"),
            "word prefix": relevance(q, "XYZ", "Green Apple Corp"),
            "symbol contains": relevance(q, "XAPP", None),
            "name contains": relevance(q, "XYZ", "Snapple"),
            "none": relevance(q, "MSFT", "Microsoft"),
        }
        assert list(scores) == sorted(scores, key=scores.get, reverse=True)
        assert scores["none"] == 0

    def test_merge_dedupes_fills_fields_and_ranks(self) -> None:
        db = [SymbolMatch("AAPL", None, source="db"), SymbolMatch("AAPLX", None, source="db")]
        yahoo = [
            SymbolMatch("AAPL", "Apple Inc.", "NASDAQ", "EQUITY", source="yahoo"),
            SymbolMatch("SAAPL=F", "Apple futures", quote_type="FUTURE", source="yahoo"),
            SymbolMatch("AAPL.TO", "Apple Inc.", "Toronto", "EQUITY", source="yahoo"),
            SymbolMatch("FUZZY", "Something semantically close", source="yahoo"),
        ]
        merged = merge_matches("aapl", [db, yahoo], 10)
        order = [m.symbol for m in merged]
        assert order == ["AAPL", "AAPLX", "AAPL.TO", "SAAPL=F", "FUZZY"]
        top = merged[0]
        assert (top.name, top.exchange, top.source, top.score) == ("Apple Inc.", "NASDAQ", "db+yahoo", 100.0)
        assert merged[-1].score > 0, "fuzzy provider hits are kept but ranked last"

    def test_merge_limit(self) -> None:
        matches = [SymbolMatch(f"A{i}") for i in range(20)]
        assert len(merge_matches("a", [matches], 5)) == 5


class TestAggregatorSearch:
    def test_merges_providers_in_parallel(self) -> None:
        db = SearchingProvider("db", [SymbolMatch("AAPL"), SymbolMatch("AAPB")], delay=0.2)
        yahoo = SearchingProvider("yahoo", [SymbolMatch("AAPL", "Apple Inc.", "NASDAQ", "EQUITY")], delay=0.2)
        aggregator = AggregatorService([db, yahoo])
        started = time.perf_counter()
        matches = aggregator.search_symbols("  aap ", 5)
        assert time.perf_counter() - started < 0.35, "providers must be queried in parallel"
        assert db.queries == yahoo.queries == ["aap"]
        assert matches[0].symbol == "AAPL" and matches[0].name == "Apple Inc." and matches[0].source == "db+yahoo"
        aggregator.close()

    def test_failing_and_slow_providers_are_skipped(self) -> None:
        good = SearchingProvider("good", [SymbolMatch("MSFT", "Microsoft")])
        broken = SearchingProvider("broken", unavailable())
        slow = SearchingProvider("slow", [SymbolMatch("MSFX")], delay=1.0)
        aggregator = AggregatorService([broken, slow, good], search_timeout_seconds=0.3)
        assert [m.symbol for m in aggregator.search_symbols("msft")] == ["MSFT"]
        aggregator.close()

    def test_unsupported_and_unavailable_providers(self) -> None:
        plain = FakeProvider("plain")
        offline = SearchingProvider("offline", [SymbolMatch("X")], available=False)
        with pytest.raises(ProviderUnavailableError, match="supports symbol search"):
            AggregatorService([plain, offline]).search_symbols("x")
        assert offline.queries == []
        with pytest.raises(ProviderUnavailableError):
            AggregatorService([SearchingProvider("down", unavailable())]).search_symbols("x")

    def test_no_matches_is_an_empty_list_not_an_error(self) -> None:
        assert AggregatorService([SearchingProvider("db", [])]).search_symbols("zzqq") == []

    def test_invalid_query_rejected_before_providers(self) -> None:
        provider = SearchingProvider("db", [])
        with pytest.raises(InvalidRequestError):
            AggregatorService([provider]).search_symbols("<x>")
        assert provider.queries == []

    def test_http_endpoint(self) -> None:
        aggregator = AggregatorService([SearchingProvider("db", [SymbolMatch("AAPL", "Apple Inc.")])])
        app = ApiApplication(aggregator, aggregator)
        response = app.handle_get("/api/search?q=apple&limit=3", {})
        assert response.status == 200
        assert response.body["count"] == 1 and response.body["data"][0]["symbol"] == "AAPL"
        assert app.handle_get("/api/search?q=", {}).status == 400
        assert app.handle_get("/api/search?q=a&limit=99", {}).status == 400


class TestProviderSearch:
    def test_local_db_search_ranks_symbol_and_name(self, yahoo_db_file: Path) -> None:
        provider = YahooDbLocalProvider(yahoo_db_file, name="local")
        assert [m.symbol for m in provider.search_symbols("aapl")] == ["AAPL"]
        by_name = provider.search_symbols("apple")
        assert by_name[0].symbol == "AAPL" and by_name[0].name == "Apple Inc." and by_name[0].source == "local"
        assert provider.search_symbols("zzqq") == []
        assert provider.search_symbols("AAP_") == [], "LIKE wildcard '_' in the query is escaped"

    def test_yahoo_db_database_manager_search(self, yahoo_db_file: Path, yahoo_db_loader: Any) -> None:
        manager = yahoo_db_loader.load_database_module().DatabaseManager(str(yahoo_db_file))
        try:
            rows = manager.search_tickers("appl", 5)
            assert rows[0]["symbol"] == "AAPL" and rows[0]["score"] == 75
            assert manager.search_tickers("aapl", 5)[0]["score"] == 100
            assert manager.search_tickers("_", 5) == []
        finally:
            manager.shutdown()

    def test_yahoo_finance_search_mapping_and_filtering(self) -> None:
        quotes = [
            {"symbol": "AAPL", "longname": "Apple Inc.", "exchDisp": "NASDAQ", "quoteType": "EQUITY"},
            {"symbol": "0P0000 XYZ", "shortname": "invalid symbol"},
            {"symbol": "SAAPL=F", "shortname": "Apple futures", "exchange": "CME", "quoteType": "FUTURE"},
        ]
        provider = YahooFinanceProvider(name="yahoo", search_factory=lambda q, n: quotes)
        matches = provider.search_symbols("apple", 5)
        assert [(m.symbol, m.name, m.exchange) for m in matches] == [
            ("AAPL", "Apple Inc.", "NASDAQ"), ("SAAPL=F", "Apple futures", "CME"),
        ]

    def test_yahoo_finance_search_network_error_is_unavailable(self) -> None:
        def fail(query: str, limit: int) -> list:
            raise ConnectionError("offline")

        with pytest.raises(ProviderUnavailableError):
            YahooFinanceProvider(name="yahoo", search_factory=fail).search_symbols("apple")
