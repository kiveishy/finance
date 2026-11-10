from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from finance_aggregator.core import DateRange, PriceBar, PriceHistory
from owl.splits import (
    ReferenceData,
    SplitCorrectionStore,
    SplitCorrector,
    apply_corrections,
    candidate_factors,
    detect_corrections,
)

START = date(2026, 9, 1)
SPLIT_DAY = date(2026, 9, 21)  # first day the stored data is on the new scale (like ETHA)


def _days(n: int) -> list[date]:
    days, day = [], START
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _adjusted(day: date) -> float:
    return 60.0 + (day - START).days * 0.1


def _bar(day: date, close: float, volume: int = 1000, source: str = "yahoo-db-api") -> PriceBar:
    return PriceBar(day, close, close * 1.01, close * 0.99, close, close, volume, source)


def _etha_like(days: list[date], source: str = "yahoo-db-api") -> PriceHistory:
    """Bars before SPLIT_DAY stored at 1/3 of the adjusted price with 3x volume."""
    bars = [
        _bar(d, _adjusted(d) / 3, 3000, source) if d < SPLIT_DAY else _bar(d, _adjusted(d), 1000, source)
        for d in days
    ]
    return PriceHistory("ETHA", DateRange(days[0], days[-1]), tuple(bars), source)


def _reference(days: list[date], splits=((date(2026, 10, 6), 1 / 3),)) -> ReferenceData:
    return ReferenceData({d: _adjusted(d) for d in days}, tuple(splits))


class FakeReference:
    def __init__(self, data: ReferenceData | None) -> None:
        self.data = data
        self.calls: list[tuple[str, date, date]] = []

    def fetch(self, symbol: str, start: date, end: date) -> ReferenceData | None:
        self.calls.append((symbol, start, end))
        return self.data


def test_candidate_factors_combine_consecutive_splits() -> None:
    assert candidate_factors([]) == []
    factors = candidate_factors([(date(2020, 1, 1), 2.0), (date(2021, 1, 1), 3.0)])
    assert factors == pytest.approx([2.0, 3.0, 6.0])


def test_detects_unadjusted_bars_before_the_split_and_rescales_them() -> None:
    days = _days(30)
    history = _etha_like(days)
    corrections = detect_corrections(history.bars, _reference(days))
    last_old = max(d for d in days if d < SPLIT_DAY)
    assert [(c.source, c.start, c.end) for c in corrections] == [("yahoo-db-api", days[0], last_old)]
    assert corrections[0].factor == pytest.approx(1 / 3)
    assert corrections[0].start_close == pytest.approx(_adjusted(days[0]) / 3)  # raw close it was detected on
    fixed, changed = apply_corrections(history, corrections)
    assert changed == sum(d < SPLIT_DAY for d in days)
    for bar in fixed.bars:
        assert bar.close == pytest.approx(_adjusted(bar.date))
        assert bar.volume == 1000
    closes = [b.close for b in fixed.bars]
    assert max(abs(b / a - 1) for a, b in zip(closes, closes[1:])) < 0.01  # no jump left


def test_gradual_dividend_drift_and_single_bad_ticks_are_left_alone() -> None:
    days = _days(30)
    drift = [_bar(d, _adjusted(d) * (1 + 0.002 * i)) for i, d in enumerate(days)]
    drift[10] = _bar(days[10], _adjusted(days[10]) / 3)  # one bad tick at the old scale
    history = PriceHistory("X", DateRange(days[0], days[-1]), tuple(drift))
    assert detect_corrections(history.bars, _reference(days)) == ()


def test_no_splits_means_no_corrections() -> None:
    days = _days(30)
    assert detect_corrections(_etha_like(days).bars, _reference(days, splits=())) == ()


def test_corrector_saves_and_reuses_corrections(tmp_path: Path) -> None:
    days = _days(30)
    now = [1000.0]
    store = SplitCorrectionStore(tmp_path / "owl.db")
    reference = FakeReference(_reference(days))
    corrector = SplitCorrector(store, reference, recheck_seconds=3600, clock=lambda: now[0])

    fixed = corrector.correct(_etha_like(days))
    assert fixed.bars[0].close == pytest.approx(_adjusted(days[0]))
    assert len(reference.calls) == 1

    # Within the re-check interval the stored correction is used without asking Yahoo again,
    # also by a new corrector (i.e. after an owl restart).
    reference.data = None
    again = SplitCorrector(SplitCorrectionStore(tmp_path / "owl.db"), reference, recheck_seconds=3600,
                           clock=lambda: now[0]).correct(_etha_like(days))
    assert again.bars[0].close == pytest.approx(_adjusted(days[0]))
    assert len(reference.calls) == 1

    # Older bars than verified, or an expired check, trigger a new check; an unreachable
    # reference keeps the stored corrections.
    now[0] += 7200
    assert corrector.correct(_etha_like(days)).bars[0].close == pytest.approx(_adjusted(days[0]))
    assert len(reference.calls) == 2
    store.close()


def test_no_double_correction_once_the_source_is_fixed(tmp_path: Path) -> None:
    """yahoo-db re-downloads the history within the re-check window: the saved /3 must not be applied again."""
    days = _days(30)
    now = [1000.0]
    store = SplitCorrectionStore(tmp_path / "owl.db")
    reference = FakeReference(_reference(days))
    corrector = SplitCorrector(store, reference, recheck_seconds=24 * 3600, clock=lambda: now[0])
    corrector.correct(_etha_like(days))
    assert len(store.load("ETHA").corrections) == 1

    fixed_source = PriceHistory("ETHA", DateRange(days[0], days[-1]),
                                tuple(_bar(d, _adjusted(d)) for d in days), "yahoo-db-api")
    now[0] += 60  # well inside the 24 h re-check window
    result = corrector.correct(fixed_source)
    assert [b.close for b in result.bars] == [b.close for b in fixed_source.bars]
    assert len(reference.calls) == 2  # re-checked at once because the raw data changed
    assert store.load("ETHA").corrections == ()


def test_stale_correction_is_not_applied_even_when_yahoo_is_unreachable(tmp_path: Path) -> None:
    days = _days(30)
    store = SplitCorrectionStore(tmp_path / "owl.db")
    reference = FakeReference(_reference(days))
    corrector = SplitCorrector(store, reference)
    corrector.correct(_etha_like(days))
    reference.data = None
    fixed_source = PriceHistory("ETHA", DateRange(days[0], days[-1]),
                                tuple(_bar(d, _adjusted(d)) for d in days), "yahoo-db-api")
    assert [b.close for b in corrector.correct(fixed_source).bars] == [b.close for b in fixed_source.bars]


def test_old_cache_layout_is_rebuilt(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "owl.db"
    conn = sqlite3.connect(db)
    with conn:
        conn.executescript(
            "CREATE TABLE split_checks (symbol TEXT PRIMARY KEY, checked_at REAL, verified_from TEXT, verified_to TEXT);"
            "CREATE TABLE split_corrections (symbol TEXT, source TEXT, start_date TEXT, end_date TEXT, factor REAL);"
            "INSERT INTO split_checks VALUES ('ETHA', 1, '2024-01-01', '2026-01-01');"
            "INSERT INTO split_corrections VALUES ('ETHA', 'yahoo-db-api', '2024-01-01', '2025-01-01', 0.333);")
    conn.close()
    store = SplitCorrectionStore(db)
    assert store.load("ETHA") is None  # old entries without checkpoints are dropped
    store.close()


def test_reference_provider_bars_are_not_checked_or_changed(tmp_path: Path) -> None:
    days = _days(30)
    store = SplitCorrectionStore(tmp_path / "owl.db")
    reference = FakeReference(_reference(days))
    yahoo_only = PriceHistory("ETHA", DateRange(days[0], days[-1]),
                              tuple(_bar(d, _adjusted(d), source="yahoo-finance") for d in days))
    fixed = SplitCorrector(store, reference).correct(yahoo_only)
    assert fixed.bars == yahoo_only.bars
    store.close()


def test_chart_service_applies_the_corrector(tmp_path: Path) -> None:
    from fakes import FakeMarket
    from owl.charts import ChartRequest, ChartService

    days = _days(30)
    market = FakeMarket({"ETHA": list(_etha_like(days).bars)})
    store = SplitCorrectionStore(tmp_path / "owl.db")
    service = ChartService(market, today=lambda: days[-1],
                           corrector=SplitCorrector(store, FakeReference(_reference(days))).correct)
    chart = service.build(ChartRequest.parse("ETHA", "3M", "D", None))
    assert chart["bars"][0]["close"] == pytest.approx(_adjusted(days[0]))
    store.close()
