from __future__ import annotations

from datetime import date, timedelta

import pytest

from fakes import FakeProvider, broken, make_bars, not_found, unavailable
from finance_aggregator.core import (
    AttemptOutcome,
    DataNotFoundError,
    DateRange,
    HealthState,
    InvalidRequestError,
    PriceHistory,
    ProviderUnavailableError,
)
from finance_aggregator.services import AggregatorService, FallbackPolicy

RANGE = DateRange(date(2026, 1, 1), date(2026, 1, 10))


def chain(*providers: FakeProvider, staleness: int | None = 5) -> AggregatorService:
    return AggregatorService(providers, policy=FallbackPolicy(staleness))


class TestPriority:
    def test_first_provider_wins_and_later_ones_are_not_called(self) -> None:
        local, api, yahoo = FakeProvider("local"), FakeProvider("api"), FakeProvider("yahoo")
        info = chain(local, api, yahoo).get_ticker_info("aapl")
        assert info.source == "local"
        assert info.symbol == "AAPL"
        assert api.calls == [] and yahoo.calls == []

    def test_unavailable_local_db_is_skipped_without_being_called(self) -> None:
        local, api = FakeProvider("local", available=False), FakeProvider("api")
        assert chain(local, api).get_ticker_info("AAPL").source == "api"
        assert local.calls == []

    def test_not_found_falls_back_to_next_provider(self) -> None:
        api, yahoo = FakeProvider("api", info=not_found()), FakeProvider("yahoo")
        assert chain(api, yahoo).get_ticker_info("TSLA").source == "yahoo"
        assert api.calls == ["info:TSLA"]

    def test_unreachable_provider_falls_back(self) -> None:
        assert chain(FakeProvider("api", info=unavailable()), FakeProvider("yahoo")).get_ticker_info("X").source == "yahoo"

    def test_provider_error_falls_back(self) -> None:
        assert chain(FakeProvider("api", info=broken()), FakeProvider("yahoo")).get_ticker_info("X").source == "yahoo"

    def test_unexpected_exception_in_provider_does_not_break_chain(self) -> None:
        assert chain(FakeProvider("api", info=KeyError("bug")), FakeProvider("yahoo")).get_ticker_info("X").source == "yahoo"

    def test_full_chain_local_missing_api_not_found_yahoo_answers(self) -> None:
        local = FakeProvider("local", available=False)
        api = FakeProvider("api", history=not_found())
        yahoo = FakeProvider("yahoo")
        history = chain(local, api, yahoo).get_price_history("MSFT", RANGE)
        assert history.source == "yahoo"
        assert not history.is_empty
        assert api.calls == ["history:MSFT"] and yahoo.calls == ["history:MSFT"]

    def test_empty_history_counts_as_not_found(self) -> None:
        api = FakeProvider("api", history=lambda s, r: PriceHistory(s, r, ()))
        assert chain(api, FakeProvider("yahoo")).get_price_history("AAPL", RANGE).source == "yahoo"


class TestFailureClassification:
    def test_all_not_found_raises_data_not_found(self) -> None:
        with pytest.raises(DataNotFoundError) as caught:
            chain(FakeProvider("api", info=not_found()), FakeProvider("yahoo", info=not_found())).get_ticker_info("NOPE")
        assert [a.outcome for a in caught.value.attempts] == [AttemptOutcome.NOT_FOUND] * 2
        assert caught.value.unchecked_providers == ()

    def test_not_found_with_some_sources_unreachable_is_still_not_found_but_flagged(self) -> None:
        with pytest.raises(DataNotFoundError) as caught:
            chain(
                FakeProvider("local", available=False),
                FakeProvider("api", info=not_found()),
                FakeProvider("yahoo", info=unavailable()),
            ).get_ticker_info("NOPE")
        assert caught.value.unchecked_providers == ("local", "yahoo")

    def test_nobody_could_answer_raises_unavailable(self) -> None:
        with pytest.raises(ProviderUnavailableError) as caught:
            chain(
                FakeProvider("local", available=False),
                FakeProvider("api", info=unavailable()),
                FakeProvider("yahoo", info=broken()),
            ).get_ticker_info("AAPL")
        outcomes = [a.outcome for a in caught.value.attempts]
        assert outcomes == [AttemptOutcome.UNAVAILABLE, AttemptOutcome.UNAVAILABLE, AttemptOutcome.ERROR]
        assert not isinstance(caught.value, DataNotFoundError)

    def test_no_providers_configured_is_unavailable(self) -> None:
        with pytest.raises(ProviderUnavailableError):
            AggregatorService([]).get_ticker_info("AAPL")

    def test_invalid_symbol_is_rejected_before_any_provider_call(self) -> None:
        provider = FakeProvider("api")
        with pytest.raises(InvalidRequestError):
            chain(provider).get_ticker_info("../sync/start")
        assert provider.calls == []


class TestHistoryFreshness:
    @staticmethod
    def stale(symbol: str, date_range: DateRange) -> PriceHistory:
        return PriceHistory(symbol, date_range, make_bars(date_range.start, 2))  # ends 8 days early

    def test_stale_history_falls_back_to_fresher_provider(self) -> None:
        api, yahoo = FakeProvider("api", history=self.stale), FakeProvider("yahoo")
        history = chain(api, yahoo).get_price_history("AAPL", RANGE)
        assert history.source == "yahoo"
        assert history.last_date == RANGE.end

    def test_stale_history_returned_when_nobody_has_better(self) -> None:
        api, yahoo = FakeProvider("api", history=self.stale), FakeProvider("yahoo", history=unavailable())
        history = chain(api, yahoo).get_price_history("AAPL", RANGE)
        assert history.source == "api"
        assert history.last_date == RANGE.start + timedelta(days=1)

    def test_freshness_check_can_be_disabled(self) -> None:
        api, yahoo = FakeProvider("api", history=self.stale), FakeProvider("yahoo")
        assert chain(api, yahoo, staleness=None).get_price_history("AAPL", RANGE).source == "api"
        assert yahoo.calls == []

    def test_gap_within_tolerance_is_complete(self) -> None:
        def short(symbol: str, date_range: DateRange) -> PriceHistory:
            return PriceHistory(symbol, date_range, make_bars(date_range.start, 7))  # ends 3 days early

        api, yahoo = FakeProvider("api", history=short), FakeProvider("yahoo")
        assert chain(api, yahoo).get_price_history("AAPL", RANGE).source == "api"


class TestStatusAndLifecycle:
    def test_status_all_available(self) -> None:
        status = chain(FakeProvider("a"), FakeProvider("b")).check_status()
        assert status.state is HealthState.AVAILABLE
        assert [p.name for p in status.providers] == ["a", "b"]

    def test_status_degraded_and_unavailable(self) -> None:
        degraded = chain(FakeProvider("a"), FakeProvider("b", probe=unavailable())).check_status()
        assert degraded.state is HealthState.DEGRADED
        assert degraded.providers[1].state is HealthState.UNAVAILABLE
        down = chain(FakeProvider("a", probe=RuntimeError("x"))).check_status()
        assert down.state is HealthState.UNAVAILABLE

    def test_close_closes_every_provider(self) -> None:
        a, b = FakeProvider("a"), FakeProvider("b")
        chain(a, b).close()
        assert a.closed and b.closed

    def test_aggregator_is_itself_a_stock_data_source(self) -> None:
        # Composite: an aggregator can be nested as a source in consumer code.
        inner = chain(FakeProvider("inner"))
        assert inner.get_ticker_info("AAPL").source == "inner"
