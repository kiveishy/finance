#!/usr/bin/env bash
# Owl - build a standalone Linux executable (dist/linux/owl).
#   ./build.sh               run unit tests, then build
#   ./build.sh --skip-tests  build without running tests
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export OWL_VENV="${OWL_VENV:-$APP_DIR/.venv-linux}"
DIST_DIR="$APP_DIR/dist/linux"
WORK_DIR="$APP_DIR/build/linux"

OWL_REQUIREMENTS="$APP_DIR/requirements-dev.txt" bash "$APP_DIR/run.sh" --setup-only
# shellcheck disable=SC1091
source "$OWL_VENV/bin/activate"
cd "$APP_DIR"

if [[ "${1:-}" != "--skip-tests" ]]; then
    echo "[build] Running unit tests"
    python -m pytest -q -p no:cacheprovider
fi

echo "[build] Building executable into $DIST_DIR"
python -m PyInstaller --noconfirm --clean --log-level WARN \
    --distpath "$DIST_DIR" --workpath "$WORK_DIR" owl.spec

echo "[build] Smoke test"
"$DIST_DIR/owl" --version

echo "[build] OK: $DIST_DIR/owl"
