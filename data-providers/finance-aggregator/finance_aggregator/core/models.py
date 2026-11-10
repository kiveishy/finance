"""Provider-independent domain models.

Every provider maps its native representation to these immutable value objects, so consumers
never see backend-specific shapes. Field names follow the yahoo-db schema/API (the reference
data contract of this system).
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Mapping

from .errors import InvalidRequestError

# Yahoo-style symbols: AAPL, BRK-B, BF.B, ^GSPC, EURUSD=X, BTC-USD
_SYMBOL_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.\-^=]{0,19}$")


def normalize_symbol(raw: str) -> str:
    """Upper-case and validate a ticker symbol; rejects anything that is not a plausible symbol."""
    symbol = (raw or "").strip().upper()
    if not _SYMBOL_RE.fullmatch(symbol):
        raise InvalidRequestError(f"Invalid ticker symbol: {raw!r}")
    return symbol


def parse_date(value: str | date) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError as exc:
        raise InvalidRequestError(f"Invalid date {value!r}, expected YYYY-MM-DD") from exc


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(result) else result


def _to_int(value: Any) -> int | None:
    as_float = _to_float(value)
    return None if as_float is None else int(as_float)


def _to_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True, slots=True)
class DateRange:
    """Inclusive calendar date range."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise InvalidRequestError(f"Start date {self.start} is after end date {self.end}")

    @classmethod
    def last_days(cls, days: int, today: date | None = None) -> "DateRange":
        if days <= 0:
            raise InvalidRequestError("Number of days must be positive")
        end = today or date.today()
        return cls(end - timedelta(days=days), end)

    @classmethod
    def last_months(cls, months: int, today: date | None = None) -> "DateRange":
        # Same 30-day month approximation as the yahoo-db API.
        return cls.last_days(months * 30, today)

    @classmethod
    def last_years(cls, years: int, today: date | None = None) -> "DateRange":
        return cls.last_days(years * 365, today)

    @classmethod
    def parse(cls, start: str | date, end: str | date | None = None) -> "DateRange":
        return cls(parse_date(start), parse_date(end) if end else date.today())

    @classmethod
    def from_options(
        cls,
        *,
        days: int | None = None,
        months: int | None = None,
        years: int | None = None,
        start: str | date | None = None,
        end: str | date | None = None,
        default_days: int = 30,
        today: date | None = None,
    ) -> "DateRange":
        """Build a range from mutually exclusive request options (same options as the yahoo-db API)."""
        relative = [value for value in (days, months, years) if value is not None]
        if len(relative) > 1 or (relative and (start or end)):
            raise InvalidRequestError("Use only one of: days, months, years, or start/end dates")
        if start:
            return cls.parse(start, end)
        if end:
            raise InvalidRequestError("An end date requires a start date")
        if months is not None:
            return cls.last_months(months, today)
        if years is not None:
            return cls.last_years(years, today)
        return cls.last_days(days if days is not None else default_days, today)

    def __str__(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


@dataclass(frozen=True, slots=True)
class TickerInfo:
    """Descriptive data about a ticker."""

    symbol: str
    short_name: str | None = None
    long_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_cap: float | None = None
    currency: str | None = None
    exchange: str | None = None
    quote_type: str | None = None
    country: str | None = None
    website: str | None = None
    description: str | None = None
    employees: int | None = None
    source: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], source: str = "") -> "TickerInfo":
        """Build from a yahoo-db shaped mapping (DB row / API payload); unknown keys are ignored."""
        return cls(
            symbol=normalize_symbol(str(data.get("symbol") or "")),
            short_name=_to_str(data.get("short_name")),
            long_name=_to_str(data.get("long_name")),
            sector=_to_str(data.get("sector")),
            industry=_to_str(data.get("industry")),
            market_cap=_to_float(data.get("market_cap")),
            currency=_to_str(data.get("currency")),
            exchange=_to_str(data.get("exchange")),
            quote_type=_to_str(data.get("quote_type")),
            country=_to_str(data.get("country")),
            website=_to_str(data.get("website")),
            description=_to_str(data.get("description")),
            employees=_to_int(data.get("employees")),
            source=source,
        )

    @property
    def display_name(self) -> str:
        return self.long_name or self.short_name or self.symbol

    def with_source(self, source: str) -> "TickerInfo":
        return replace(self, source=source)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PriceBar:
    """One OHLCV bar. `adj_close` follows yahoo-db semantics (split/dividend adjusted close)."""

    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    adj_close: float | None
    volume: int | None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PriceBar":
        return cls(
            date=parse_date(data["date"]),
            open=_to_float(data.get("open")),
            high=_to_float(data.get("high")),
            low=_to_float(data.get("low")),
            close=_to_float(data.get("close")),
            adj_close=_to_float(data.get("adj_close")),
            volume=_to_int(data.get("volume")),
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["date"] = self.date.isoformat()
        return result


@dataclass(frozen=True, slots=True)
class PriceHistory:
    """Ordered price bars for one symbol within a requested range."""

    symbol: str
    date_range: DateRange
    bars: tuple[PriceBar, ...]
    source: str = ""

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.bars, key=lambda bar: bar.date))
        object.__setattr__(self, "bars", ordered)

    @property
    def is_empty(self) -> bool:
        return not self.bars

    @property
    def first_date(self) -> date | None:
        return self.bars[0].date if self.bars else None

    @property
    def last_date(self) -> date | None:
        return self.bars[-1].date if self.bars else None

    def with_source(self, source: str) -> "PriceHistory":
        return replace(self, source=source)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "start_date": self.date_range.start.isoformat(),
            "end_date": self.date_range.end.isoformat(),
            "count": len(self.bars),
            "source": self.source,
            "data": [bar.to_dict() for bar in self.bars],
        }


class HealthState(str, Enum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    name: str
    kind: str
    state: HealthState
    detail: str = ""
    latency_ms: float | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        return self.state in (HealthState.AVAILABLE, HealthState.DEGRADED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "state": self.state.value,
            "detail": self.detail,
            "latency_ms": self.latency_ms,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    name: str
    state: HealthState
    providers: tuple[ProviderStatus, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "detail": self.detail,
            "providers": [p.to_dict() for p in self.providers],
        }


class AttemptOutcome(str, Enum):
    SUCCESS = "success"
    INCOMPLETE = "incomplete"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    """Diagnostic record of one provider call made by an aggregating service."""

    provider: str
    outcome: AttemptOutcome
    detail: str = ""
    elapsed_ms: float = 0.0

    def __str__(self) -> str:
        text = f"{self.provider}: {self.outcome.value}"
        return f"{text} ({self.detail})" if self.detail else text


__all__ = [
    "AttemptOutcome",
    "DateRange",
    "HealthState",
    "PriceBar",
    "PriceHistory",
    "ProviderAttempt",
    "ProviderStatus",
    "ServiceStatus",
    "TICKER_INFO_FIELDS",
    "TickerInfo",
    "normalize_symbol",
    "parse_date",
]

# Identical to the yahoo-db `tickers` column names.
TICKER_INFO_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(TickerInfo) if f.name != "source")
