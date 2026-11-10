#!/usr/bin/env bash
# Finance Aggregator - run from source (Linux/macOS).
# Creates/activates the venv, validates/installs dependencies, then starts the app.
#   ./run.sh                    interactive CLI
#   ./run.sh status             one-shot command
#   ./run.sh serve              HTTP API server
#   ./run.sh --setup-only       prepare the environment only
# Environment overrides:
#   FINANCE_PYTHON        base interpreter used to create the venv (default python3)
#   FINANCE_VENV          venv directory (default .venv-linux)
#   FINANCE_REQUIREMENTS  requirements file (default requirements.txt)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FINANCE_VENV="${FINANCE_VENV:-$APP_DIR/.venv-linux}"
FINANCE_REQUIREMENTS="${FINANCE_REQUIREMENTS:-$APP_DIR/requirements.txt}"
FINANCE_PYTHON="${FINANCE_PYTHON:-python3}"
GET_PIP_URL="https://bootstrap.pypa.io/get-pip.py"

# Debian/Ubuntu without python3-venv lack ensurepip: fall back to a pip-less venv + official get-pip.py.
bootstrap_pip_venv() {
    [[ "${FINANCE_NO_GET_PIP:-0}" == "1" ]] && return 1
    "$FINANCE_PYTHON" -c "import ensurepip" 2>/dev/null && return 1
    echo "[run] ensurepip unavailable; creating venv without pip and bootstrapping pip from $GET_PIP_URL"
    "$FINANCE_PYTHON" -m venv --without-pip "$FINANCE_VENV" || return 1
    local get_pip
    get_pip="$(mktemp)"
    if command -v curl >/dev/null; then
        curl -fsSL "$GET_PIP_URL" -o "$get_pip" || { rm -f "$get_pip"; return 1; }
    elif command -v wget >/dev/null; then
        wget -q "$GET_PIP_URL" -O "$get_pip" || { rm -f "$get_pip"; return 1; }
    else
        rm -f "$get_pip"; return 1
    fi
    "$FINANCE_VENV/bin/python" "$get_pip" --quiet --disable-pip-version-check
    local status=$?
    rm -f "$get_pip"
    return $status
}

VENV_MARKER="$FINANCE_VENV/.finance-aggregator-location"

# venvs hard-code their own path: a moved/renamed or incomplete venv must be recreated.
venv_valid() {
    [[ -x "$FINANCE_VENV/bin/python" && -f "$FINANCE_VENV/pyvenv.cfg" && -f "$VENV_MARKER" ]] \
        && [[ "$(cat "$VENV_MARKER")" == "$FINANCE_VENV" ]]
}

if ! venv_valid; then
    if [[ -e "$FINANCE_VENV" ]]; then
        if [[ -f "$FINANCE_VENV/pyvenv.cfg" || -e "$FINANCE_VENV/bin/python" ]]; then
            echo "[run] Virtual environment $FINANCE_VENV is incomplete or was moved, recreating it"
            # Move aside first: recreating under the same name right after deletion is unreliable on shared folders.
            venv_old="$FINANCE_VENV.broken-$$"
            mv -- "$FINANCE_VENV" "$venv_old"
            rm -rf -- "$venv_old" || true
        else
            echo "[run] ERROR: $FINANCE_VENV exists but is not a virtual environment. Remove it or set FINANCE_VENV." >&2
            exit 1
        fi
    fi
    echo "[run] Creating virtual environment $FINANCE_VENV"
    echo "[run] Base interpreter: $FINANCE_PYTHON"
    if ! "$FINANCE_PYTHON" -m venv "$FINANCE_VENV" >/dev/null 2>&1; then
        rm -rf -- "$FINANCE_VENV"
        if ! bootstrap_pip_venv; then
            rm -rf -- "$FINANCE_VENV"
            echo "[run] ERROR: could not create the virtual environment." >&2
            echo "[run]        Debian/Ubuntu: sudo apt install python3-venv" >&2
            echo "[run]        Or set FINANCE_PYTHON to another Python 3.10+ interpreter." >&2
            exit 1
        fi
    fi
    echo "$FINANCE_VENV" > "$VENV_MARKER"
fi

# shellcheck disable=SC1091
source "$FINANCE_VENV/bin/activate"

if ! python "$APP_DIR/tools/ensure_env.py" "$FINANCE_REQUIREMENTS"; then
    echo "[run] ERROR: dependency validation failed" >&2
    exit 1
fi

if [[ "${1:-}" == "--setup-only" ]]; then
    echo "[run] Environment ready: $FINANCE_VENV"
    exit 0
fi

exec python "$APP_DIR/main.py" "$@"
