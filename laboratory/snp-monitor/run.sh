#!/usr/bin/env bash
# snp-monitor - run from source (Linux/macOS).
# Creates/activates .venv-linux, validates/installs dependencies, then starts the web server.
#   ./run.sh                  start snp-monitor and open the UI in Chrome (if any)
#   ./run.sh --no-browser     start without opening a browser
#   ./run.sh --setup-only     prepare the environment only
# Environment overrides:
#   SNP_PYTHON        base interpreter used to create the venv (default python3)
#   SNP_VENV          venv directory (default .venv-linux)
#   SNP_REQUIREMENTS  requirements file (default requirements.txt)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SNP_VENV="${SNP_VENV:-$APP_DIR/.venv-linux}"
SNP_REQUIREMENTS="${SNP_REQUIREMENTS:-$APP_DIR/requirements.txt}"
SNP_PYTHON="${SNP_PYTHON:-python3}"
GET_PIP_URL="https://bootstrap.pypa.io/get-pip.py"

# Debian/Ubuntu without python3-venv lack ensurepip: fall back to a pip-less venv + official get-pip.py.
bootstrap_pip_venv() {
    [[ "${SNP_NO_GET_PIP:-0}" == "1" ]] && return 1
    "$SNP_PYTHON" -c "import ensurepip" 2>/dev/null && return 1
    echo "[run] ensurepip unavailable; creating venv without pip and bootstrapping pip from $GET_PIP_URL"
    "$SNP_PYTHON" -m venv --without-pip "$SNP_VENV" || return 1
    local get_pip
    get_pip="$(mktemp)"
    if command -v curl >/dev/null; then
        curl -fsSL "$GET_PIP_URL" -o "$get_pip" || { rm -f "$get_pip"; return 1; }
    elif command -v wget >/dev/null; then
        wget -q "$GET_PIP_URL" -O "$get_pip" || { rm -f "$get_pip"; return 1; }
    else
        rm -f "$get_pip"; return 1
    fi
    "$SNP_VENV/bin/python" "$get_pip" --quiet --disable-pip-version-check
    local status=$?
    rm -f "$get_pip"
    return $status
}

VENV_MARKER="$SNP_VENV/.snp-monitor-location"

# venvs hard-code their own path: a moved/renamed or incomplete venv must be recreated.
venv_valid() {
    [[ -x "$SNP_VENV/bin/python" && -f "$SNP_VENV/pyvenv.cfg" && -f "$VENV_MARKER" ]] \
        && [[ "$(cat "$VENV_MARKER")" == "$SNP_VENV" ]]
}

if ! venv_valid; then
    if [[ -e "$SNP_VENV" ]]; then
        if [[ -f "$SNP_VENV/pyvenv.cfg" || -e "$SNP_VENV/bin/python" ]]; then
            echo "[run] Virtual environment $SNP_VENV is incomplete or was moved, recreating it"
            # Move aside first: recreating under the same name right after deletion is unreliable on shared folders.
            venv_old="$SNP_VENV.broken-$$"
            mv -- "$SNP_VENV" "$venv_old"
            rm -rf -- "$venv_old" || true
        else
            echo "[run] ERROR: $SNP_VENV exists but is not a virtual environment. Remove it or set SNP_VENV." >&2
            exit 1
        fi
    fi
    echo "[run] Creating virtual environment $SNP_VENV"
    echo "[run] Base interpreter: $SNP_PYTHON"
    if ! "$SNP_PYTHON" -m venv "$SNP_VENV" >/dev/null 2>&1; then
        rm -rf -- "$SNP_VENV"
        if ! bootstrap_pip_venv; then
            rm -rf -- "$SNP_VENV"
            echo "[run] ERROR: could not create the virtual environment." >&2
            echo "[run]        Debian/Ubuntu: sudo apt install python3-venv" >&2
            echo "[run]        Or set SNP_PYTHON to another Python 3.10+ interpreter." >&2
            exit 1
        fi
    fi
    echo "$SNP_VENV" > "$VENV_MARKER"
fi

# shellcheck disable=SC1091
source "$SNP_VENV/bin/activate"

if ! python "$APP_DIR/tools/ensure_env.py" "$SNP_REQUIREMENTS"; then
    echo "[run] ERROR: dependency validation failed" >&2
    exit 1
fi

if [[ "${1:-}" == "--setup-only" ]]; then
    echo "[run] Environment ready: $SNP_VENV"
    exit 0
fi

exec python "$APP_DIR/main.py" "$@"
