"""Loads reusable modules straight from the yahoo-db service sources (no copy, no duplication).

yahoo-db is a script-style project (hyphenated file names, top-level modules such as
``database``), so its files are loaded by path under unique module names.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Iterable

from ..core.errors import ProviderUnavailableError

logger = logging.getLogger(__name__)

API_CLIENT_FILE = "yahoodb-api.py"
DATABASE_FILE = "database.py"
#: Folder name used for yahoo-db sources inside a frozen (PyInstaller) bundle.
BUNDLED_DIR_NAME = "yahoo_db"


class YahooDbModuleLoader:
    _lock = threading.Lock()

    def __init__(self, search_dirs: Iterable[Path]) -> None:
        self._search_dirs = tuple(Path(d) for d in search_dirs)

    @property
    def search_dirs(self) -> tuple[Path, ...]:
        return self._search_dirs

    def find(self, filename: str) -> Path:
        for directory in self._search_dirs:
            candidate = directory / filename
            if candidate.is_file():
                return candidate
        searched = ", ".join(str(d) for d in self._search_dirs) or "<none>"
        raise ProviderUnavailableError(f"yahoo-db source {filename!r} not found (searched: {searched})")

    def load(self, filename: str, module_name: str) -> ModuleType:
        path = self.find(filename).resolve()
        with self._lock:
            cached = sys.modules.get(module_name)
            if cached is not None and Path(getattr(cached, "__file__", "")).resolve() == path:
                return cached
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ProviderUnavailableError(f"Cannot load yahoo-db module from {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            # Don't write __pycache__ into the (possibly read-only) yahoo-db folder.
            previous_flag, sys.dont_write_bytecode = sys.dont_write_bytecode, True
            try:
                spec.loader.exec_module(module)
            except Exception as exc:
                del sys.modules[module_name]
                raise ProviderUnavailableError(f"Failed to import yahoo-db module {path}: {exc}") from exc
            finally:
                sys.dont_write_bytecode = previous_flag
            logger.debug("Loaded yahoo-db module %s from %s", module_name, path)
            return module

    def load_api_client_module(self) -> ModuleType:
        return self.load(API_CLIENT_FILE, "yahoodb_api_client")

    def load_database_module(self) -> ModuleType:
        return self.load(DATABASE_FILE, "yahoodb_database")


def default_search_dirs(configured: Path | None, bundle_dir: Path | None) -> list[Path]:
    dirs: list[Path] = []
    if configured is not None:
        dirs.append(configured)
    if bundle_dir is not None:
        dirs.append(bundle_dir / BUNDLED_DIR_NAME)
    return dirs
