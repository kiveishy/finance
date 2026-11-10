from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Mapping

from finance_aggregator.core import (
    DataNotFoundError,
    DataProvider,
    DateRange,
    PriceBar,
    PriceHistory,
    ProviderError,
    ProviderUnavailableError,
    TickerInfo,
)


def make_bars(start: date, days: int, price: float = 100.0) -> tuple[PriceBar, ...]:
    return tuple(
        PriceBar(start + timedelta(days=i), price + i, price + i + 1, price + i - 1, price + i + 0.5, price + i + 0.5, 1000 + i)
        for i in range(days)
    )


class FakeProvider(DataProvider):
    """Scriptable provider. A behaviour is a value to return, an exception to raise,
    or (history only) a callable(symbol, date_range) -> PriceHistory."""

    kind = "fake"

    def __init__(
        self,
        name: str,
        *,
        info: Any = None,
        history: Any = None,
        available: bool = True,
        probe: Any = ("ok", {}),
    ) -> None:
        super().__init__(name)
        self.info_behaviour = info
        self.history_behaviour = history
        self.available = available
        self.probe_behaviour = probe
        self.calls: list[str] = []
        self.history_requests: list[DateRange] = []
        self.closed = False

    def is_available(self) -> bool:
        return self.available

    def unavailable_reason(self) -> str:
        return "fake offline"

    def close(self) -> None:
        self.closed = True

    @staticmethod
    def _act(behaviour: Any, default: Callable[[], Any]) -> Any:
        if isinstance(behaviour, BaseException):
            raise behaviour
        return behaviour if behaviour is not None else default()

    def _fetch_ticker_info(self, symbol: str) -> TickerInfo:
        self.calls.append(f"info:{symbol}")
        return self._act(self.info_behaviour, lambda: TickerInfo(symbol, short_name=f"{symbol} from {self.name}"))

    def _fetch_price_history(self, symbol: str, date_range: DateRange) -> PriceHistory:
        self.calls.append(f"history:{symbol}")
        self.history_requests.append(date_range)
        behaviour = self.history_behaviour
        if callable(behaviour) and not isinstance(behaviour, BaseException):
            return behaviour(symbol, date_range)
        days = (date_range.end - date_range.start).days + 1
        return self._act(behaviour, lambda: PriceHistory(symbol, date_range, make_bars(date_range.start, days)))

    def _probe(self) -> tuple[str, Mapping[str, Any]]:
        if isinstance(self.probe_behaviour, BaseException):
            raise self.probe_behaviour
        return self.probe_behaviour


def not_found() -> DataNotFoundError:
    return DataNotFoundError("not here")


def unavailable() -> ProviderUnavailableError:
    return ProviderUnavailableError("offline")


def broken() -> ProviderError:
    return ProviderError("bad response")
