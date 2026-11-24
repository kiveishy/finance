"""Financial news headlines from public feeds.

Layers (each replaceable on its own):
  Fetcher      downloads a document (HttpFetcher; tests use a fake)
  FeedParser   RSS / Atom document -> NewsItem list (new formats register by subclassing)
  NewsFeed     one source: its FeedInfo (where the data comes from) and how to fetch it
  FeedCatalog  the built-in feeds, addressable by id
  NewsService  parallel fetching, per-feed caching and status, merging and de-duplication
"""

from .catalog import FeedCatalog, UnknownFeedError
from .feeds import NewsFeed, RssFeed, YahooFinanceFeed
from .fetcher import FeedFetchError, Fetcher, HttpFetcher
from .models import FeedInfo, FeedStatus, NewsItem, NewsResult
from .parsers import AtomParser, FeedParseError, FeedParser, RssParser
from .service import DEFAULT_LIMIT, MAX_LIMIT, NewsService

__all__ = [
    "AtomParser",
    "DEFAULT_LIMIT",
    "FeedCatalog",
    "FeedFetchError",
    "FeedInfo",
    "FeedParseError",
    "FeedParser",
    "FeedStatus",
    "Fetcher",
    "HttpFetcher",
    "MAX_LIMIT",
    "NewsFeed",
    "NewsItem",
    "NewsResult",
    "NewsService",
    "RssFeed",
    "RssParser",
    "UnknownFeedError",
    "YahooFinanceFeed",
]
