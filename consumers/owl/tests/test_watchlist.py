from __future__ import annotations

from pathlib import Path

import pytest

from finance_aggregator.core import InvalidRequestError
from owl.errors import ConflictError, ResourceNotFoundError, ValidationError
from owl.watchlist import DEFAULT_SYMBOLS, MAX_SETTING_BYTES, SqliteWatchlistRepository, WatchItem


@pytest.fixture
def repo(tmp_path: Path):  # type: ignore[no-untyped-def]
    repository = SqliteWatchlistRepository(tmp_path / "owl.db")
    yield repository
    repository.close()


def test_seeded_once_with_default_group(tmp_path: Path) -> None:
    repo = SqliteWatchlistRepository(tmp_path / "owl.db")
    (group,) = repo.list_groups()
    assert group.symbols == DEFAULT_SYMBOLS
    repo.delete_group(group.id)
    repo.close()
    reopened = SqliteWatchlistRepository(tmp_path / "owl.db")
    assert reopened.list_groups() == [], "deleting everything must not re-seed"
    reopened.close()


def test_group_crud_and_order(repo: SqliteWatchlistRepository) -> None:
    tech = repo.create_group("  Tech  ")
    assert tech.name == "Tech"
    energy = repo.create_group("Energy")
    assert [g.name for g in repo.list_groups()][-2:] == ["Tech", "Energy"]
    assert repo.rename_group(energy.id, "Oil & Gas").name == "Oil & Gas"
    with pytest.raises(ConflictError):
        repo.create_group("tech")  # case-insensitive unique
    with pytest.raises(ConflictError):
        repo.rename_group(energy.id, "TECH")
    repo.delete_group(tech.id)
    with pytest.raises(ResourceNotFoundError):
        repo.get_group(tech.id)
    with pytest.raises(ResourceNotFoundError):
        repo.delete_group(tech.id)


@pytest.mark.parametrize("name", ["", "   ", "x" * 65])
def test_invalid_group_names(repo: SqliteWatchlistRepository, name: str) -> None:
    with pytest.raises(ValidationError):
        repo.create_group(name)


def test_items(repo: SqliteWatchlistRepository) -> None:
    group = repo.create_group("Semis")
    group = repo.add_item(group.id, WatchItem("nvda", "NVIDIA"))
    group = repo.add_item(group.id, WatchItem("AMD"))
    assert group.symbols == ("NVDA", "AMD")
    assert group.items[0].name == "NVIDIA"
    with pytest.raises(ConflictError):
        repo.add_item(group.id, WatchItem("NVDA"))
    with pytest.raises(InvalidRequestError):
        repo.add_item(group.id, WatchItem("../x"))
    with pytest.raises(ResourceNotFoundError):
        repo.add_item(9999, WatchItem("AMD"))
    assert repo.remove_item(group.id, "nvda").symbols == ("AMD",)
    with pytest.raises(ResourceNotFoundError):
        repo.remove_item(group.id, "NVDA")


def test_move_copy_and_reorder(repo: SqliteWatchlistRepository) -> None:
    tech = repo.create_group("Tech", [WatchItem("AAPL", "Apple"), WatchItem("MSFT"), WatchItem("NVDA", "NVIDIA")])
    semis = repo.create_group("Semis", [WatchItem("AMD"), WatchItem("INTC")])

    source, target = repo.move_item(tech.id, "nvda", semis.id)  # default: to the end
    assert source.symbols == ("AAPL", "MSFT") and target.symbols == ("AMD", "INTC", "NVDA")
    assert target.items[2].name == "NVIDIA"  # display name travels with the symbol

    _, target = repo.move_item(tech.id, "AAPL", semis.id, copy=True, before="INTC")
    assert target.symbols == ("AMD", "AAPL", "INTC", "NVDA")
    assert repo.get_group(tech.id).symbols == ("AAPL", "MSFT")  # copy keeps the original

    _, reordered = repo.move_item(semis.id, "NVDA", semis.id, before="AMD")
    assert reordered.symbols == ("NVDA", "AMD", "AAPL", "INTC")

    with pytest.raises(ConflictError):
        repo.move_item(tech.id, "AAPL", semis.id)  # already there
    with pytest.raises(ResourceNotFoundError):
        repo.move_item(tech.id, "NVDA", semis.id)  # not in the source any more
    with pytest.raises(ResourceNotFoundError):
        repo.move_item(tech.id, "MSFT", 9999)
    with pytest.raises(ValidationError):
        repo.move_item(semis.id, "AMD", semis.id, copy=True)
    assert repo.get_group(tech.id).symbols == ("AAPL", "MSFT")  # failed moves change nothing


def test_same_symbol_in_several_groups_and_cascade_delete(repo: SqliteWatchlistRepository) -> None:
    a, b = repo.create_group("A"), repo.create_group("B")
    repo.add_item(a.id, WatchItem("AAPL"))
    repo.add_item(b.id, WatchItem("AAPL"))
    repo.set_symbol_name("aapl", "Apple Inc.")
    assert repo.get_group(a.id).items[0].name == repo.get_group(b.id).items[0].name == "Apple Inc."
    repo.delete_group(a.id)
    assert repo.get_group(b.id).symbols == ("AAPL",)


def test_symbols_without_name(repo: SqliteWatchlistRepository) -> None:
    assert repo.symbols_without_name() == sorted(DEFAULT_SYMBOLS)
    repo.set_symbol_name("SPY", "SPDR")
    assert "SPY" not in repo.symbols_without_name()


def test_settings(repo: SqliteWatchlistRepository) -> None:
    assert repo.get_setting("chart") is None
    repo.set_setting("chart", {"indicators": [{"type": "sma", "period": 20}]})
    assert repo.get_setting("chart")["indicators"][0]["period"] == 20
    with pytest.raises(ValidationError):
        repo.set_setting("chart", {"blob": "x" * (MAX_SETTING_BYTES + 1)})


def test_persistence(tmp_path: Path) -> None:
    repo = SqliteWatchlistRepository(tmp_path / "owl.db", seed_defaults=False)
    group = repo.create_group("Keep")
    repo.add_item(group.id, WatchItem("MSFT"))
    repo.close()
    reopened = SqliteWatchlistRepository(tmp_path / "owl.db")
    assert [(g.name, g.symbols) for g in reopened.list_groups()] == [("Keep", ("MSFT",))]
    reopened.close()
