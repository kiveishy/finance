"""
Stock split handling for the stored history.

Yahoo Finance's history is split-adjusted as of the moment it is downloaded. Rows stored before
a later split stay on the old price scale, so the chart jumps at the split. Every Yahoo download
also carries the split events ('Stock Splits'), which this module uses:

  * a split inside the stored history that is not registered yet -> the stored history is
    re-downloaded from Yahoo (already adjusted) and replaced in one transaction, and the split is
    registered in `split_events`, so later updates don't repeat the work;
  * every ticker gets one full check over its whole stored life (`split_checks`), which also
    repairs tickers broken before this module existed;
  * splits before the first stored day, or inside a fresh initial download, are registered
    without any work: that data was downloaded after the split, so it is already adjusted.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

Split = Tuple[str, float]  # ('YYYY-MM-DD', ratio as reported by Yahoo, e.g. 0.3333 or 4.0)


def splits_in(records: List[Dict[str, Any]]) -> List[Split]:
    """Split events contained in downloaded records."""
    return [(r['date'], float(r['split'])) for r in records if r.get('split')]


class SplitChecker:
    """Keeps a ticker's stored history adjusted for every split Yahoo reports."""

    def __init__(self, db, yahoo_client):
        self.db = db
        self.yahoo = yahoo_client

    def needs_full_check(self, symbol: str) -> bool:
        return not self.db.has_split_check(symbol)

    def store_update(self, symbol: str, records: List[Dict[str, Any]], *, covers_history: bool) -> Dict[str, Any]:
        """
        Store freshly downloaded `records` for an existing ticker, handling splits.

        `covers_history` means `records` were downloaded from the first stored date, so they can
        replace the stored history directly and the one-time full check is done with them.
        """
        first_date = self.db.get_first_date_for_ticker(symbol)
        registered = self.db.get_split_dates(symbol)
        new_splits = [s for s in splits_in(records) if s[0] not in registered]
        # Splits before the first stored day: the stored rows were downloaded later, already adjusted.
        before = [s for s in new_splits if first_date is None or s[0] < first_date]
        inside = [s for s in new_splits if first_date is not None and s[0] >= first_date]
        self.db.record_split_events(symbol, before, 'before_history')

        if not inside:
            count = self.db.insert_historical_data(symbol, records)
            if covers_history:
                self.db.record_split_check(symbol, 'ok')
            return {'records': count, 'action': 'updated', 'splits': []}

        full = records if covers_history else self.yahoo.get_historical_data(symbol, start_date=first_date)
        if not full:
            # Can't re-download now: store the update, leave the split unregistered for the next run.
            logger.warning(f"{symbol}: split(s) {inside} found but the full history could not be downloaded")
            return {'records': self.db.insert_historical_data(symbol, records), 'action': 'split_pending',
                    'splits': inside}
        # A split that happened after the download window (only in the daily window) is in `full` too.
        inside = sorted(set(inside) | {s for s in splits_in(full) if s[0] not in registered and s[0] >= first_date})
        mismatched = self._mismatched_bars(symbol, full)
        if mismatched == 0:
            # Stored rows were downloaded after these splits (already adjusted): nothing to rewrite.
            count = self.db.insert_historical_data(symbol, records)
            self.db.record_split_events(symbol, inside, 'verified')
            if covers_history:
                self.db.record_split_check(symbol, 'ok')
            return {'records': count, 'action': 'verified', 'splits': inside, 'mismatched_bars': 0}
        count = self.db.replace_historical_data(symbol, full)
        if not covers_history:
            # The daily window may extend past the full download (same day); keep its newest rows.
            newer = [r for r in records if r['date'] > max(x['date'] for x in full)]
            count += self.db.insert_historical_data(symbol, newer) if newer else 0
        self.db.record_split_events(symbol, inside, 'redownloaded')
        self.db.record_split_check(symbol, 'redownloaded', mismatched)
        self.db.insert_update_history(ticker_symbol=symbol, update_type='split_redownload',
                                      records_updated=count, status='success',
                                      error_message=f"splits {', '.join(f'{d} x{r:g}' for d, r in inside)}; "
                                                    f"{mismatched} bars were on an old price scale")
        logger.info(f"{symbol}: split(s) {inside} -> history re-downloaded ({count} rows, "
                    f"{mismatched} were on an old price scale)")
        return {'records': count, 'action': 'redownloaded', 'splits': inside, 'mismatched_bars': mismatched}

    def register_initial(self, symbol: str, records: List[Dict[str, Any]]) -> None:
        """A first download is adjusted for every split it contains: just register them."""
        self.db.record_split_events(symbol, splits_in(records), 'initial_download')
        self.db.record_split_check(symbol, 'ok')

    def check(self, symbol: str, *, force: bool = False) -> Dict[str, Any]:
        """Full check of one ticker now (API); `force` re-downloads even without a new split."""
        first_date = self.db.get_first_date_for_ticker(symbol)
        if first_date is None:
            return {'records': 0, 'action': 'no_history', 'splits': []}
        records = self.yahoo.get_historical_data(symbol, start_date=first_date)
        if not records:
            return {'records': 0, 'action': 'download_failed', 'splits': []}
        if not force:
            return self.store_update(symbol, records, covers_history=True)
        mismatched = self._mismatched_bars(symbol, records)
        count = self.db.replace_historical_data(symbol, records)
        splits = splits_in(records)
        self.db.record_split_events(symbol, splits, 'redownloaded')
        self.db.record_split_check(symbol, 'redownloaded', mismatched)
        self.db.insert_update_history(ticker_symbol=symbol, update_type='split_redownload',
                                      records_updated=count, status='success',
                                      error_message=f"forced; {mismatched} bars were on an old price scale")
        return {'records': count, 'action': 'redownloaded', 'splits': splits, 'mismatched_bars': mismatched}

    def _mismatched_bars(self, symbol: str, fresh: List[Dict[str, Any]], tolerance: float = 0.02) -> int:
        """Stored closes that differ from the fresh adjusted closes (for logging only)."""
        stored = self.db.get_closes(symbol)
        mismatched = 0
        for record in fresh:
            old, new = stored.get(record['date']), record.get('close')
            if old and new and abs(old / new - 1.0) > tolerance:
                mismatched += 1
        return mismatched
