"""Current price per symbol for the watchlist (last close and change), cached briefly."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any, Callable, Iterable

from finance_aggregator.core import (
    DataNotFoundError,
    DateRange,
    FinanceError,
    InvalidRequestError,
    normalize_symbol,
)

from .charts import _quote
from .market_data import MarketData

logger = logging.getLogger(__name__)

MAX_SYMBOLS = 200
#: Enough calendar days to contain two trading days across weekends and holidays.
LOOKBACK_DAYS = 10


class QuoteService:
    """Last close, previous close and change for many symbols, fetched in parallel."""

    def __init__(
        self,
        market: MarketData,
        *,
        ttl_seconds: float = 60.0,
        max_workers: int = 6,
        today: Callable[[], date] = date.today,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._market = market
        self._ttl = ttl_seconds
        self._max_workers = max_workers
        self._today = today
        self._clock = clock
        self._cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
        self._lock = threading.Lock()
        self._pool: ThreadPoolExecutor | None = None

    def quotes(self, symbols: Iterable[str]) -> dict[str, dict[str, Any] | None]:
        """Quote per symbol; None when there is no price data for it."""
        wanted = list(dict.fromkeys(normalize_symbol(s) for s in symbols))
        if len(wanted) > MAX_SYMBOLS:
            raise InvalidRequestError(f"At most {MAX_SYMBOLS} symbols per request")
        now = self._clock()
        with self._lock:
            result = {s: self._cache[s][1] for s in wanted if s in self._cache and now - self._cache[s][0] < self._ttl}
        missing = [s for s in wanted if s not in result]
        if missing:
            for symbol, quote, cacheable in self._executor().map(self._fetch, missing):
                result[symbol] = quote
                if cacheable:
                    with self._lock:
                        self._cache[symbol] = (now, quote)
        return {s: result[s] for s in wanted}

    def _fetch(self, symbol: str) -> tuple[str, dict[str, Any] | None, bool]:
        today = self._today()
        try:
            history = self._market.get_price_history(symbol, DateRange(today - timedelta(days=LOOKBACK_DAYS), today))
        except DataNotFoundError:
            return symbol, None, True
        except (FinanceError, OSError) as exc:  # provider down: report "no price" now, retry next time
            logger.info("Quote for %s unavailable: %s", symbol, exc)
            return symbol, None, False
        bars = [b for b in history.bars if b.close is not None]
        return symbol, (_quote(bars) if bars else None), True

    def _executor(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="quotes")
            return self._pool

    def close(self) -> None:
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
