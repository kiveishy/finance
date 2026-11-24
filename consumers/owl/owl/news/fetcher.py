"""Downloading feed documents (an interface, so tests and other transports can replace HTTP)."""

from __future__ import annotations

import urllib.request
from abc import ABC, abstractmethod
from urllib.parse import urlsplit


class FeedFetchError(Exception):
    """A feed could not be downloaded."""


class Fetcher(ABC):
    @abstractmethod
    def fetch(self, url: str) -> bytes:
        """Returns the raw document; raises FeedFetchError on failure."""


class HttpFetcher(Fetcher):
    """Plain urllib GET with a timeout and a size cap."""

    # Several publishers answer 403 to unknown clients, so present a regular browser.
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

    def __init__(self, *, timeout_seconds: float = 10.0, max_bytes: int = 4 * 1024 * 1024) -> None:
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes

    def fetch(self, url: str) -> bytes:
        if urlsplit(url).scheme not in ("http", "https"):
            raise FeedFetchError(f"Unsupported feed URL {url!r}")
        request = urllib.request.Request(url, headers={
            "User-Agent": self.USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
        })
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310 - fixed catalog URLs
                body = response.read(self._max_bytes + 1)
        except OSError as exc:  # URLError, HTTPError, timeouts
            raise FeedFetchError(str(getattr(exc, "reason", exc)) or exc.__class__.__name__) from exc
        if len(body) > self._max_bytes:
            raise FeedFetchError("Feed document too large")
        return body
