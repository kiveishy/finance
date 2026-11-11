"""Chart data preparation: ranges, candle intervals, indicators. Computed server-side (testable)."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Any, Callable, ClassVar, Sequence

from finance_aggregator.core import DataNotFoundError, DateRange, PriceBar, normalize_symbol

from .errors import ValidationError
from .market_data import MarketData

MAX_INDICATORS = 10
MAX_PERIOD = 1000
#: Earliest date requested for the ALL range (pre-1970 timestamps break some backends on Windows).
ALL_START = date(1970, 1, 2)


class Interval(Enum):
    DAY = ("D", 7 / 5)  # calendar days per trading bar (for indicator warm-up)
    WEEK = ("W", 7.0)
    MONTH = ("M", 31.0)

    def __init__(self, key: str, calendar_days_per_bar: float) -> None:
        self.key = key
        self.calendar_days_per_bar = calendar_days_per_bar

    @classmethod
    def parse(cls, raw: str | None) -> "Interval | None":
        if not raw:
            return None
        for interval in cls:
            if interval.key == raw.upper():
                return interval
        raise ValidationError(f"Unknown interval {raw!r} (use D, W or M)")

    def warmup_days(self, bars: int) -> int:
        return math.ceil(bars * self.calendar_days_per_bar) + 10 if bars else 0

    def bucket(self, day: date) -> tuple[int, int]:
        if self is Interval.WEEK:
            iso = day.isocalendar()
            return iso[0], iso[1]
        if self is Interval.MONTH:
            return day.year, day.month
        return day.toordinal(), 0


class ChartRange(Enum):
    """Visible range presets. `days` None = all available history."""

    D1 = ("1D", 1, Interval.DAY)
    W1 = ("1W", 7, Interval.DAY)
    M1 = ("1M", 31, Interval.DAY)
    M3 = ("3M", 92, Interval.DAY)
    M6 = ("6M", 183, Interval.DAY)
    Y1 = ("1Y", 366, Interval.DAY)
    Y2 = ("2Y", 731, Interval.DAY)
    Y5 = ("5Y", 1827, Interval.WEEK)
    ALL = ("ALL", None, Interval.MONTH)

    def __init__(self, key: str, days: int | None, default_interval: Interval) -> None:
        self.key = key
        self.days = days
        self.default_interval = default_interval

    @classmethod
    def parse(cls, raw: str | None) -> "ChartRange":
        wanted = (raw or "6M").upper()
        for chart_range in cls:
            if chart_range.key == wanted:
                return chart_range
        raise ValidationError(f"Unknown range {raw!r} (use {', '.join(r.key for r in cls)})")

    def visible_start(self, today: date) -> date:
        return ALL_START if self.days is None else today - timedelta(days=self.days)


# ---- indicators ------------------------------------------------------------------------------


class Indicator(ABC):
    """A series computed from closing prices. Register subclasses in INDICATORS."""

    type: ClassVar[str]
    label_prefix: ClassVar[str]

    def __init__(self, period: int) -> None:
        if not 1 <= period <= MAX_PERIOD:
            raise ValidationError(f"Indicator period must be between 1 and {MAX_PERIOD}")
        self.period = period

    @property
    def key(self) -> str:
        return f"{self.type}:{self.period}"

    @property
    def label(self) -> str:
        return f"{self.label_prefix} {self.period}"

    @property
    def warmup_bars(self) -> int:
        return self.period

    @abstractmethod
    def compute(self, values: Sequence[float | None]) -> list[float | None]: ...


class SimpleMovingAverage(Indicator):
    type = "sma"
    label_prefix = "MA"

    def compute(self, values: Sequence[float | None]) -> list[float | None]:
        result: list[float | None] = []
        window_sum, valid = 0.0, 0
        for index, value in enumerate(values):
            if value is not None:
                window_sum += value
                valid += 1
            if index >= self.period:
                dropped = values[index - self.period]
                if dropped is not None:
                    window_sum -= dropped
                    valid -= 1
            full = index >= self.period - 1 and valid == self.period
            result.append(window_sum / self.period if full else None)
        return result


class ExponentialMovingAverage(Indicator):
    type = "ema"
    label_prefix = "EMA"

    def compute(self, values: Sequence[float | None]) -> list[float | None]:
        alpha = 2 / (self.period + 1)
        result: list[float | None] = []
        seed: list[float] = []
        current: float | None = None
        for value in values:
            if value is None:
                result.append(None)
                continue
            if current is None:
                seed.append(value)
                if len(seed) == self.period:
                    current = sum(seed) / self.period  # SMA seed, as in most charting tools
                result.append(current)
                continue
            current = alpha * value + (1 - alpha) * current
            result.append(current)
        return result


INDICATORS: dict[str, Callable[[int], Indicator]] = {
    SimpleMovingAverage.type: SimpleMovingAverage,
    ExponentialMovingAverage.type: ExponentialMovingAverage,
}


def parse_indicators(raw: str | None) -> list[Indicator]:
    """Parse "sma:20,sma:50,ema:12" (duplicates removed, order kept)."""
    specs = [s.strip().lower() for s in (raw or "").split(",") if s.strip()]
    if len(specs) > MAX_INDICATORS:
        raise ValidationError(f"At most {MAX_INDICATORS} indicators per chart")
    indicators: dict[str, Indicator] = {}
    for spec in specs:
        kind, _, period = spec.partition(":")
        factory = INDICATORS.get(kind)
        if factory is None:
            raise ValidationError(f"Unknown indicator {kind!r} (available: {', '.join(sorted(INDICATORS))})")
        if not period.isdigit():
            raise ValidationError(f"Indicator {spec!r} needs a numeric period, e.g. {kind}:20")
        indicator = factory(int(period))
        indicators.setdefault(indicator.key, indicator)
    return list(indicators.values())


# ---- resampling ------------------------------------------------------------------------------


def resample(bars: Sequence[PriceBar], interval: Interval) -> list[PriceBar]:
    """Aggregate daily bars into weekly/monthly candles (time = first trading day of the period)."""
    if interval is Interval.DAY:
        return list(bars)
    buckets: dict[tuple[int, int], list[PriceBar]] = {}
    for bar in bars:
        buckets.setdefault(interval.bucket(bar.date), []).append(bar)
    return [_merge(group) for group in buckets.values()]


def _merge(group: list[PriceBar]) -> PriceBar:
    highs = [b.high for b in group if b.high is not None]
    lows = [b.low for b in group if b.low is not None]
    volumes = [b.volume for b in group if b.volume is not None]
    sources = list(dict.fromkeys(b.source for b in group if b.source))
    return PriceBar(
        date=group[0].date,
        open=group[0].open,
        high=max(highs) if highs else None,
        low=min(lows) if lows else None,
        close=group[-1].close,
        adj_close=group[-1].adj_close,
        volume=sum(volumes) if volumes else None,
        source="+".join(sources),
    )


# ---- service ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ChartRequest:
    symbol: str
    chart_range: ChartRange
    interval: Interval
    indicators: tuple[Indicator, ...]

    @classmethod
    def parse(cls, symbol: str, range_key: str | None, interval_key: str | None, indicators: str | None) -> "ChartRequest":
        chart_range = ChartRange.parse(range_key)
        return cls(
            symbol=normalize_symbol(symbol),
            chart_range=chart_range,
            interval=Interval.parse(interval_key) or chart_range.default_interval,
            indicators=tuple(parse_indicators(indicators)),
        )


class ChartService:
    #: Daily charts always load at least this much history so the user can scroll back.
    MIN_DAILY_LOOKBACK_DAYS = 400

    def __init__(
        self,
        market: MarketData,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._market = market
        self._today = today

    def build(self, request: ChartRequest) -> dict[str, Any]:
        today = self._today()
        visible_start = request.chart_range.visible_start(today)
        warmup = max((i.warmup_bars for i in request.indicators), default=0)
        fetch_start = visible_start - timedelta(days=request.interval.warmup_days(warmup))
        if request.interval is Interval.DAY:
            fetch_start = min(fetch_start, today - timedelta(days=self.MIN_DAILY_LOOKBACK_DAYS))
        fetch_start = max(fetch_start, ALL_START)

        history = self._market.get_price_history(request.symbol, DateRange(fetch_start, today))
        # Candles need full OHLC; providers occasionally return partial rows (e.g. halted days).
        daily = [b for b in history.bars if None not in (b.open, b.high, b.low, b.close)]
        if not daily:
            raise DataNotFoundError(f"No complete price bars for {request.symbol}")
        bars = resample(daily, request.interval)
        closes = [b.close for b in bars]

        if request.chart_range is ChartRange.D1:
            visible_from = daily[-1].date
        else:
            visible_from = max(visible_start, bars[0].date)

        return {
            "symbol": request.symbol,
            "range": request.chart_range.key,
            "interval": request.interval.key,
            "visible_from": visible_from.isoformat(),
            "visible_to": bars[-1].date.isoformat(),
            "source": history.source,
            "sources": history.source_counts,
            "quote": _quote(daily),
            "bars": [_bar_dict(b) for b in bars],
            "indicators": [
                {
                    "key": indicator.key,
                    "type": indicator.type,
                    "period": indicator.period,
                    "label": indicator.label,
                    "values": [
                        {"time": bar.date.isoformat(), "value": round(value, 6)}
                        for bar, value in zip(bars, indicator.compute(closes))
                        if value is not None
                    ],
                }
                for indicator in request.indicators
            ],
        }


def _bar_dict(bar: PriceBar) -> dict[str, Any]:
    return {
        "time": bar.date.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "source": bar.source,
    }


def _quote(daily: Sequence[PriceBar]) -> dict[str, Any]:
    last = daily[-1]
    previous = next((b.close for b in reversed(daily[:-1]) if b.close is not None), None)
    change = last.close - previous if last.close is not None and previous else None
    return {
        "date": last.date.isoformat(),
        "close": last.close,
        "previous_close": previous,
        "change": change,
        "change_percent": (change / previous * 100) if change is not None and previous else None,
        "source": last.source,
    }
