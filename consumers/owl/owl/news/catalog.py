"""The built-in list of news feeds (free public RSS feeds, no account or API key needed)."""

from __future__ import annotations

from typing import Iterable, Iterator

from .feeds import NewsFeed, RssFeed, YahooFinanceFeed
from .models import FeedInfo


class UnknownFeedError(KeyError):
    """A feed id that is not in the catalog."""


class FeedCatalog:
    """Ordered, id-addressable collection of feeds."""

    def __init__(self, feeds: Iterable[NewsFeed]) -> None:
        self._feeds: dict[str, NewsFeed] = {}
        for feed in feeds:
            if feed.id in self._feeds:
                raise ValueError(f"Duplicate feed id {feed.id!r}")
            self._feeds[feed.id] = feed

    def __iter__(self) -> Iterator[NewsFeed]:
        return iter(self._feeds.values())

    def __len__(self) -> int:
        return len(self._feeds)

    def __contains__(self, feed_id: object) -> bool:
        return feed_id in self._feeds

    def get(self, feed_id: str) -> NewsFeed:
        try:
            return self._feeds[feed_id]
        except KeyError:
            raise UnknownFeedError(feed_id) from None

    def default_ids(self) -> list[str]:
        return [f.id for f in self if f.info.enabled_by_default]

    @classmethod
    def default(cls) -> "FeedCatalog":
        def rss(feed_id: str, name: str, url: str, homepage: str, description: str, *, language: str = "en",
                region: str = "US", enabled: bool = True) -> RssFeed:
            return RssFeed(FeedInfo(feed_id, name, url, homepage, language, region, description, enabled))

        return cls([
            YahooFinanceFeed("yahoo-markets", "Yahoo Finance", ("^GSPC", "^IXIC", "^DJI"),
                             description="US market news from Yahoo Finance (S&P 500, Nasdaq and Dow Jones headlines)"),
            rss("cnbc-top", "CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
                "https://www.cnbc.com/", "CNBC top news: business and markets, updated very often"),
            rss("marketwatch-top", "MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories",
                "https://www.marketwatch.com/", "MarketWatch top stories (US markets)"),
            rss("investing", "Investing.com", "https://www.investing.com/rss/news.rss",
                "https://www.investing.com/news/", "Investing.com latest news: global markets, short wire-style items",
                region="Global"),
            rss("bloomberg-markets", "Bloomberg", "https://feeds.bloomberg.com/markets/news.rss",
                "https://www.bloomberg.com/markets", "Bloomberg markets news (articles may need a subscription)",
                region="Global"),
            rss("globes", "Globes", "https://www.globes.co.il/webservice/rss/rssfeeder.asmx/FeederNode?iID=585",
                "https://www.globes.co.il/", "Globes: Israeli business and Tel Aviv market news (Hebrew)",
                language="he", region="Israel"),
            rss("seeking-alpha", "Seeking Alpha", "https://seekingalpha.com/market_currents.xml",
                "https://seekingalpha.com/market-news", "Seeking Alpha market currents: upgrades, downgrades, earnings",
                enabled=False),
            rss("ft-markets", "Financial Times", "https://www.ft.com/markets?format=rss",
                "https://www.ft.com/markets", "Financial Times markets and macro news (articles are paywalled)",
                region="Global", enabled=False),
            rss("investing-il", "Investing.com Israel", "https://il.investing.com/rss/news.rss",
                "https://il.investing.com/news/", "Investing.com news in Hebrew", language="he", region="Israel",
                enabled=False),
        ])
