"""S&P 500 members: downloaded from Wikipedia (or the GitHub "datasets" CSV), cached, with a bundled fallback."""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Protocol, Sequence

logger = logging.getLogger(__name__)

WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
DATASETS_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
USER_AGENT = "snp-monitor/0.1 (local desktop app)"
# The index has ~503 lines (some companies list two share classes); fewer means a broken download/parse.
MIN_CONSTITUENTS = 400
SNAPSHOT_FILE = Path(__file__).resolve().parent / "sp500_snapshot.json"
_CLASS_SUFFIX = re.compile(r"\s*\((?:class|series)\s+[a-z0-9]+\)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Constituent:
    symbol: str  # as listed by S&P, e.g. BRK.B
    name: str
    sector: str = ""
    sub_industry: str = ""
    cik: str = ""  # SEC company id: share classes of one company share it

    @property
    def yahoo_symbol(self) -> str:
        return self.symbol.replace(".", "-")

    @property
    def company_key(self) -> str:
        return self.cik or company_name(self.name).lower()


@dataclass(frozen=True)
class ConstituentList:
    items: tuple[Constituent, ...]
    source: str
    fetched_at: float


def company_name(security: str) -> str:
    """'Alphabet Inc. (Class A)' -> 'Alphabet Inc.'"""
    return _CLASS_SUFFIX.sub("", security).strip()


def _normalize_cik(raw: str) -> str:
    digits = raw.strip().lstrip("0")
    return digits if digits.isdigit() else ""


def _build(rows: Sequence[dict[str, str]]) -> list[Constituent]:
    items: list[Constituent] = []
    seen: set[str] = set()
    for row in rows:
        symbol = (row.get("Symbol") or "").strip().upper()
        name = (row.get("Security") or "").strip()
        if not symbol or not name or symbol in seen or not re.fullmatch(r"[A-Z0-9.\-]{1,10}", symbol):
            continue
        seen.add(symbol)
        items.append(Constituent(
            symbol=symbol,
            name=name,
            sector=(row.get("GICS Sector") or "").strip(),
            sub_industry=(row.get("GICS Sub-Industry") or "").strip(),
            cik=_normalize_cik(row.get("CIK") or ""),
        ))
    return items


class _ConstituentsTableParser(HTMLParser):
    """Collects the cell texts of <table id="constituents">."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._table_depth = 0  # >0 while inside the constituents table (nested tables counted)
        self._cell: list[str] | None = None
        self._row: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            if self._table_depth:
                self._table_depth += 1
            elif dict(attrs).get("id") == "constituents":
                self._table_depth = 1
        elif self._table_depth == 1:
            if tag == "tr":
                self._row = []
            elif tag in ("td", "th") and self._row is not None:
                self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if not self._table_depth:
            return
        if tag == "table":
            self._table_depth -= 1
        elif self._table_depth == 1:
            if tag in ("td", "th") and self._cell is not None and self._row is not None:
                self._row.append(" ".join("".join(self._cell).split()))
                self._cell = None
            elif tag == "tr" and self._row is not None:
                if self._row:
                    self.rows.append(self._row)
                self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def parse_wikipedia(html: str) -> list[Constituent]:
    parser = _ConstituentsTableParser()
    parser.feed(html)
    if not parser.rows:
        return []
    header, *body = parser.rows
    return _build([dict(zip(header, row)) for row in body if len(row) == len(header)])


def parse_datasets_csv(text: str) -> list[Constituent]:
    return _build(list(csv.DictReader(io.StringIO(text))))


GetText = Callable[[str, float], str]


def http_get_text(url: str, timeout: float) -> str:
    import requests

    response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.text


class ConstituentSource(Protocol):
    name: str

    def fetch(self) -> list[Constituent]: ...


class WebSource:
    def __init__(self, name: str, url: str, parse: Callable[[str], list[Constituent]], *,
                 timeout: float = 20.0, get_text: GetText = http_get_text) -> None:
        self.name = name
        self._url = url
        self._parse = parse
        self._timeout = timeout
        self._get_text = get_text

    def fetch(self) -> list[Constituent]:
        return self._parse(self._get_text(self._url, self._timeout))


def create_sources(names: Sequence[str], *, timeout: float, get_text: GetText = http_get_text) -> list[WebSource]:
    known = {
        "wikipedia": (WIKIPEDIA_URL, parse_wikipedia),
        "github-datasets": (DATASETS_URL, parse_datasets_csv),
    }
    return [WebSource(name, *known[name], timeout=timeout, get_text=get_text) for name in names]


class ConstituentsProvider:
    """Current member list: fresh cache -> online sources (in order) -> stale cache -> bundled snapshot."""

    def __init__(
        self,
        sources: Sequence[ConstituentSource],
        cache_file: Path,
        *,
        max_age_seconds: float = 24 * 3600,
        snapshot_file: Path = SNAPSHOT_FILE,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sources = list(sources)
        self._cache_file = cache_file
        self._max_age = max_age_seconds
        self._snapshot_file = snapshot_file
        self._clock = clock

    def get(self) -> ConstituentList:
        cached = _read(self._cache_file)
        if cached and self._clock() - cached.fetched_at < self._max_age:
            return cached
        for source in self._sources:
            try:
                items = source.fetch()
            except Exception as exc:  # noqa: BLE001 - try the next source
                logger.warning("S&P 500 list from %s failed: %s", source.name, exc)
                continue
            if len(items) < MIN_CONSTITUENTS:
                logger.warning("S&P 500 list from %s has only %d members - ignored", source.name, len(items))
                continue
            result = ConstituentList(tuple(items), source.name, self._clock())
            _write(self._cache_file, result)
            logger.info("S&P 500 list: %d members from %s", len(items), source.name)
            return result
        if cached:
            logger.warning("Using the cached S&P 500 list from %s", time.strftime("%Y-%m-%d %H:%M", time.localtime(cached.fetched_at)))
            return cached
        snapshot = _read(self._snapshot_file)
        if snapshot:
            logger.warning("Using the bundled S&P 500 list (%d members)", len(snapshot.items))
            return ConstituentList(snapshot.items, "bundled", snapshot.fetched_at)
        raise RuntimeError("S&P 500 member list is unavailable (offline and no cached copy)")


def _read(path: Path) -> ConstituentList | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = tuple(Constituent(**item) for item in raw["items"])
        return ConstituentList(items, str(raw["source"]), float(raw["fetched_at"])) if items else None
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Ignoring unreadable S&P 500 list %s: %s", path, exc)
        return None


def _write(path: Path, value: ConstituentList) -> None:
    payload = {"source": value.source, "fetched_at": value.fetched_at, "items": [asdict(i) for i in value.items]}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning("Cannot cache the S&P 500 list in %s: %s", path, exc)


def write_snapshot(value: ConstituentList, path: Path = SNAPSHOT_FILE) -> None:
    _write(path, value)
