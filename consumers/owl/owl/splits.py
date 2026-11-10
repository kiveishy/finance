"""Split correction for chart history.

Providers that store history (yahoo-db) keep bars on the price scale they had when downloaded; a
later stock split makes everything before it jump. The corrector compares the bars with Yahoo
Finance's split-adjusted history, finds runs of bars that are off by a split ratio and rescales
them. Detected corrections are saved in owl's database and reused until the next re-check.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Iterable, Protocol

from finance_aggregator.core import PriceBar, PriceHistory

logger = logging.getLogger(__name__)

#: A bar counts as "on another scale" when its close is within this tolerance of a split factor.
FACTOR_TOLERANCE = 0.03
#: Shorter mismatching runs are treated as bad ticks, not as an unadjusted split.
MIN_RUN_BARS = 3


@dataclass(frozen=True)
class ReferenceData:
    """Split-adjusted closes and the split events (date, ratio as reported by Yahoo) in a range."""

    closes: dict[date, float]
    splits: tuple[tuple[date, float], ...]


class SplitReference(Protocol):
    def fetch(self, symbol: str, start: date, end: date) -> ReferenceData | None:
        """None when the reference can't be reached (corrections are then not re-checked)."""


class YahooSplitReference:
    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self._timeout = timeout_seconds

    def fetch(self, symbol: str, start: date, end: date) -> ReferenceData | None:
        import yfinance as yf

        try:
            frame = yf.Ticker(symbol).history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                                              auto_adjust=True, actions=True, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 - yfinance raises many types; the check is best effort
            logger.info("Split check for %s: Yahoo Finance unavailable (%s)", symbol, exc)
            return None
        if frame is None or frame.empty:
            return None
        closes = {ts.date(): float(close) for ts, close in frame["Close"].items() if close == close and close > 0}
        splits: tuple[tuple[date, float], ...] = ()
        if "Stock Splits" in frame:
            splits = tuple((ts.date(), float(r)) for ts, r in frame["Stock Splits"].items() if r and r > 0)
        return ReferenceData(closes, splits)


#: A stored raw close that differs more than this from the current bar means the source data changed.
RAW_CLOSE_TOLERANCE = 1e-4


@dataclass(frozen=True)
class Correction:
    """Bars of `source` dated start..end are `factor` times the split-adjusted price.

    `start_close` / `end_close` are the raw closes the correction was detected on; if the source
    later serves other values (e.g. yahoo-db re-downloaded the history), the correction is stale.
    """

    source: str
    start: date
    end: date
    factor: float
    start_close: float | None = None
    end_close: float | None = None

    def covers(self, bar: PriceBar) -> bool:
        return bar.source == self.source and self.start <= bar.date <= self.end

    def matches(self, bars: dict[tuple[str, date], PriceBar]) -> bool:
        """False when a checkpoint bar is present but no longer has the raw close it was detected on."""
        for day, expected in ((self.start, self.start_close), (self.end, self.end_close)):
            bar = bars.get((self.source, day))
            if bar is None or bar.close is None:
                continue
            if expected is None or abs(bar.close / expected - 1.0) > RAW_CLOSE_TOLERANCE:
                return False
        return True


@dataclass(frozen=True)
class CheckRecord:
    checked_at: float
    verified_from: date
    verified_to: date
    corrections: tuple[Correction, ...]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS split_checks (
    symbol TEXT PRIMARY KEY,
    checked_at REAL NOT NULL,
    verified_from TEXT NOT NULL,
    verified_to TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS split_corrections (
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    factor REAL NOT NULL,
    start_close REAL,
    end_close REAL,
    PRIMARY KEY (symbol, source, start_date)
);
"""


class SplitCorrectionStore:
    """Detected corrections per symbol, in owl's SQLite database."""

    def __init__(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(Path(path), check_same_thread=False, timeout=10)
        with self._lock, self._conn:
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(split_corrections)")}
            if columns and "start_close" not in columns:
                # Older layout without checkpoints: it's only a cache, so start over.
                self._conn.executescript("DROP TABLE split_corrections; DROP TABLE IF EXISTS split_checks;")
            self._conn.executescript(_SCHEMA)

    def load(self, symbol: str) -> CheckRecord | None:
        with self._lock:
            check = self._conn.execute(
                "SELECT checked_at, verified_from, verified_to FROM split_checks WHERE symbol = ?", (symbol,)
            ).fetchone()
            if check is None:
                return None
            rows = self._conn.execute(
                "SELECT source, start_date, end_date, factor, start_close, end_close FROM split_corrections "
                "WHERE symbol = ? ORDER BY start_date",
                (symbol,),
            ).fetchall()
        return CheckRecord(
            check[0], date.fromisoformat(check[1]), date.fromisoformat(check[2]),
            tuple(Correction(s, date.fromisoformat(a), date.fromisoformat(b), f, sc, ec) for s, a, b, f, sc, ec in rows),
        )

    def save(self, symbol: str, record: CheckRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO split_checks(symbol, checked_at, verified_from, verified_to) VALUES (?, ?, ?, ?)",
                (symbol, record.checked_at, record.verified_from.isoformat(), record.verified_to.isoformat()),
            )
            self._conn.execute("DELETE FROM split_corrections WHERE symbol = ?", (symbol,))
            self._conn.executemany(
                "INSERT INTO split_corrections(symbol, source, start_date, end_date, factor, start_close, end_close) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(symbol, c.source, c.start.isoformat(), c.end.isoformat(), c.factor, c.start_close, c.end_close)
                 for c in record.corrections],
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def candidate_factors(splits: Iterable[tuple[date, float]]) -> list[float]:
    """Products of every chronological run of splits: the scale of bars stored before some of them."""
    ratios = [ratio for _, ratio in sorted(splits)]
    factors = set()
    for i in range(len(ratios)):
        product = 1.0
        for ratio in ratios[i:]:
            product *= ratio
            if abs(product - 1.0) > FACTOR_TOLERANCE:
                factors.add(product)
    return sorted(factors)


def _matching_factor(value: float, factors: list[float]) -> float | None:
    return next((f for f in factors if abs(value / f - 1.0) <= FACTOR_TOLERANCE), None)


def detect_corrections(bars: Iterable[PriceBar], reference: ReferenceData) -> tuple[Correction, ...]:
    """Find runs of bars stored on a pre-split price scale.

    Per source, r = stored close / adjusted close. Dividends shift r only gradually; a split that
    happened after the bars were stored makes r jump by the split ratio from one bar to the next.
    Walking back from the newest bar, every such jump multiplies the scale of all older bars.
    """
    factors = candidate_factors(reference.splits)
    if not factors:
        return ()
    by_source: dict[str, list[tuple[date, float, float]]] = {}
    for bar in sorted(bars, key=lambda b: b.date):
        ref = reference.closes.get(bar.date)
        if bar.close and ref:
            by_source.setdefault(bar.source, []).append((bar.date, bar.close / ref, bar.close))

    corrections: list[Correction] = []
    for source, points in by_source.items():
        ratios = [p[1] for p in points]
        # Newest bars: normally already on today's scale, unless the source has nothing after the split.
        scale = _matching_factor(median(ratios[-MIN_RUN_BARS:]), factors) or 1.0
        segment_end = len(points) - 1
        segments: list[tuple[int, int, float]] = []
        for i in range(len(points) - 1, 0, -1):
            jump = _matching_factor(ratios[i - 1] / ratios[i], factors)
            # A single bad tick also "jumps" - require the new level to hold on both sides.
            if jump is None or _matching_factor(
                    median(ratios[max(0, i - MIN_RUN_BARS):i]) / median(ratios[i:i + MIN_RUN_BARS]), factors) != jump:
                continue
            segments.append((i, segment_end, scale))
            scale *= jump
            segment_end = i - 1
        segments.append((0, segment_end, scale))
        for first, last, factor in segments:
            if abs(factor - 1.0) > FACTOR_TOLERANCE and last - first + 1 >= MIN_RUN_BARS:
                corrections.append(Correction(source, points[first][0], points[last][0], factor,
                                              points[first][2], points[last][2]))
    return tuple(sorted(corrections, key=lambda c: (c.source, c.start)))


def apply_corrections(history: PriceHistory, corrections: Iterable[Correction]) -> tuple[PriceHistory, int]:
    corrections = tuple(corrections)
    if not corrections:
        return history, 0
    changed = 0
    bars = []
    for bar in history.bars:
        correction = next((c for c in corrections if c.covers(bar)), None)
        if correction is None:
            bars.append(bar)
            continue
        f = correction.factor
        bars.append(replace(
            bar,
            open=None if bar.open is None else bar.open / f,
            high=None if bar.high is None else bar.high / f,
            low=None if bar.low is None else bar.low / f,
            close=None if bar.close is None else bar.close / f,
            adj_close=None if bar.adj_close is None else bar.adj_close / f,
            volume=None if bar.volume is None else round(bar.volume * f),
        ))
        changed += 1
    return replace(history, bars=tuple(bars)), changed


class SplitCorrector:
    """Makes a history continuous across splits, using (and maintaining) the stored corrections."""

    def __init__(
        self,
        store: SplitCorrectionStore,
        reference: SplitReference,
        *,
        recheck_seconds: float = 24 * 3600,
        reference_source: str = "yahoo-finance",
        clock=time.time,
    ) -> None:
        self._store = store
        self._reference = reference
        self._recheck_seconds = recheck_seconds
        self._reference_source = reference_source
        self._clock = clock
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def correct(self, history: PriceHistory) -> PriceHistory:
        if not history.bars:
            return history
        symbol = history.symbol
        raw = {(b.source, b.date): b for b in history.bars}
        with self._symbol_lock(symbol):
            record = self._store.load(symbol)
            stale = record is not None and not all(c.matches(raw) for c in record.corrections)
            if stale:
                logger.info("%s: source data changed since the last split check - checking again", symbol)
            if stale or self._needs_check(record, history):
                record = self._check(history, record)
        # Never correct data that no longer looks like what was corrected (e.g. already re-downloaded).
        valid = tuple(c for c in record.corrections if c.matches(raw)) if record else ()
        corrected, changed = apply_corrections(history, valid)
        if changed:
            logger.info("%s: rescaled %d bars for splits (%s)", symbol, changed, ", ".join(
                f"{c.source} {c.start}..{c.end} /{c.factor:g}" for c in valid))
        return corrected

    def _needs_check(self, record: CheckRecord | None, history: PriceHistory) -> bool:
        if record is None or self._clock() - record.checked_at >= self._recheck_seconds:
            return True
        checkable = [b.date for b in history.bars if b.source != self._reference_source]
        return bool(checkable) and min(checkable) < record.verified_from

    def _check(self, history: PriceHistory, previous: CheckRecord | None) -> CheckRecord | None:
        # Yahoo's own bars are already adjusted; only the other providers need verifying.
        bars = [b for b in history.bars if b.source != self._reference_source]
        start = min((b.date for b in bars), default=history.bars[0].date)
        if previous is not None:
            start = min(start, previous.verified_from)
        end = history.bars[-1].date
        reference = self._reference.fetch(history.symbol, start, end)
        if reference is None:
            return previous
        record = CheckRecord(self._clock(), start, end, detect_corrections(bars, reference))
        self._store.save(history.symbol, record)
        if record.corrections or (previous and previous.corrections):
            logger.info("%s: split check against Yahoo Finance %s..%s -> %d correction(s)",
                        history.symbol, start, end, len(record.corrections))
        return record

    def _symbol_lock(self, symbol: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(symbol, threading.Lock())
