#!/usr/bin/env bash
# NYSE ticker sync - prepares the environment via run.sh, then runs sync_nyse.py with the given arguments.
# Uses the same YAHOODB_* environment overrides as run.sh.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YAHOODB_VENV="${YAHOODB_VENV:-$APP_DIR/.venv-linux}"
export YAHOODB_VENV

"$APP_DIR/run.sh" --setup-only

cd "$APP_DIR"
exec "$YAHOODB_VENV/bin/python" "$APP_DIR/sync_nyse.py" "$@"
