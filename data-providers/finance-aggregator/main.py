"""Entry point for running from source and for the PyInstaller build."""

import sys

from finance_aggregator.app import main

if __name__ == "__main__":
    sys.exit(main())
