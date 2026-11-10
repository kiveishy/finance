from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import pytest

yf_exceptions = pytest.importorskip("yfinance.exceptions")

from finance_aggregator.core import (  # noqa: E402
    DataNotFoundError,
    DateRange,
    HealthState,
    ProviderError,
    ProviderUnavailableError,
)
from finance_aggregator.providers.yahoo_finance import YahooFinanceProvider  # noqa: E402

RANGE = DateRange(date(2026, 10, 1), date(2026, 10, 2))


class FakeTicker:
    def __init__(self, info: Any = None, history: Any = None) -> None:
        self._info = info
        self._history = history
        self.history_kwargs: dict[str, Any] = {}

    @property
    def info(self) -> Any:
        if isinstance(self._info, BaseException):
            raise self._info
        return self._info

    def history(self, **kwargs: Any) -> Any:
        self.history_kwargs = kwargs
        if isinstance(self._history, BaseException):
            raise self._history
        return self._history


def provider_for(ticker: FakeTicker) -> YahooFinanceProvider:
    return YahooFinanceProvider(name="yahoo", ticker_factory=lambda symbol: ticker)


class HttpError(OSError):
    """Mimics curl_cffi's HTTPError (an OSError carrying a response)."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP Error {status}: ")
        self.response = type("R", (), {"status_code": status})()


def test_info_is_mapped_like_yahoo_db() -> None:
    info = provider_for(
        FakeTicker(info={"symbol": "AAPL", "quoteType": "EQUITY", "shortName": "Apple", "longName": "Apple Inc.",
                         "marketCap": 3.5e12, "fullTimeEmployees": 150000, "longBusinessSummary": "Phones"})
    ).get_ticker_info("aapl")
    assert (info.symbol, info.short_name, info.long_name, info.quote_type) == ("AAPL", "Apple", "Apple Inc.", "EQUITY")
    assert info.market_cap == 3.5e12 and info.employees == 150000 and info.description == "Phones"
    assert info.source == "yahoo"


@pytest.mark.parametrize(
    "behaviour",
    [{"trailingPegRatio": None}, {}, HttpError(404), yf_exceptions.YFTickerMissingError("ZZZ", "no tz")],
)
def test_info_not_found_cases(behaviour: Any) -> None:
    with pytest.raises(DataNotFoundError):
        provider_for(FakeTicker(info=behaviour)).get_ticker_info("ZZZ")


@pytest.mark.parametrize(
    "behaviour",
    [ConnectionError("refused"), HttpError(503), HttpError(429), yf_exceptions.YFRateLimitError(), TimeoutError()],
)
def test_info_unavailable_cases(behaviour: Exception) -> None:
    with pytest.raises(ProviderUnavailableError):
        provider_for(FakeTicker(info=behaviour)).get_ticker_info("AAPL")


def test_unexpected_yfinance_error_is_provider_error() -> None:
    with pytest.raises(ProviderError) as caught:
        provider_for(FakeTicker(info=yf_exceptions.YFDataException("weird"))).get_ticker_info("AAPL")
    assert not isinstance(caught.value, ProviderUnavailableError)


def test_history_conversion_and_inclusive_end() -> None:
    frame = pd.DataFrame(
        {"Open": [1.0, 2.0], "High": [1.5, 2.5], "Low": [0.5, 1.5], "Close": [1.2, float("nan")], "Volume": [100, float("nan")]},
        index=pd.to_datetime(["2026-10-01", "2026-10-02"]).tz_localize("America/New_York"),
    )
    ticker = FakeTicker(history=frame)
    history = provider_for(ticker).get_price_history("AAPL", RANGE)
    assert [b.date for b in history.bars] == [date(2026, 10, 1), date(2026, 10, 2)]
    assert history.bars[0].adj_close == history.bars[0].close == 1.2
    assert history.bars[1].close is None and history.bars[1].volume is None
    assert ticker.history_kwargs["end"] == "2026-10-03"  # yfinance end is exclusive


@pytest.mark.parametrize(
    "behaviour",
    [pd.DataFrame(), yf_exceptions.YFPricesMissingError("ZZZ", ""), yf_exceptions.YFTzMissingError("ZZZ")],
)
def test_history_not_found_cases(behaviour: Any) -> None:
    with pytest.raises(DataNotFoundError):
        provider_for(FakeTicker(history=behaviour)).get_price_history("ZZZ", RANGE)


def test_history_network_failure_is_unavailable_and_status_reflects_it() -> None:
    provider = provider_for(FakeTicker(history=ConnectionError("no route")))
    with pytest.raises(ProviderUnavailableError):
        provider.get_price_history("AAPL", RANGE)
    assert provider.check_status().state is HealthState.UNAVAILABLE


@pytest.mark.live
def test_live_yahoo_finance() -> None:
    provider = YahooFinanceProvider(name="yahoo")
    assert provider.get_ticker_info("MSFT").quote_type == "EQUITY"
    assert not provider.get_price_history("MSFT", DateRange.last_days(14)).is_empty
    with pytest.raises(DataNotFoundError):
        provider.get_ticker_info("ZZZZNOPEX")
