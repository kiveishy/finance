"""User watchlist: named groups of ticker symbols, persisted in SQLite."""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from finance_aggregator.core import normalize_symbol

from .errors import ConflictError, ResourceNotFoundError, ValidationError

MAX_GROUP_NAME = 64
MAX_SYMBOLS_PER_GROUP = 500
MAX_SETTING_BYTES = 64 * 1024
DEFAULT_GROUP = "Watchlist"
DEFAULT_SYMBOLS = ("SPY", "QQQ", "AAPL", "MSFT")


@dataclass(frozen=True)
class WatchItem:
    symbol: str
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "name": self.name}


@dataclass(frozen=True)
class Group:
    id: int
    name: str
    items: tuple[WatchItem, ...] = field(default_factory=tuple)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "items": [i.to_dict() for i in self.items]}


def validate_group_name(raw: Any) -> str:
    name = str(raw or "").strip()
    if not name:
        raise ValidationError("Group name must not be empty")
    if len(name) > MAX_GROUP_NAME:
        raise ValidationError(f"Group name must be at most {MAX_GROUP_NAME} characters")
    return name


class WatchlistRepository(ABC):
    """Storage contract for groups and settings (swap for another backend without touching the API)."""

    @abstractmethod
    def list_groups(self) -> list[Group]: ...

    @abstractmethod
    def get_group(self, group_id: int) -> Group: ...

    @abstractmethod
    def create_group(self, name: str, symbols: Iterable[WatchItem] = ()) -> Group: ...

    @abstractmethod
    def rename_group(self, group_id: int, name: str) -> Group: ...

    @abstractmethod
    def delete_group(self, group_id: int) -> None: ...

    @abstractmethod
    def add_item(self, group_id: int, item: WatchItem) -> Group: ...

    @abstractmethod
    def remove_item(self, group_id: int, symbol: str) -> Group: ...

    @abstractmethod
    def move_item(
        self, from_group_id: int, symbol: str, to_group_id: int, *, copy: bool = False, before: str | None = None
    ) -> tuple[Group, Group]:
        """Move (or copy) a symbol to another group, placed before `before` (default: at the end).

        With the same source and target group this reorders the symbol inside the group.
        Returns the (source, target) groups after the change.
        """

    @abstractmethod
    def set_symbol_name(self, symbol: str, name: str) -> None:
        """Store the display name of a symbol in every group containing it."""

    def symbols_without_name(self) -> list[str]:
        return sorted({i.symbol for g in self.list_groups() for i in g.items if not i.name})

    @abstractmethod
    def get_setting(self, key: str) -> Any: ...

    @abstractmethod
    def set_setting(self, key: str, value: Any) -> None: ...

    def close(self) -> None:
        """Release resources."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS group_items (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    name TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (group_id, symbol)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Split correction moved to yahoo-db; remove the tables older owl versions created.
DROP TABLE IF EXISTS split_corrections;
DROP TABLE IF EXISTS split_checks;
"""


class SqliteWatchlistRepository(WatchlistRepository):
    def __init__(self, path: Path, *, seed_defaults: bool = True) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            empty = self._conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0] == 0
            initialised = self._conn.execute("SELECT 1 FROM settings WHERE key = '_initialised'").fetchone()
            if empty and not initialised and seed_defaults:
                self._insert_group(DEFAULT_GROUP, [WatchItem(s) for s in DEFAULT_SYMBOLS])
            self._conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('_initialised', 'true')")

    # ---- groups ------------------------------------------------------------------------------

    def list_groups(self) -> list[Group]:
        with self._lock:
            rows = self._conn.execute("SELECT id, name FROM groups ORDER BY position, id").fetchall()
            return [self._load_group(row["id"], row["name"]) for row in rows]

    def get_group(self, group_id: int) -> Group:
        with self._lock:
            return self._group_or_404(group_id)

    def create_group(self, name: str, symbols: Iterable[WatchItem] = ()) -> Group:
        name = validate_group_name(name)
        with self._lock, self._conn:
            group_id = self._insert_group(name, list(symbols))
            return self._group_or_404(group_id)

    def rename_group(self, group_id: int, name: str) -> Group:
        name = validate_group_name(name)
        with self._lock, self._conn:
            self._group_or_404(group_id)
            try:
                self._conn.execute("UPDATE groups SET name = ? WHERE id = ?", (name, group_id))
            except sqlite3.IntegrityError as exc:
                raise ConflictError(f"A group named {name!r} already exists") from exc
            return self._group_or_404(group_id)

    def delete_group(self, group_id: int) -> None:
        with self._lock, self._conn:
            if self._conn.execute("DELETE FROM groups WHERE id = ?", (group_id,)).rowcount == 0:
                raise ResourceNotFoundError(f"Group {group_id} not found")

    def add_item(self, group_id: int, item: WatchItem) -> Group:
        symbol = normalize_symbol(item.symbol)
        with self._lock, self._conn:
            group = self._group_or_404(group_id)
            if symbol in group.symbols:
                raise ConflictError(f"{symbol} is already in group {group.name!r}")
            if len(group.items) >= MAX_SYMBOLS_PER_GROUP:
                raise ValidationError(f"A group can hold at most {MAX_SYMBOLS_PER_GROUP} symbols")
            self._conn.execute(
                "INSERT INTO group_items(group_id, symbol, name, position) VALUES (?, ?, ?, ?)",
                (group_id, symbol, item.name, len(group.items)),
            )
            return self._group_or_404(group_id)

    def remove_item(self, group_id: int, symbol: str) -> Group:
        symbol = normalize_symbol(symbol)
        with self._lock, self._conn:
            group = self._group_or_404(group_id)
            deleted = self._conn.execute(
                "DELETE FROM group_items WHERE group_id = ? AND symbol = ?", (group_id, symbol)
            ).rowcount
            if deleted == 0:
                raise ResourceNotFoundError(f"{symbol} is not in group {group.name!r}")
            return self._group_or_404(group_id)

    def move_item(
        self, from_group_id: int, symbol: str, to_group_id: int, *, copy: bool = False, before: str | None = None
    ) -> tuple[Group, Group]:
        symbol = normalize_symbol(symbol)
        before = normalize_symbol(before) if before else None
        same = from_group_id == to_group_id
        with self._lock, self._conn:
            source = self._group_or_404(from_group_id)
            item = next((i for i in source.items if i.symbol == symbol), None)
            if item is None:
                raise ResourceNotFoundError(f"{symbol} is not in group {source.name!r}")
            target = source if same else self._group_or_404(to_group_id)
            if same and copy:
                raise ValidationError("A symbol can't be copied into its own group")
            if not same:
                if symbol in target.symbols:
                    raise ConflictError(f"{symbol} is already in group {target.name!r}")
                if len(target.items) >= MAX_SYMBOLS_PER_GROUP:
                    raise ValidationError(f"A group can hold at most {MAX_SYMBOLS_PER_GROUP} symbols")

            order = [s for s in target.symbols if s != symbol]
            order.insert(order.index(before) if before in order else len(order), symbol)
            if not same:
                if not copy:
                    self._conn.execute("DELETE FROM group_items WHERE group_id = ? AND symbol = ?", (from_group_id, symbol))
                self._conn.execute("INSERT INTO group_items(group_id, symbol, name, position) VALUES (?, ?, ?, 0)",
                                   (to_group_id, symbol, item.name))
            self._conn.executemany("UPDATE group_items SET position = ? WHERE group_id = ? AND symbol = ?",
                                   [(index, to_group_id, s) for index, s in enumerate(order)])
            return self._group_or_404(from_group_id), self._group_or_404(to_group_id)

    def set_symbol_name(self, symbol: str, name: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE group_items SET name = ? WHERE symbol = ?", (name, normalize_symbol(symbol)))

    # ---- settings ----------------------------------------------------------------------------

    def get_setting(self, key: str) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def set_setting(self, key: str, value: Any) -> None:
        payload = json.dumps(value)
        if len(payload.encode("utf-8")) > MAX_SETTING_BYTES:
            raise ValidationError(f"Setting {key!r} exceeds {MAX_SETTING_BYTES} bytes")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, payload),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- helpers (caller holds the lock) -----------------------------------------------------

    def _insert_group(self, name: str, items: list[WatchItem]) -> int:
        position = self._conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM groups").fetchone()[0]
        try:
            cursor = self._conn.execute("INSERT INTO groups(name, position) VALUES (?, ?)", (name, position))
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"A group named {name!r} already exists") from exc
        group_id = int(cursor.lastrowid or 0)
        for index, item in enumerate(items):
            self._conn.execute(
                "INSERT OR IGNORE INTO group_items(group_id, symbol, name, position) VALUES (?, ?, ?, ?)",
                (group_id, normalize_symbol(item.symbol), item.name, index),
            )
        return group_id

    def _group_or_404(self, group_id: int) -> Group:
        row = self._conn.execute("SELECT id, name FROM groups WHERE id = ?", (group_id,)).fetchone()
        if row is None:
            raise ResourceNotFoundError(f"Group {group_id} not found")
        return self._load_group(row["id"], row["name"])

    def _load_group(self, group_id: int, name: str) -> Group:
        items = self._conn.execute(
            "SELECT symbol, name FROM group_items WHERE group_id = ? ORDER BY position, symbol", (group_id,)
        ).fetchall()
        return Group(group_id, name, tuple(WatchItem(r["symbol"], r["name"]) for r in items))
