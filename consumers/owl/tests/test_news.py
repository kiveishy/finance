from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest

from fakes import TODAY, market_with
from owl.charts import ChartService
from owl.news import (
    AtomParser,
    FeedCatalog,
    FeedFetchError,
    FeedInfo,
    FeedParseError,
    FeedParser,
    Fetcher,
    NewsService,
    RssFeed,
    RssParser,
    UnknownFeedError,
    YahooFinanceFeed,
)
from owl.news.parsers import clean_text, parse_date
from owl.server import OwlApi, Request
from owl.watchlist import SqliteWatchlistRepository

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test</title>
<item><guid isPermaLink="false">ID-1</guid><title>Stocks &amp; bonds <b>rally</b></title><link>https://example.com/a</link>
  <pubDate>Thu, 19 Nov 2026 19:58:00 GMT</pubDate><description>&lt;p&gt;Big day&lt;/p&gt;</description></item>
<item><title>Oil climbs</title><link>https://example.com/b</link><pubDate>Thu, 19 Nov 2026 18:00:00 +0000</pubDate></item>
<item><title>No link</title></item>
<item><title>Bad scheme</title><link>javascript:alert(1)</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title>
<entry><title>Fed holds rates</title><link rel="alternate" href="https://example.org/fed"/>
  <updated>2026-11-19T20:30:00Z</updated><summary>Unchanged</summary></entry>
</feed>"""


def info(feed_id: str, name: str | None = None, enabled: bool = True) -> FeedInfo:
    return FeedInfo(feed_id, name or feed_id.upper(), f"https://feeds.test/{feed_id}", "https://feeds.test/",
                    enabled_by_default=enabled)


class FakeFetcher(Fetcher):
    def __init__(self, documents: dict[str, bytes | Exception]) -> None:
        self.documents = documents
        self.calls: list[str] = []

    def fetch(self, url: str) -> bytes:
        self.calls.append(url)
        document = self.documents[url]
        if isinstance(document, Exception):
            raise document
        return document


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestParsers:
    def test_rss(self) -> None:
        items = FeedParser.parse(RSS, info("a", "Alpha"))
        assert [i.title for i in items] == ["Stocks & bonds rally", "Oil climbs"]  # no link / bad scheme dropped
        first = items[0]
        assert first.link == "https://example.com/a" and first.source == "Alpha" and first.feed_id == "a"
        assert first.summary == "Big day"
        assert first.published == datetime(2026, 11, 19, 19, 58, tzinfo=timezone.utc)

    def test_atom(self) -> None:
        [item] = FeedParser.parse(ATOM, info("b"))
        assert (item.title, item.link, item.summary) == ("Fed holds rates", "https://example.org/fed", "Unchanged")
        assert item.published == datetime(2026, 11, 19, 20, 30, tzinfo=timezone.utc)

    def test_parsers_register_themselves(self) -> None:
        assert {RssParser, AtomParser} <= set(FeedParser.registry)

    def test_rejects_non_feeds(self) -> None:
        with pytest.raises(FeedParseError):
            FeedParser.parse(b"<html><body>blocked</body></html>", info("a"))
        with pytest.raises(FeedParseError):
            FeedParser.parse(b"not xml", info("a"))

    @pytest.mark.parametrize("raw, expected", [
        ("Thu, 19 Nov 2026 19:58:00 GMT", datetime(2026, 11, 19, 19, 58, tzinfo=timezone.utc)),
        ("Thu, 19 Nov 2026 14:59:28 -0500", datetime(2026, 11, 19, 19, 59, 28, tzinfo=timezone.utc)),
        ("2026-11-19 20:00:26", datetime(2026, 11, 19, 20, 0, 26, tzinfo=timezone.utc)),
        ("2026-11-19T20:30:00Z", datetime(2026, 11, 19, 20, 30, tzinfo=timezone.utc)),
        ("yesterday", None),
        ("", None),
    ])
    def test_dates(self, raw: str, expected: datetime | None) -> None:
        assert parse_date(raw) == expected

    def test_clean_text_limits_length(self) -> None:
        assert clean_text("a" * 20, 10) == "a" * 9 + "…"


class TestFeeds:
    def test_rss_feed_sorts_newest_first(self) -> None:
        feed = RssFeed(info("a"))
        items = feed.fetch(FakeFetcher({feed.info.url: RSS}))
        assert items[0].sort_key > items[1].sort_key

    def test_yahoo_feed_url(self) -> None:
        feed = YahooFinanceFeed("y", "Yahoo", ("^GSPC", "^DJI"))
        assert feed.info.url.startswith("https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC%2C%5EDJI")

    def test_default_catalog(self) -> None:
        catalog = FeedCatalog.default()
        assert len(catalog) >= 7 and "cnbc-top" in catalog and "globes" in catalog
        assert set(catalog.default_ids()) < {f.id for f in catalog}
        assert all(f.info.url.startswith("https://") and f.info.homepage.startswith("https://") for f in catalog)
        with pytest.raises(UnknownFeedError):
            catalog.get("nope")

    def test_duplicate_ids_rejected(self) -> None:
        with pytest.raises(ValueError):
            FeedCatalog([RssFeed(info("a")), RssFeed(info("a"))])


class TestNewsService:
    @pytest.fixture
    def setup(self) -> tuple[NewsService, FakeFetcher, Clock]:
        a, b, c = RssFeed(info("a")), RssFeed(info("b")), RssFeed(info("c", enabled=False))
        rss_b = RSS.replace(b"https://example.com/a", b"https://other.com/a")  # same story, other site
        fetcher = FakeFetcher({a.info.url: RSS, b.info.url: rss_b + b"", c.info.url: ATOM})
        clock = Clock()
        service = NewsService(FeedCatalog([a, b, c]), fetcher, ttl_seconds=120, retry_seconds=30, clock=clock)
        yield service, fetcher, clock
        service.close()

    def test_merges_dedupes_and_sorts(self, setup) -> None:
        service, _, _ = setup
        result = service.headlines(["a", "b", "c"])
        assert [i.title for i in result.items] == ["Fed holds rates", "Stocks & bonds rally", "Oil climbs"]
        assert [s.feed_id for s in result.statuses] == ["a", "b", "c"]
        assert all(s.ok for s in result.statuses)

    def test_default_selection_and_limit(self, setup) -> None:
        service, fetcher, _ = setup
        result = service.headlines(limit=1)
        assert len(result.items) == 1
        assert [s.feed_id for s in result.statuses] == ["a", "b"]
        assert "https://feeds.test/c" not in fetcher.calls

    def test_caches_until_ttl(self, setup) -> None:
        service, fetcher, clock = setup
        service.headlines(["a"])
        service.headlines(["a"])
        assert fetcher.calls.count("https://feeds.test/a") == 1
        clock.now += 121
        service.headlines(["a"])
        assert fetcher.calls.count("https://feeds.test/a") == 2

    def test_failure_keeps_previous_headlines_and_retries_sooner(self, setup) -> None:
        service, fetcher, clock = setup
        service.headlines(["a"])
        fetcher.documents["https://feeds.test/a"] = FeedFetchError("HTTP Error 503")
        clock.now += 121
        result = service.headlines(["a"])
        assert len(result.items) == 2
        status = result.statuses[0]
        assert not status.ok and status.error == "HTTP Error 503" and status.item_count == 2
        clock.now += 31
        fetcher.documents["https://feeds.test/a"] = RSS
        assert service.headlines(["a"]).statuses[0].ok

    def test_one_broken_feed_does_not_break_others(self, setup) -> None:
        service, fetcher, _ = setup
        fetcher.documents["https://feeds.test/b"] = b"<html/>"
        result = service.headlines(["a", "b"])
        assert len(result.items) == 2
        assert [s.ok for s in result.statuses] == [True, False]

    def test_unknown_feed(self, setup) -> None:
        service, _, _ = setup
        with pytest.raises(UnknownFeedError):
            service.headlines(["zzz"])

    def test_feeds_lists_sources_with_status(self, setup) -> None:
        service, _, _ = setup
        service.headlines(["a"])
        feeds = {f["id"]: f for f in service.feeds()}
        assert feeds["a"]["url"] == "https://feeds.test/a" and feeds["a"]["status"]["items"] == 2
        assert feeds["c"]["enabled_by_default"] is False and feeds["c"]["status"]["last_attempt"] is None


class TestNewsApi:
    @pytest.fixture
    def api(self, tmp_path: Path) -> Iterator[OwlApi]:
        feed = RssFeed(info("a"))
        news = NewsService(FeedCatalog([feed]), FakeFetcher({feed.info.url: RSS}))
        market = market_with("AAPL", days=10)
        repo = SqliteWatchlistRepository(tmp_path / "owl.db")
        yield OwlApi(market, repo, ChartService(market, today=lambda: TODAY), news=news)
        repo.close()
        news.close()

    def test_endpoints(self, api: OwlApi) -> None:
        response = api.handle(Request("GET", "/api/news", {"feeds": "a", "limit": "5"}))
        assert int(response.status) == 200
        data = response.body["data"]
        assert data["items"][0]["title"] == "Stocks & bonds rally"
        assert data["items"][0]["published"] == "2026-11-19T19:58:00Z"
        assert data["feeds"][0]["feed"] == "a"
        feeds = api.handle(Request("GET", "/api/news/feeds")).body["data"]
        assert feeds[0]["id"] == "a" and feeds[0]["status"]["ok"] is True

    def test_bad_requests(self, api: OwlApi) -> None:
        assert int(api.handle(Request("GET", "/api/news", {"feeds": "zzz"})).status) == 400
        assert int(api.handle(Request("GET", "/api/news", {"limit": "x"})).status) == 400
        assert int(api.handle(Request("GET", "/api/news", {"feeds": ""})).status) == 200  # nothing selected
