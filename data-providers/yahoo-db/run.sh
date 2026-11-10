#!/usr/bin/env bash
# Stock Data Service (yahoo-db) - run from source (Linux/macOS).
# Creates/activates the venv, validates/installs dependencies, then starts the Flask dashboard + API.
#   ./run.sh                    start the service (http://localhost:5000)
#   ./run.sh --setup-only       prepare the environment only
# Environment overrides:
#   YAHOODB_PYTHON        base interpreter used to create the venv (default python3)
#   YAHOODB_VENV          venv directory (default .venv-linux)
#   YAHOODB_REQUIREMENTS  requirements file (default requirements.txt)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YAHOODB_VENV="${YAHOODB_VENV:-$APP_DIR/.venv-linux}"
YAHOODB_REQUIREMENTS="${YAHOODB_REQUIREMENTS:-$APP_DIR/requirements.txt}"
YAHOODB_PYTHON="${YAHOODB_PYTHON:-python3}"
GET_PIP_URL="https://bootstrap.pypa.io/get-pip.py"

# Debian/Ubuntu without python3-venv lack ensurepip: fall back to a pip-less venv + official get-pip.py.
bootstrap_pip_venv() {
    [[ "${YAHOODB_NO_GET_PIP:-0}" == "1" ]] && return 1
    "$YAHOODB_PYTHON" -c "import ensurepip" 2>/dev/null && return 1
    echo "[run] ensurepip unavailable; creating venv without pip and bootstrapping pip from $GET_PIP_URL"
    "$YAHOODB_PYTHON" -m venv --without-pip "$YAHOODB_VENV" || return 1
    local get_pip
    get_pip="$(mktemp)"
    if command -v curl >/dev/null; then
        curl -fsSL "$GET_PIP_URL" -o "$get_pip" || { rm -f "$get_pip"; return 1; }
    elif command -v wget >/dev/null; then
        wget -q "$GET_PIP_URL" -O "$get_pip" || { rm -f "$get_pip"; return 1; }
    else
        rm -f "$get_pip"; return 1
    fi
    "$YAHOODB_VENV/bin/python" "$get_pip" --quiet --disable-pip-version-check
    local status=$?
    rm -f "$get_pip"
    return $status
}

VENV_MARKER="$YAHOODB_VENV/.yahoo-db-location"

# venvs hard-code their own path: a moved/renamed or incomplete venv must be recreated.
venv_valid() {
    [[ -x "$YAHOODB_VENV/bin/python" && -f "$YAHOODB_VENV/pyvenv.cfg" && -f "$VENV_MARKER" ]] \
        && [[ "$(cat "$VENV_MARKER")" == "$YAHOODB_VENV" ]]
}

if ! venv_valid; then
    if [[ -e "$YAHOODB_VENV" ]]; then
        if [[ -f "$YAHOODB_VENV/pyvenv.cfg" || -e "$YAHOODB_VENV/bin/python" ]]; then
            echo "[run] Virtual environment $YAHOODB_VENV is incomplete or was moved, recreating it"
            # Move aside first: recreating under the same name right after deletion is unreliable on shared folders.
            venv_old="$YAHOODB_VENV.broken-$$"
            mv -- "$YAHOODB_VENV" "$venv_old"
            rm -rf -- "$venv_old" || true
        else
            echo "[run] ERROR: $YAHOODB_VENV exists but is not a virtual environment. Remove it or set YAHOODB_VENV." >&2
            exit 1
        fi
    fi
    echo "[run] Creating virtual environment $YAHOODB_VENV"
    echo "[run] Base interpreter: $YAHOODB_PYTHON"
    if ! "$YAHOODB_PYTHON" -m venv "$YAHOODB_VENV" >/dev/null 2>&1; then
        rm -rf -- "$YAHOODB_VENV"
        if ! bootstrap_pip_venv; then
            rm -rf -- "$YAHOODB_VENV"
            echo "[run] ERROR: could not create the virtual environment." >&2
            echo "[run]        Debian/Ubuntu: sudo apt install python3-venv" >&2
            echo "[run]        Or set YAHOODB_PYTHON to another Python 3.10+ interpreter." >&2
            exit 1
        fi
    fi
    echo "$YAHOODB_VENV" > "$VENV_MARKER"
fi

# shellcheck disable=SC1091
source "$YAHOODB_VENV/bin/activate"

if ! python "$APP_DIR/tools/ensure_env.py" "$YAHOODB_REQUIREMENTS"; then
    echo "[run] ERROR: dependency validation failed" >&2
    exit 1
fi

if [[ "${1:-}" == "--setup-only" ]]; then
    echo "[run] Environment ready: $YAHOODB_VENV"
    exit 0
fi

# main.py reads config.json from the working directory.
cd "$APP_DIR"
exec python "$APP_DIR/main.py" "$@"
