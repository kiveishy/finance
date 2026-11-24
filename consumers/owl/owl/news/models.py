"""Value objects of the news feature: feed descriptions, headlines and per-feed status."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if moment else None


@dataclass(frozen=True)
class FeedInfo:
    """Where a feed's data comes from; shown in Settings so the user can see every source."""

    id: str
    name: str
    url: str
    homepage: str
    language: str = "en"
    region: str = "Global"
    description: str = ""
    enabled_by_default: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "homepage": self.homepage,
            "language": self.language,
            "region": self.region,
            "description": self.description,
            "enabled_by_default": self.enabled_by_default,
        }


@dataclass(frozen=True)
class NewsItem:
    """One headline."""

    title: str
    link: str
    feed_id: str
    source: str
    published: datetime | None = None
    summary: str = ""

    @property
    def dedupe_key(self) -> str:
        """Same story published by several feeds (or twice by one) has the same key."""
        return _WORD_RE.sub(" ", self.title.casefold()).strip()

    @property
    def sort_key(self) -> float:
        return self.published.timestamp() if self.published else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "link": self.link,
            "feed": self.feed_id,
            "source": self.source,
            "published": _iso(self.published),
            "summary": self.summary,
        }


@dataclass
class FeedStatus:
    """Outcome of the most recent fetches of one feed."""

    feed_id: str
    last_attempt: datetime | None = None
    last_success: datetime | None = None
    item_count: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.last_success is not None and self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "feed": self.feed_id,
            "ok": self.ok,
            "last_attempt": _iso(self.last_attempt),
            "last_success": _iso(self.last_success),
            "items": self.item_count,
            "error": self.error,
        }


@dataclass
class NewsResult:
    """Merged headlines of several feeds plus the status of each feed."""

    items: list[NewsItem] = field(default_factory=list)
    statuses: list[FeedStatus] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"items": [i.to_dict() for i in self.items], "feeds": [s.to_dict() for s in self.statuses]}
