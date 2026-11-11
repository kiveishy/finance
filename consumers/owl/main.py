"""Owl entry point (source runs and PyInstaller builds)."""

import os
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    # Owl imports only the aggregator's data contracts (models/errors) from its sources; data comes over HTTP.
    _aggregator = Path(os.environ.get(
        "OWL_AGGREGATOR_DIR",
        Path(__file__).resolve().parent.parent.parent / "data-providers" / "finance-aggregator",
    ))
    if not (_aggregator / "finance_aggregator").is_dir():
        sys.exit(f"finance aggregator sources not found at {_aggregator} (set OWL_AGGREGATOR_DIR)")
    sys.path.insert(0, str(_aggregator))

from owl.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
