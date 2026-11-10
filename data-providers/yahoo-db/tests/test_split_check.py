"""Split handling tests (run: python -m pytest tests -q from the yahoo-db folder)."""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import DatabaseManager  # noqa: E402
from split_check import SplitChecker  # noqa: E402

SPLIT_DAY = "2026-10-06"


def _days(start: str, n: int) -> list:
    days, day = [], date.fromisoformat(start)
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _record(day: str, close: float, volume: int = 1000, split: float = 0.0) -> dict:
    return {"date": day, "open": close, "high": close, "low": close, "close": close,
            "adj_close": close, "volume": volume, "split": split}


def _adjusted(day: str) -> float:
    return 60.0 + (date.fromisoformat(day) - date(2026, 9, 1)).days * 0.1


class FakeYahoo:
    """Yahoo's current, split-adjusted view: every day on today's scale, the split on its day."""

    def __init__(self, days: list) -> None:
        self.days = days
        self.calls = []

    def get_historical_data(self, symbol, start_date=None, end_date=None, period=None):
        self.calls.append(start_date or period)
        days = [d for d in self.days if start_date is None or d >= start_date]
        if start_date is None:
            days = days[-10:]  # the daily "1mo" window
        return [_record(d, _adjusted(d), split=(1 / 3 if d == SPLIT_DAY else 0.0)) for d in days]


@pytest.fixture
def db(tmp_path):
    manager = DatabaseManager(str(tmp_path / "stocks.db"))
    manager.connect()
    manager.initialize_tables()
    manager.insert_ticker({"symbol": "ETHA"})
    yield manager
    manager.shutdown()


def _store_broken_history(db, days):
    """Like ETHA on the server: rows before the re-adjusted window still at 1/3 of the price, 3x volume."""
    db.insert_historical_data("ETHA", [
        _record(d, _adjusted(d) / 3, 3000) if d < "2026-09-22" else _record(d, _adjusted(d))
        for d in days if d < SPLIT_DAY
    ])


def test_full_check_redownloads_history_with_an_unhandled_split_and_registers_it(db):
    days = _days("2026-09-01", 40)
    _store_broken_history(db, days)
    yahoo = FakeYahoo(days)
    checker = SplitChecker(db, yahoo)

    assert checker.needs_full_check("ETHA")
    first = db.get_first_date_for_ticker("ETHA")
    result = checker.store_update("ETHA", yahoo.get_historical_data("ETHA", start_date=first), covers_history=True)

    assert result["action"] == "redownloaded" and result["splits"] == [(SPLIT_DAY, pytest.approx(1 / 3))]
    assert result["mismatched_bars"] == sum(d < "2026-09-22" for d in days)
    closes = db.get_closes("ETHA")
    assert all(closes[d] == pytest.approx(_adjusted(d)) for d in days)  # whole history on today's scale
    assert db.get_split_dates("ETHA") == {SPLIT_DAY}
    assert db.get_split_check("ETHA")["result"] == "redownloaded"
    assert not checker.needs_full_check("ETHA")


def test_handled_split_is_not_fixed_again_on_the_next_update(db):
    days = _days("2026-09-01", 40)
    _store_broken_history(db, days)
    yahoo = FakeYahoo(days)
    checker = SplitChecker(db, yahoo)
    checker.store_update("ETHA", yahoo.get_historical_data("ETHA", start_date=days[0]), covers_history=True)

    # Next day's update still contains the split day in its window: registered -> plain update.
    window = [r for r in yahoo.get_historical_data("ETHA", start_date="2026-10-01")]
    calls = len(yahoo.calls)
    result = checker.store_update("ETHA", window, covers_history=False)
    assert result["action"] == "updated"
    assert len(yahoo.calls) == calls  # no extra download
    history = db.get_update_history("ETHA", limit=10)
    assert sum(h["update_type"] == "split_redownload" for h in history) == 1


def test_split_seen_in_the_daily_window_triggers_a_full_redownload(db):
    days = _days("2026-09-01", 40)
    _store_broken_history(db, days)
    db.record_split_check("ETHA", "ok")  # full check already done before the split happened
    yahoo = FakeYahoo(days)
    checker = SplitChecker(db, yahoo)

    window = yahoo.get_historical_data("ETHA", start_date="2026-10-01")
    result = checker.store_update("ETHA", window, covers_history=False)
    assert result["action"] == "redownloaded"
    assert yahoo.calls[-1] == days[0]  # downloaded again from the first stored day
    assert db.get_closes("ETHA")[days[0]] == pytest.approx(_adjusted(days[0]))


def test_split_before_the_stored_history_needs_no_work(db):
    days = _days("2026-10-07", 20)  # stored only after the split: already adjusted
    db.insert_historical_data("ETHA", [_record(d, _adjusted(d)) for d in days])
    yahoo = FakeYahoo(_days("2026-09-01", 60))
    checker = SplitChecker(db, yahoo)
    result = checker.store_update("ETHA", yahoo.get_historical_data("ETHA", start_date="2026-09-01"),
                                  covers_history=True)
    assert result["action"] == "updated"
    assert [e["action"] for e in db.get_split_events("ETHA")] == ["before_history"]


def test_failed_redownload_keeps_old_rows_and_leaves_the_split_pending(db):
    days = _days("2026-09-01", 40)
    _store_broken_history(db, days)
    db.record_split_check("ETHA", "ok")

    class Offline(FakeYahoo):
        def get_historical_data(self, symbol, start_date=None, end_date=None, period=None):
            return [] if start_date == days[0] else super().get_historical_data(symbol, start_date, end_date, period)

    yahoo = Offline(days)
    checker = SplitChecker(db, yahoo)
    result = checker.store_update("ETHA", yahoo.get_historical_data("ETHA", start_date="2026-10-01"),
                                  covers_history=False)
    assert result["action"] == "split_pending"
    assert db.get_split_dates("ETHA") == set()  # retried on the next update
    assert db.get_closes("ETHA")[days[0]] == pytest.approx(_adjusted(days[0]) / 3)


def test_initial_download_registers_its_splits(db):
    yahoo = FakeYahoo(_days("2026-09-01", 40))
    records = yahoo.get_historical_data("ETHA", start_date="2026-09-01")
    db.insert_historical_data("ETHA", records)
    SplitChecker(db, yahoo).register_initial("ETHA", records)
    assert db.get_split_dates("ETHA") == {SPLIT_DAY}
    assert db.get_split_check("ETHA")["result"] == "ok"


def test_split_inside_already_adjusted_history_is_only_verified(db):
    days = _days("2026-09-01", 40)
    yahoo = FakeYahoo(days)
    db.insert_historical_data("ETHA", yahoo.get_historical_data("ETHA", start_date=days[0]))  # correct data
    result = SplitChecker(db, yahoo).store_update(
        "ETHA", yahoo.get_historical_data("ETHA", start_date=days[0]), covers_history=True)
    assert result["action"] == "verified" and result["mismatched_bars"] == 0
    assert [e["action"] for e in db.get_split_events("ETHA")] == ["verified"]
    assert not any(h["update_type"] == "split_redownload" for h in db.get_update_history("ETHA"))
