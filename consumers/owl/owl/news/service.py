"""NewsService: fetches the selected feeds in parallel, caches them briefly and merges the headlines."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Iterable

from .catalog import FeedCatalog
from .feeds import NewsFeed
from .fetcher import Fetcher, HttpFetcher
from .models import FeedStatus, NewsItem, NewsResult

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 100
MAX_LIMIT = 300


class _FeedState:
    """Cached headlines and status of one feed. The lock makes concurrent requests share one download."""

    def __init__(self, feed: NewsFeed) -> None:
        self.feed = feed
        self.lock = threading.Lock()
        self.items: list[NewsItem] = []
        self.fetched_at: float | None = None  # monotonic time of the last attempt
        self.status = FeedStatus(feed.id)

    def is_fresh(self, now: float, ttl: float, retry: float) -> bool:
        if self.fetched_at is None:
            return False
        return now - self.fetched_at < (ttl if self.status.error is None else retry)


class NewsService:
    """Latest headlines of any subset of the catalog."""

    def __init__(
        self,
        catalog: FeedCatalog | None = None,
        fetcher: Fetcher | None = None,
        *,
        ttl_seconds: float = 120.0,
        retry_seconds: float = 60.0,
        max_workers: int = 6,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.catalog = catalog or FeedCatalog.default()
        self._fetcher = fetcher or HttpFetcher()
        self._ttl = ttl_seconds
        self._retry = retry_seconds
        self._max_workers = max_workers
        self._clock = clock
        self._now = now
        self._states = {feed.id: _FeedState(feed) for feed in self.catalog}
        self._pool: ThreadPoolExecutor | None = None
        self._pool_lock = threading.Lock()

    def feeds(self) -> list[dict]:
        """Every feed in the catalog with where it comes from and how its last fetch went."""
        return [{**state.feed.info.to_dict(), "status": state.status.to_dict()} for state in self._states.values()]

    def headlines(self, feed_ids: Iterable[str] | None = None, limit: int = DEFAULT_LIMIT) -> NewsResult:
        """Newest-first headlines of the given feeds (default: the catalog's default selection)."""
        ids = list(dict.fromkeys(feed_ids)) if feed_ids is not None else self.catalog.default_ids()
        states = [self._states[self.catalog.get(i).id] for i in ids]  # get() rejects unknown ids
        limit = max(1, min(int(limit), MAX_LIMIT))
        if states:
            list(self._executor().map(self._refresh, states))
        return NewsResult(self._merge(states, limit), [s.status for s in states])

    def _refresh(self, state: _FeedState) -> None:
        with state.lock:
            if state.is_fresh(self._clock(), self._ttl, self._retry):
                return
            state.fetched_at = self._clock()
            state.status.last_attempt = self._now()
            try:
                items = state.feed.fetch(self._fetcher)
            except Exception as exc:  # noqa: BLE001 - one broken feed must not break the others
                logger.info("News feed %s failed: %s", state.feed.id, exc)
                state.status.error = str(exc) or exc.__class__.__name__
                return  # keep the previous headlines
            state.items = items
            state.status.error = None
            state.status.last_success = state.status.last_attempt
            state.status.item_count = len(items)

    @staticmethod
    def _merge(states: list[_FeedState], limit: int) -> list[NewsItem]:
        seen: set[str] = set()
        merged: list[NewsItem] = []
        for item in sorted((i for s in states for i in s.items), key=lambda i: i.sort_key, reverse=True):
            key = item.dedupe_key
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= limit:
                break
        return merged

    def _executor(self) -> ThreadPoolExecutor:
        with self._pool_lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="news")
            return self._pool

    def close(self) -> None:
        with self._pool_lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
