"""News feeds: a feed knows where its data lives and how to turn it into headlines."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence
from urllib.parse import urlencode

from .fetcher import Fetcher
from .models import FeedInfo, NewsItem
from .parsers import FeedParser

MAX_ITEMS_PER_FEED = 60


class NewsFeed(ABC):
    """A source of headlines. Subclass it for sources that are not plain RSS / Atom (e.g. a JSON API)."""

    def __init__(self, info: FeedInfo) -> None:
        self.info = info

    @property
    def id(self) -> str:
        return self.info.id

    @abstractmethod
    def fetch(self, fetcher: Fetcher) -> list[NewsItem]:
        """Latest headlines, newest first; raises on failure."""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.id!r})"


class RssFeed(NewsFeed):
    """An RSS 2.0 or Atom document at a fixed URL."""

    def fetch(self, fetcher: Fetcher) -> list[NewsItem]:
        items = FeedParser.parse(fetcher.fetch(self.info.url), self.info)
        items.sort(key=lambda i: i.sort_key, reverse=True)
        return items[:MAX_ITEMS_PER_FEED]


class YahooFinanceFeed(RssFeed):
    """Yahoo Finance headlines for a set of symbols; index symbols give general market news."""

    BASE_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"

    def __init__(self, feed_id: str, name: str, symbols: Sequence[str], *, description: str = "",
                 enabled_by_default: bool = True) -> None:
        self.symbols = tuple(symbols)
        url = f"{self.BASE_URL}?{urlencode({'s': ','.join(self.symbols), 'region': 'US', 'lang': 'en-US'})}"
        super().__init__(FeedInfo(
            id=feed_id, name=name, url=url, homepage="https://finance.yahoo.com/news/", language="en", region="US",
            description=description or f"Yahoo Finance headlines for {', '.join(self.symbols)}",
            enabled_by_default=enabled_by_default,
        ))
