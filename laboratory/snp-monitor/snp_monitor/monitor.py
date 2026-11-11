"""Keeps a refreshed snapshot of all S&P 500 members with prices and their price / company-value shares."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from .constituents import Constituent, ConstituentList, ConstituentsProvider, company_name
from .quotes import Quote, QuoteSource

logger = logging.getLogger(__name__)

MIN_FORCED_REFRESH_SECONDS = 10.0
FIRST_SNAPSHOT_WAIT_SECONDS = 60.0


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _share(value: float | None, total: float) -> float | None:
    return value / total * 100 if value is not None and total > 0 else None


def build_snapshot(members: ConstituentList, quotes: dict[str, Quote], now: float) -> dict[str, Any]:
    """Pure: table rows plus the data of both pie charts."""
    rows: list[tuple[Constituent, Quote | None]] = [(c, quotes.get(c.yahoo_symbol)) for c in members.items]

    total_price = sum(q.price for _, q in rows if q and q.price and q.price > 0)

    # Share classes (GOOGL/GOOG, FOXA/FOX, NWSA/NWS) are one company: Yahoo reports the company value on each line.
    companies: dict[str, dict[str, Any]] = {}
    for member, quote in rows:
        entry = companies.setdefault(member.company_key, {
            "name": company_name(member.name), "symbols": [], "sector": member.sector, "market_cap": None,
        })
        entry["symbols"].append(member.symbol)
        cap = quote.market_cap if quote else None
        if cap and cap > 0 and (entry["market_cap"] is None or cap > entry["market_cap"]):
            entry["market_cap"] = cap
    total_cap = sum(c["market_cap"] for c in companies.values() if c["market_cap"])
    for entry in companies.values():
        entry["share"] = _share(entry["market_cap"], total_cap)

    stocks = []
    for member, quote in rows:
        price = quote.price if quote and quote.price and quote.price > 0 else None
        company = companies[member.company_key]
        stocks.append({
            "symbol": member.symbol,
            "name": member.name,
            "sector": member.sector,
            "price": price,
            "change": quote.change if quote else None,
            "change_percent": quote.change_percent if quote else None,
            "market_cap": quote.market_cap if quote else None,
            "price_share": _share(price, total_price),
            "company_share": company["share"],
            "company": company["name"],
            "currency": quote.currency if quote else None,
        })

    changes = [s["change_percent"] for s in stocks if s["change_percent"] is not None]
    states = [q.market_state for _, q in rows if q and q.market_state]
    last_trade = max((q.time for _, q in rows if q and q.time), default=None)
    return {
        "updated_at": _iso(now),
        "last_trade_at": _iso(last_trade) if last_trade else None,
        "market_state": max(set(states), key=states.count) if states else None,
        "constituents": {"count": len(members.items), "source": members.source, "fetched_at": _iso(members.fetched_at)},
        "summary": {
            "priced": sum(1 for s in stocks if s["price"] is not None),
            "companies": len(companies),
            "advancers": sum(1 for c in changes if c > 0),
            "decliners": sum(1 for c in changes if c < 0),
            "total_price": total_price,
            "total_market_cap": total_cap,
        },
        "stocks": stocks,
        "companies": sorted(
            ({"name": c["name"], "symbols": c["symbols"], "sector": c["sector"],
              "market_cap": c["market_cap"], "share": c["share"]} for c in companies.values()),
            key=lambda c: c["market_cap"] or 0, reverse=True,
        ),
    }


class SnpMonitor:
    """Refreshes the snapshot in a background thread; requests are answered from memory."""

    def __init__(
        self,
        constituents: ConstituentsProvider,
        quotes: QuoteSource,
        *,
        refresh_seconds: float = 60.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._constituents = constituents
        self._quotes = quotes
        self._refresh_seconds = refresh_seconds
        self._clock = clock
        self._lock = threading.Lock()  # one refresh at a time
        self._ready = threading.Condition()
        self._snapshot: dict[str, Any] | None = None
        self._error: str | None = None
        self._last_attempt = 0.0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="snp-refresh", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def refresh(self) -> dict[str, Any]:
        """Download the member list (if stale) and all quotes now; returns the new snapshot."""
        with self._lock:
            self._last_attempt = self._clock()
            try:
                members = self._constituents.get()
                symbols: Sequence[str] = [c.yahoo_symbol for c in members.items]
                started = time.monotonic()
                quotes = self._quotes.fetch(symbols)
                snapshot = build_snapshot(members, quotes, self._clock())
            except Exception as exc:
                logger.warning("S&P 500 refresh failed: %s", exc)
                with self._ready:
                    self._error = str(exc)
                    self._ready.notify_all()
                raise
            logger.info("S&P 500 refreshed: %d/%d priced in %.1f s", snapshot["summary"]["priced"],
                        len(symbols), time.monotonic() - started)
            with self._ready:
                self._snapshot, self._error = snapshot, None
                self._ready.notify_all()
            return snapshot

    def request_refresh(self) -> bool:
        """Ask the background thread for an early refresh; False if the last one was too recent."""
        if self._clock() - self._last_attempt < MIN_FORCED_REFRESH_SECONDS:
            return False
        self._wake.set()
        return True

    def snapshot(self, *, wait_seconds: float = FIRST_SNAPSHOT_WAIT_SECONDS) -> dict[str, Any]:
        """Latest snapshot (with refresh status); waits for the first one after start-up."""
        with self._ready:
            self._ready.wait_for(lambda: self._snapshot is not None or self._error is not None, timeout=wait_seconds)
            if self._snapshot is None:
                raise RuntimeError(self._error or "S&P 500 data is still loading")
            return {**self._snapshot, "error": self._error, "refresh_seconds": self._refresh_seconds}

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:  # noqa: BLE001 - logged in refresh(); retried next cycle
                pass
            self._wake.wait(self._refresh_seconds)
            self._wake.clear()
