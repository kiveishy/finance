#!/usr/bin/env bash
# Owl - run from source (Linux/macOS).
# Creates/activates .venv-linux, validates/installs dependencies, then starts the web server.
#   ./run.sh                  start owl and open the browser (if any)
#   ./run.sh --no-browser     start without opening a browser
#   ./run.sh --setup-only     prepare the environment only
# Environment overrides:
#   FINANCE_PYTHON      base interpreter used to create the venv (default python3)
#   OWL_VENV            venv directory (default .venv-linux)
#   OWL_AGGREGATOR_DIR  finance-aggregator sources (default ../../data-providers/finance-aggregator)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export OWL_AGGREGATOR_DIR="${OWL_AGGREGATOR_DIR:-$APP_DIR/../../data-providers/finance-aggregator}"
if [[ ! -f "$OWL_AGGREGATOR_DIR/run.sh" ]]; then
    echo "[owl] ERROR: finance-aggregator not found at $OWL_AGGREGATOR_DIR. Set OWL_AGGREGATOR_DIR." >&2
    exit 1
fi
OWL_VENV="${OWL_VENV:-$APP_DIR/.venv-linux}"
OWL_REQUIREMENTS="${OWL_REQUIREMENTS:-$APP_DIR/requirements.txt}"

# The aggregator's run script owns venv creation, repair and dependency validation.
FINANCE_VENV="$OWL_VENV" FINANCE_REQUIREMENTS="$OWL_REQUIREMENTS" bash "$OWL_AGGREGATOR_DIR/run.sh" --setup-only

if [[ "${1:-}" == "--setup-only" ]]; then
    exit 0
fi

# shellcheck disable=SC1091
source "$OWL_VENV/bin/activate"
exec python "$APP_DIR/main.py" "$@"
