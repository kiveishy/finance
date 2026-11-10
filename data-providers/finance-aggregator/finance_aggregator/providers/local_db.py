"""Read-only access to a locally reachable yahoo-db SQLite file (highest priority source).

yahoo-db's own `DatabaseManager` is not reused here on purpose: it opens the file read-write
(creating directories/files when missing) and swallows SQLite errors into empty results, which
would make "not found" indistinguishable from "database broken". This provider only issues two
SELECTs against the same schema, in SQLite read-only mode, so it never interferes with the
yahoo-db writer.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from ..core.errors import (
    ConfigurationError,
    DataNotFoundError,
    ProviderError,
    ProviderUnavailableError,
)
from ..core.interfaces import DataProvider
from ..core.models import TICKER_INFO_FIELDS, DateRange, PriceBar, PriceHistory, TickerInfo
from ..core.search import SymbolMatch
from .registry import ProviderContext, option, register_provider

logger = logging.getLogger(__name__)

_INFO_SQL = f"SELECT {', '.join(TICKER_INFO_FIELDS)} FROM tickers WHERE symbol = ?"
_HISTORY_SQL = """
    SELECT date, open_price AS open, high_price AS high, low_price AS low,
           close_price AS close, adj_close, volume
    FROM historical_data
    WHERE ticker_symbol = ? AND date BETWEEN ? AND ?
    ORDER BY date ASC
"""
# Same ranking as yahoo-db's DatabaseManager.search_tickers (read-only connection here).
_SEARCH_SQL = r"""
    SELECT symbol, COALESCE(long_name, short_name) AS name, exchange, quote_type,
           CASE
               WHEN symbol = :exact THEN 100
               WHEN symbol LIKE :prefix ESCAPE '\' THEN 90
               WHEN long_name LIKE :prefix ESCAPE '\' OR short_name LIKE :prefix ESCAPE '\' THEN 75
               WHEN long_name LIKE :word ESCAPE '\' OR short_name LIKE :word ESCAPE '\' THEN 65
               WHEN symbol LIKE :contains ESCAPE '\' THEN 50
               ELSE 45
           END AS score
    FROM tickers
    WHERE symbol LIKE :contains ESCAPE '\'
       OR short_name LIKE :contains ESCAPE '\'
       OR long_name LIKE :contains ESCAPE '\'
    ORDER BY score DESC, length(symbol), symbol
    LIMIT :limit
"""


def sqlite_readonly_uri(path: Path) -> str:
    """`file:` URI with an empty authority; works for drive, UNC and POSIX paths."""
    posix = path.resolve().as_posix()
    if not posix.startswith("/"):
        posix = "/" + posix
    return f"file://{quote(posix, safe='/:')}?mode=ro"


@register_provider
class YahooDbLocalProvider(DataProvider):
    kind = "yahoo_db_local"
    supports_search = True

    def __init__(self, db_path: Path, *, name: str | None = None, timeout_seconds: float = 5.0) -> None:
        super().__init__(name)
        self._db_path = Path(db_path)
        self._timeout = timeout_seconds

    @classmethod
    def from_config(
        cls, name: str, options: Mapping[str, Any], context: ProviderContext
    ) -> "YahooDbLocalProvider":
        raw_path = options.get("db_path")
        if not raw_path:
            raise ConfigurationError(f"Provider {name!r}: option 'db_path' is required")
        return cls(
            context.resolve_path(raw_path),
            name=name,
            timeout_seconds=option(options, "timeout_seconds", 5.0, float),
        )

    @property
    def db_path(self) -> Path:
        return self._db_path

    def is_available(self) -> bool:
        return self._db_path.is_file()

    def unavailable_reason(self) -> str:
        return f"database file not accessible: {self._db_path}"

    # ---- hooks -------------------------------------------------------------------------------

    def _fetch_ticker_info(self, symbol: str) -> TickerInfo:
        row = self._query_one(_INFO_SQL, (symbol,))
        if row is None:
            raise DataNotFoundError(f"Ticker {symbol} not found in local database", provider=self.name)
        return TickerInfo.from_mapping(dict(row))

    def _fetch_price_history(self, symbol: str, date_range: DateRange) -> PriceHistory:
        rows = self._query_all(
            _HISTORY_SQL, (symbol, date_range.start.isoformat(), date_range.end.isoformat())
        )
        return PriceHistory(symbol, date_range, tuple(PriceBar.from_mapping(dict(r)) for r in rows))

    def _search(self, query: str, limit: int) -> list[SymbolMatch]:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self._query_all(
            _SEARCH_SQL,
            {
                "exact": query.upper(),
                "prefix": f"{escaped}%",
                "word": f"% {escaped}%",
                "contains": f"%{escaped}%",
                "limit": limit,
            },
        )
        return [
            SymbolMatch(r["symbol"], r["name"], r["exchange"], r["quote_type"], score=float(r["score"]))
            for r in rows
        ]

    def _probe(self) -> tuple[str, Mapping[str, Any]]:
        tickers = self._query_one("SELECT COUNT(*) AS n FROM tickers", ())
        latest = self._query_one("SELECT MAX(date) AS d FROM historical_data", ())
        details = {
            "path": str(self._db_path),
            "ticker_count": tickers["n"] if tickers else 0,
            "latest_price_date": latest["d"] if latest else None,
        }
        return f"{details['ticker_count']} tickers, latest price {details['latest_price_date']}", details

    # ---- sqlite helpers ----------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if not self.is_available():
            raise ProviderUnavailableError(self.unavailable_reason(), provider=self.name)
        connection = sqlite3.connect(
            sqlite_readonly_uri(self._db_path), uri=True, timeout=self._timeout, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        return connection

    def _query_one(self, sql: str, params: tuple[Any, ...] | dict[str, Any]) -> sqlite3.Row | None:
        try:
            with closing(self._connect()) as conn:
                return conn.execute(sql, params).fetchone()
        except sqlite3.Error as exc:
            raise self._translate(exc) from exc

    def _query_all(self, sql: str, params: tuple[Any, ...] | dict[str, Any]) -> list[sqlite3.Row]:
        try:
            with closing(self._connect()) as conn:
                return conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise self._translate(exc) from exc

    def _translate(self, exc: sqlite3.Error) -> ProviderError:
        message = str(exc).lower()
        if "unable to open" in message or "locked" in message or "busy" in message:
            return ProviderUnavailableError(f"local database not usable: {exc}", provider=self.name)
        if "no such table" in message or "no such column" in message:
            return ProviderError(
                f"{self._db_path} does not look like a yahoo-db database: {exc}", provider=self.name
            )
        return ProviderError(f"local database error: {exc}", provider=self.name)
