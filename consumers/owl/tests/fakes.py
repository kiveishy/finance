from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from finance_aggregator.core import (
    DataNotFoundError,
    DateRange,
    HealthState,
    PriceBar,
    PriceHistory,
    ServiceStatus,
    SymbolMatch,
    TickerInfo,
    merge_matches,
    normalize_query,
    normalize_symbol,
    relevance,
)

TODAY = date(2026, 11, 10)


def daily_bars(start: date, days: int, *, price: float = 100.0, source: str = "fake") -> list[PriceBar]:
    """Weekday bars with a rising close."""
    bars, day, i = [], start, 0
    while len(bars) < days:
        if day.weekday() < 5:
            close = price + i
            bars.append(PriceBar(day, close - 0.5, close + 1, close - 1, close, close, 1000 + i, source))
            i += 1
        day += timedelta(days=1)
    return bars


class FakeMarket:
    """In-memory MarketData: symbols -> daily bars; records requested ranges."""

    def __init__(self, bars: dict[str, list[PriceBar]] | None = None, names: dict[str, str] | None = None) -> None:
        self.bars = bars or {}
        self.names = names or {}
        self.requests: list[tuple[str, DateRange]] = []
        self.status: ServiceStatus | Exception = ServiceStatus("fake", HealthState.AVAILABLE, (), "1/1 providers available")
        self.status_calls = 0
        self.closed = False

    def get_ticker_info(self, symbol: str) -> TickerInfo:
        symbol = normalize_symbol(symbol)
        if symbol not in self.bars and symbol not in self.names:
            raise DataNotFoundError(f"{symbol} unknown")
        return TickerInfo(symbol, long_name=self.names.get(symbol, f"{symbol} Corp"), source="fake")

    def get_price_history(self, symbol: str, date_range: DateRange) -> PriceHistory:
        symbol = normalize_symbol(symbol)
        self.requests.append((symbol, date_range))
        bars = [b for b in self.bars.get(symbol, []) if date_range.start <= b.date <= date_range.end]
        if not bars:
            raise DataNotFoundError(f"no history for {symbol}")
        return PriceHistory(symbol, date_range, tuple(bars), "+".join(dict.fromkeys(b.source for b in bars)))

    def check_status(self) -> ServiceStatus:
        self.status_calls += 1
        if isinstance(self.status, Exception):
            raise self.status
        return self.status

    def search_symbols(self, query: str, limit: int = 10) -> list[SymbolMatch]:
        q = normalize_query(query)
        known = {**{s: f"{s} Corp" for s in self.bars}, **self.names}
        matches = [SymbolMatch(s, n) for s, n in known.items() if relevance(q, s, n) > 0]
        return merge_matches(q, [matches], limit)

    def close(self) -> None:
        self.closed = True


def market_with(symbol: str = "AAPL", days: int = 600, **kwargs: Any) -> FakeMarket:
    start = TODAY - timedelta(days=int(days * 7 / 5) + 5)
    return FakeMarket({symbol: daily_bars(start, days, **kwargs)})
