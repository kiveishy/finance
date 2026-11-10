from __future__ import annotations

from datetime import date, timedelta

import pytest

from fakes import TODAY, FakeMarket, daily_bars, market_with
from finance_aggregator.core import DataNotFoundError, PriceBar
from owl.charts import (
    ALL_START,
    ChartRange,
    ChartRequest,
    ChartService,
    ExponentialMovingAverage,
    Interval,
    SimpleMovingAverage,
    parse_indicators,
    resample,
)
from owl.errors import ValidationError


class TestIndicators:
    def test_sma(self) -> None:
        assert SimpleMovingAverage(3).compute([1, 2, 3, 4, 5]) == [None, None, 2.0, 3.0, 4.0]

    def test_sma_window_with_gap_is_undefined(self) -> None:
        assert SimpleMovingAverage(2).compute([1, None, 3, 5]) == [None, None, None, 4.0]

    def test_ema_seeded_with_sma(self) -> None:
        values = EMA = ExponentialMovingAverage(3).compute([1, 2, 3, 4, 5])
        assert values[:2] == [None, None]
        assert values[2] == pytest.approx(2.0)
        assert values[3] == pytest.approx(0.5 * 4 + 0.5 * 2.0)
        assert EMA[4] == pytest.approx(0.5 * 5 + 0.5 * 3.0)

    def test_parse(self) -> None:
        indicators = parse_indicators("sma:20, SMA:50,ema:12,sma:20")
        assert [i.key for i in indicators] == ["sma:20", "sma:50", "ema:12"]
        assert [i.label for i in indicators] == ["MA 20", "MA 50", "EMA 12"]
        assert parse_indicators(None) == [] and parse_indicators("") == []

    @pytest.mark.parametrize("spec", ["rsi:14", "sma", "sma:abc", "sma:0", "sma:5000", ",".join(f"sma:{i}" for i in range(1, 12))])
    def test_parse_rejects(self, spec: str) -> None:
        with pytest.raises(ValidationError):
            parse_indicators(spec)


class TestResample:
    def test_weekly_and_monthly_ohlcv_and_sources(self) -> None:
        bars = daily_bars(date(2026, 1, 5), 10, source="db")  # Mon 5 Jan .. Fri 16 Jan
        bars[-1] = PriceBar(bars[-1].date, 1, 200, 0.5, 150, 150, 5, "yahoo")
        weekly = resample(bars, Interval.WEEK)
        assert [b.date for b in weekly] == [date(2026, 1, 5), date(2026, 1, 12)]
        first, second = weekly
        assert first.open == bars[0].open and first.close == bars[4].close
        assert first.high == max(b.high for b in bars[:5]) and first.low == min(b.low for b in bars[:5])
        assert first.volume == sum(b.volume for b in bars[:5])
        assert (first.source, second.source) == ("db", "db+yahoo")
        assert second.high == 200 and second.close == 150

        monthly = resample(daily_bars(date(2026, 1, 26), 10), Interval.MONTH)
        assert [b.date.month for b in monthly] == [1, 2]

    def test_daily_is_identity(self) -> None:
        bars = daily_bars(date(2026, 1, 5), 3)
        assert resample(bars, Interval.DAY) == bars


class TestRanges:
    def test_parse_and_defaults(self) -> None:
        assert ChartRange.parse(None) is ChartRange.M6
        assert ChartRange.parse("5y").default_interval is Interval.WEEK
        assert ChartRange.parse("ALL").default_interval is Interval.MONTH
        assert [r.key for r in ChartRange] == ["1D", "1W", "1M", "3M", "6M", "1Y", "2Y", "5Y", "ALL"]
        with pytest.raises(ValidationError):
            ChartRange.parse("10Y")
        with pytest.raises(ValidationError):
            Interval.parse("H")

    def test_request_parsing(self) -> None:
        request = ChartRequest.parse("aapl", "5Y", None, "sma:20")
        assert (request.symbol, request.interval) == ("AAPL", Interval.WEEK)
        assert ChartRequest.parse("aapl", "5Y", "d", None).interval is Interval.DAY


class TestChartService:
    def service(self, market: FakeMarket) -> ChartService:
        return ChartService(market, today=lambda: TODAY)

    def test_fetches_warmup_for_indicators(self) -> None:
        market = market_with(days=900)
        payload = self.service(market).build(ChartRequest.parse("AAPL", "1M", None, "sma:200"))
        fetched = market.requests[0][1]
        assert fetched.end == TODAY
        assert fetched.start <= TODAY - timedelta(days=31) - timedelta(days=280)
        ma = payload["indicators"][0]
        assert ma["key"] == "sma:200" and ma["label"] == "MA 200"
        assert ma["values"][-1]["time"] == payload["bars"][-1]["time"]
        assert payload["visible_from"] == (TODAY - timedelta(days=31)).isoformat()

    def test_payload_shape_quote_and_sources(self) -> None:
        payload = self.service(market_with(days=300, source="yahoo-db-api")).build(ChartRequest.parse("AAPL", "6M", None, None))
        bar = payload["bars"][-1]
        assert set(bar) == {"time", "open", "high", "low", "close", "volume", "source"}
        assert bar["source"] == "yahoo-db-api"
        assert payload["sources"] == {"yahoo-db-api": len(payload["bars"])}
        quote = payload["quote"]
        assert quote["change"] == pytest.approx(1.0)
        assert quote["change_percent"] == pytest.approx(100 / payload["bars"][-2]["close"])
        assert payload["interval"] == "D" and payload["range"] == "6M"

    def test_one_day_range_shows_last_session(self) -> None:
        payload = self.service(market_with(days=300)).build(ChartRequest.parse("AAPL", "1D", None, None))
        assert payload["visible_from"] == payload["bars"][-1]["time"]

    def test_all_range_monthly(self) -> None:
        market = market_with(days=900)
        payload = self.service(market).build(ChartRequest.parse("AAPL", "ALL", None, "sma:12"))
        assert market.requests[0][1].start == ALL_START
        assert payload["interval"] == "M"
        assert len(payload["bars"]) < 50
        assert payload["visible_from"] == payload["bars"][0]["time"]

    def test_incomplete_bars_are_dropped_and_no_data_is_not_found(self) -> None:
        bars = daily_bars(TODAY - timedelta(days=40), 20)
        bars[3] = PriceBar(bars[3].date, None, None, None, None, None, None, "x")
        payload = self.service(FakeMarket({"AAPL": bars})).build(ChartRequest.parse("AAPL", "1M", None, None))
        assert len(payload["bars"]) == 19
        with pytest.raises(DataNotFoundError):
            self.service(FakeMarket({"AAPL": [bars[3]]})).build(ChartRequest.parse("AAPL", "1M", None, None))
        with pytest.raises(DataNotFoundError):
            self.service(FakeMarket()).build(ChartRequest.parse("MSFT", "1M", None, None))
