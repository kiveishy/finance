"""Symbol search: shared query validation, relevance ranking and result merging.

Every provider returns candidate matches; the aggregator re-ranks them with `relevance` so results
from different backends are ordered consistently ("nearest" first).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable, Sequence

from .errors import InvalidRequestError

MAX_QUERY_LENGTH = 64
MAX_LIMIT = 50
_QUERY_RE = re.compile(r"^[\w .&'\-^=,/()+]+$", re.UNICODE)
#: Score for provider results that match semantically but not textually (e.g. Yahoo fuzzy hits).
FUZZY_SCORE = 20.0
_DERIVATIVE_TYPES = frozenset({"FUTURE", "OPTION", "CRYPTOCURRENCY", "CURRENCY"})


def listing_penalty(symbol: str, quote_type: str | None) -> float:
    """Push futures/crypto/FX and foreign duplicate listings (AAPL.TO) below the primary listing."""
    if (quote_type or "").upper() in _DERIVATIVE_TYPES or "=" in symbol:
        return 20.0
    if "." in symbol:
        return 5.0
    return 0.0


def normalize_query(raw: str) -> str:
    query = " ".join(str(raw or "").split())
    if not query:
        raise InvalidRequestError("Search query must not be empty")
    if len(query) > MAX_QUERY_LENGTH:
        raise InvalidRequestError(f"Search query must be at most {MAX_QUERY_LENGTH} characters")
    if not _QUERY_RE.fullmatch(query):
        raise InvalidRequestError("Search query contains unsupported characters")
    return query


def normalize_limit(raw: Any, default: int = 10) -> int:
    if raw is None or raw == "":
        return default
    try:
        limit = int(raw)
    except (TypeError, ValueError) as exc:
        raise InvalidRequestError("Search limit must be an integer") from exc
    if not 1 <= limit <= MAX_LIMIT:
        raise InvalidRequestError(f"Search limit must be between 1 and {MAX_LIMIT}")
    return limit


def relevance(query: str, symbol: str, name: str | None) -> float:
    """0 = no textual match; higher = nearer. Ticker matches beat company-name matches."""
    q = query.upper()
    s = symbol.upper()
    n = (name or "").upper()
    if s == q:
        return 100.0
    if s.startswith(q):
        return 90.0 - min(len(s) - len(q), 9)
    if n.startswith(q):
        return 75.0
    if any(word.startswith(q) for word in re.split(r"[\s,.\-&/()]+", n) if word):
        return 65.0
    if q in s:
        return 50.0
    if q in n:
        return 45.0
    return 0.0


@dataclass(frozen=True, slots=True)
class SymbolMatch:
    symbol: str
    name: str | None = None
    exchange: str | None = None
    quote_type: str | None = None
    source: str = ""
    score: float = 0.0

    def with_source(self, source: str) -> "SymbolMatch":
        return self if self.source else replace(self, source=source)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SymbolMatch":
        return cls(
            symbol=str(data["symbol"]),
            name=data.get("name"),
            exchange=data.get("exchange"),
            quote_type=data.get("quote_type"),
            source=str(data.get("source") or ""),
            score=float(data.get("score") or 0.0),
        )


def merge_matches(query: str, ranked_lists: Iterable[Sequence[SymbolMatch]], limit: int) -> list[SymbolMatch]:
    """Merge per-provider results (given in provider priority order) into one ranked list.

    Duplicates are folded (first provider wins, missing fields are filled from the others),
    each symbol is re-scored with `relevance`, and provider order breaks ties.
    """
    merged: dict[str, tuple[SymbolMatch, int, int]] = {}  # symbol -> (match, provider index, position)
    for provider_index, matches in enumerate(ranked_lists):
        for position, match in enumerate(matches):
            current = merged.get(match.symbol)
            if current is None:
                merged[match.symbol] = (match, provider_index, position)
                continue
            existing = current[0]
            sources = existing.source if match.source in existing.source.split("+") else f"{existing.source}+{match.source}"
            merged[match.symbol] = (
                replace(
                    existing,
                    name=existing.name or match.name,
                    exchange=existing.exchange or match.exchange,
                    quote_type=existing.quote_type or match.quote_type,
                    source=sources.strip("+"),
                ),
                current[1],
                min(current[2], position),
            )

    scored = []
    for match, provider_index, position in merged.values():
        textual = relevance(query, match.symbol, match.name) or max(FUZZY_SCORE - position * 0.5, 1.0)
        score = max(textual - listing_penalty(match.symbol, match.quote_type), 1.0)
        scored.append((replace(match, score=score), provider_index, position))
    scored.sort(key=lambda item: (
        -item[0].score, item[0].name is None, item[1], item[2], len(item[0].symbol), item[0].symbol,
    ))
    return [match for match, _, _ in scored[:limit]]
