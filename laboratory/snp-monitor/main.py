"""snp-monitor entry point (source runs and PyInstaller builds)."""

import sys

from snp_monitor.app import main

if __name__ == "__main__":
    sys.exit(main())
