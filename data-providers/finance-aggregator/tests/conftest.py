from __future__ import annotations

import io
from pathlib import Path

import pytest

from finance_aggregator.providers.yahoo_db_bridge import YahooDbModuleLoader

PROJECT_DIR = Path(__file__).resolve().parent.parent
YAHOO_DB_DIR = PROJECT_DIR.parent / "yahoo-db"


@pytest.fixture
def out() -> io.StringIO:
    return io.StringIO()


@pytest.fixture(scope="session")
def yahoo_db_dir() -> Path:
    if not (YAHOO_DB_DIR / "database.py").is_file():
        pytest.skip(f"yahoo-db sources not found at {YAHOO_DB_DIR}")
    return YAHOO_DB_DIR


@pytest.fixture(scope="session")
def yahoo_db_loader(yahoo_db_dir: Path) -> YahooDbModuleLoader:
    return YahooDbModuleLoader([yahoo_db_dir])


@pytest.fixture
def yahoo_db_file(tmp_path: Path, yahoo_db_loader: YahooDbModuleLoader) -> Path:
    """SQLite file created by yahoo-db's own DatabaseManager (guarantees schema compatibility)."""
    module = yahoo_db_loader.load_database_module()
    db_path = tmp_path / "data" / "stocks.db"
    manager = module.DatabaseManager(str(db_path))
    manager.initialize_tables()
    manager.insert_ticker(
        {
            "symbol": "AAPL",
            "short_name": "Apple Inc.",
            "long_name": "Apple Inc.",
            "sector": "Technology",
            "market_cap": 3.0e12,
            "currency": "USD",
            "exchange": "NMS",
            "quote_type": "EQUITY",
            "employees": 164000,
        }
    )
    manager.insert_historical_data(
        "AAPL",
        [
            {"date": "2026-01-02", "open": 185.5, "close": 187.25, "high": 188.0, "low": 184.75, "volume": 45678900, "adj_close": 187.25},
            {"date": "2026-01-05", "open": 187.0, "close": 189.5, "high": 190.0, "low": 186.5, "volume": 52341200, "adj_close": 189.5},
            {"date": "2026-01-06", "open": 189.0, "close": 188.0, "high": 191.0, "low": 187.0, "volume": 40000000, "adj_close": 188.0},
        ],
    )
    manager.shutdown()
    return db_path
