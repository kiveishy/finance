"""Feed document parsers: RSS 2.0 and Atom, picked by the document's root element."""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import ClassVar, Iterable

from .models import FeedInfo, NewsItem

MAX_TITLE = 300
MAX_SUMMARY = 500
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


class FeedParseError(Exception):
    """The document is not a feed this module understands."""


def clean_text(value: str | None, limit: int) -> str:
    """Plain text from a feed field that may contain HTML markup and entities."""
    text = _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value or ""))).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def parse_date(value: str | None) -> datetime | None:
    """RFC 822 (RSS), ISO 8601 (Atom) and "YYYY-MM-DD HH:MM:SS" (assumed UTC); None when unreadable."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment is None:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def safe_link(value: str | None) -> str | None:
    link = (value or "").strip()
    return link if link.startswith(("https://", "http://")) else None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class FeedParser(ABC):
    """Turns one kind of feed document into headlines."""

    registry: ClassVar[list[type["FeedParser"]]] = []

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        FeedParser.registry.append(cls)

    @classmethod
    @abstractmethod
    def accepts(cls, root: ET.Element) -> bool: ...

    @abstractmethod
    def entries(self, root: ET.Element) -> Iterable[ET.Element]: ...

    @abstractmethod
    def item(self, entry: ET.Element, feed: FeedInfo) -> NewsItem | None: ...

    @staticmethod
    def parse(document: bytes, feed: FeedInfo) -> list[NewsItem]:
        try:
            root = ET.fromstring(document)
        except ET.ParseError as exc:
            raise FeedParseError(f"Not an XML feed: {exc}") from exc
        for parser_class in FeedParser.registry:
            if parser_class.accepts(root):
                parser = parser_class()
                return [item for entry in parser.entries(root) if (item := parser.item(entry, feed))]
        raise FeedParseError(f"Unsupported feed format <{_local(root.tag)}>")

    @staticmethod
    def child_text(entry: ET.Element, *names: str) -> str | None:
        """Text of the first child named `names[0]`, else `names[1]`, ... (names are in priority order)."""
        for name in names:
            for child in entry:
                if _local(child.tag) == name:
                    text = "".join(child.itertext())  # also text inside un-escaped markup such as <b>
                    if text.strip():
                        return text
        return None


class RssParser(FeedParser):
    """RSS 2.0 (also RSS 0.9x / 1.0 RDF items)."""

    @classmethod
    def accepts(cls, root: ET.Element) -> bool:
        return _local(root.tag) in ("rss", "RDF")

    def entries(self, root: ET.Element) -> Iterable[ET.Element]:
        return (e for e in root.iter() if _local(e.tag) == "item")

    def item(self, entry: ET.Element, feed: FeedInfo) -> NewsItem | None:
        title = clean_text(self.child_text(entry, "title"), MAX_TITLE)
        link = safe_link(self.child_text(entry, "link")) or safe_link(self.child_text(entry, "guid"))
        if not title or not link:
            return None
        return NewsItem(
            title=title,
            link=link,
            feed_id=feed.id,
            source=feed.name,
            published=parse_date(self.child_text(entry, "pubDate", "date", "published", "updated")),
            summary=clean_text(self.child_text(entry, "description", "summary"), MAX_SUMMARY),
        )


class AtomParser(FeedParser):
    """Atom 1.0."""

    @classmethod
    def accepts(cls, root: ET.Element) -> bool:
        return _local(root.tag) == "feed"

    def entries(self, root: ET.Element) -> Iterable[ET.Element]:
        return (e for e in root if _local(e.tag) == "entry")

    def item(self, entry: ET.Element, feed: FeedInfo) -> NewsItem | None:
        title = clean_text(self.child_text(entry, "title"), MAX_TITLE)
        links = [c for c in entry if _local(c.tag) == "link"]
        preferred = next((c for c in links if c.get("rel", "alternate") == "alternate"), links[0] if links else None)
        link = safe_link(preferred.get("href") if preferred is not None else None)
        if not title or not link:
            return None
        return NewsItem(
            title=title,
            link=link,
            feed_id=feed.id,
            source=feed.name,
            published=parse_date(self.child_text(entry, "published", "updated")),
            summary=clean_text(self.child_text(entry, "summary", "content"), MAX_SUMMARY),
        )
