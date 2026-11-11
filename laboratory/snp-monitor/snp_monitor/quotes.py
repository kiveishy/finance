"""Batch stock quotes (price, day change, market cap) from Yahoo Finance."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

logger = logging.getLogger(__name__)

QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
CHUNK_SIZE = 150


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float | None
    previous_close: float | None = None
    change: float | None = None
    change_percent: float | None = None
    market_cap: float | None = None
    currency: str | None = None
    market_state: str | None = None
    time: int | None = None  # epoch seconds of the last trade


class QuoteSource(Protocol):
    def fetch(self, symbols: Sequence[str]) -> dict[str, Quote]: ...


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:  # NaN
        return None
    return float(value)


def parse_quote(raw: Mapping[str, Any]) -> Quote:
    price = _number(raw.get("regularMarketPrice"))
    previous = _number(raw.get("regularMarketPreviousClose"))
    change = _number(raw.get("regularMarketChange"))
    percent = _number(raw.get("regularMarketChangePercent"))
    if change is None and price is not None and previous:
        change = price - previous
        percent = change / previous * 100
    market_time = raw.get("regularMarketTime")
    return Quote(
        symbol=str(raw.get("symbol", "")).upper(),
        price=price,
        previous_close=previous,
        change=change,
        change_percent=percent,
        market_cap=_number(raw.get("marketCap")),
        currency=raw.get("currency") or None,
        market_state=raw.get("marketState") or None,
        time=int(market_time) if isinstance(market_time, (int, float)) else None,
    )


def _yahoo_get_json(url: str, params: Mapping[str, str], timeout: float) -> Any:
    # yfinance's session handles Yahoo's cookie/crumb handshake required by the quote endpoint.
    from yfinance.data import YfData

    return YfData().get_raw_json(url, params=dict(params), timeout=timeout)


class YahooQuoteSource:
    def __init__(self, *, timeout: float = 20.0, chunk_size: int = CHUNK_SIZE,
                 get_json: Callable[[str, Mapping[str, str], float], Any] = _yahoo_get_json) -> None:
        self._timeout = timeout
        self._chunk_size = chunk_size
        self._get_json = get_json

    def fetch(self, symbols: Sequence[str]) -> dict[str, Quote]:
        quotes: dict[str, Quote] = {}
        failures: list[str] = []
        for start in range(0, len(symbols), self._chunk_size):
            chunk = symbols[start:start + self._chunk_size]
            try:
                payload = self._get_json(QUOTE_URL, {"symbols": ",".join(chunk)}, self._timeout)
                results = payload["quoteResponse"]["result"] or []
            except Exception as exc:  # noqa: BLE001 - one failed chunk must not hide the others
                failures.append(f"{chunk[0]}..{chunk[-1]}: {exc}")
                continue
            for raw in results:
                quote = parse_quote(raw)
                if quote.symbol:
                    quotes[quote.symbol] = quote
        if failures:
            logger.warning("Quote download failed for %d chunk(s): %s", len(failures), "; ".join(failures))
            if not quotes:
                raise RuntimeError("Yahoo Finance quotes unavailable: " + failures[0])
        return quotes
