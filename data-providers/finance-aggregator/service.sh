#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Finance Aggregator - systemd daemon management (runs `finance-aggregator serve`).
#
#   sudo ./service.sh install [--mode binary|source] [--user NAME] [--config PATH]
#   sudo ./service.sh {uninstall|start|stop|restart|enable|disable}
#        ./service.sh {status|logs|unit}
#
# --mode binary  runs dist/linux/finance-aggregator (build with ./build.sh)
# --mode source  runs main.py with the .venv-linux interpreter (prepared via ./run.sh --setup-only)
# Default mode: binary when the built executable exists, otherwise source.
# ──────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="finance-aggregator"
SYSTEMD_DIR="/etc/systemd/system"
UNIT_PATH="${SYSTEMD_DIR}/${SERVICE_NAME}.service"
BINARY="$APP_DIR/dist/linux/finance-aggregator"
VENV_PYTHON="${FINANCE_VENV:-$APP_DIR/.venv-linux}/bin/python"

MODE=""
SERVICE_USER="${SUDO_USER:-$(whoami)}"
CONFIG=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${YELLOW}$*${NC}"; }
ok()    { echo -e "${GREEN}$*${NC}"; }
fail()  { echo -e "${RED}Error: $*${NC}" >&2; exit 1; }

print_usage() {
    sed -n '3,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

require_root() {
    [[ $EUID -eq 0 ]] || fail "this command requires root privileges. Use sudo."
}

parse_install_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --mode)   MODE="${2:-}"; shift 2 ;;
            --user)   SERVICE_USER="${2:-}"; shift 2 ;;
            --config) CONFIG="${2:-}"; shift 2 ;;
            *) fail "unknown option '$1'" ;;
        esac
    done
    if [[ -z "$MODE" ]]; then
        if [[ -x "$BINARY" ]]; then MODE="binary"; else MODE="source"; fi
    fi
    [[ "$MODE" == "binary" || "$MODE" == "source" ]] || fail "--mode must be 'binary' or 'source'"
    id "$SERVICE_USER" >/dev/null 2>&1 || fail "user '$SERVICE_USER' does not exist"
}

# Prints ExecStart / WorkingDirectory / runtime dir for the selected mode.
resolve_runtime() {
    if [[ "$MODE" == "binary" ]]; then
        [[ -x "$BINARY" ]] || fail "executable not found: $BINARY (run ./build.sh first)"
        WORK_DIR="$(dirname "$BINARY")"
        CONFIG="${CONFIG:-$WORK_DIR/config.json}"
        EXEC_START="\"$BINARY\" --config \"$CONFIG\" serve"
    else
        WORK_DIR="$APP_DIR"
        CONFIG="${CONFIG:-$APP_DIR/config.json}"
        EXEC_START="\"$VENV_PYTHON\" \"$APP_DIR/main.py\" --config \"$CONFIG\" serve"
    fi
    [[ -f "$CONFIG" ]] || fail "config file not found: $CONFIG"
    RUNTIME_DIR="$WORK_DIR/runtime"
}

render_unit() {
    cat <<EOF
[Unit]
Description=Finance Aggregator - unified stock data API (yahoo-db local/API + Yahoo Finance fallback)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${WORK_DIR}
ExecStart=${EXEC_START}
Restart=on-failure
RestartSec=10
KillSignal=SIGTERM
TimeoutStopSec=20
Environment=PYTHONUNBUFFERED=1
# PyInstaller onefile extraction dir; kept out of /tmp so tmp cleaners cannot remove it while running.
Environment="TMPDIR=${RUNTIME_DIR}"
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
}

cmd_unit() {
    parse_install_args "$@"
    resolve_runtime
    render_unit
}

cmd_install() {
    require_root
    parse_install_args "$@"
    if [[ "$MODE" == "source" ]]; then
        info "Validating virtual environment as ${SERVICE_USER}..."
        sudo -u "$SERVICE_USER" -H "$APP_DIR/run.sh" --setup-only
    fi
    [[ "$MODE" == "binary" || -x "$VENV_PYTHON" ]] || fail "virtual environment missing: $VENV_PYTHON"
    resolve_runtime

    info "Installing ${SERVICE_NAME} (${MODE} mode, user ${SERVICE_USER})..."
    install -d -o "$SERVICE_USER" "$RUNTIME_DIR"
    render_unit > "$UNIT_PATH"
    chmod 644 "$UNIT_PATH"
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    ok "Service installed and enabled: $UNIT_PATH"
    echo -e "Start with: ${YELLOW}sudo $0 start${NC}"
}

cmd_uninstall() {
    require_root
    info "Uninstalling ${SERVICE_NAME}..."
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$UNIT_PATH"
    systemctl daemon-reload
    ok "Service uninstalled."
}

cmd_start()   { require_root; systemctl start "$SERVICE_NAME";   ok "Service started.";   systemctl --no-pager status "$SERVICE_NAME" || true; }
cmd_stop()    { require_root; systemctl stop "$SERVICE_NAME";    ok "Service stopped."; }
cmd_restart() { require_root; systemctl restart "$SERVICE_NAME"; ok "Service restarted."; systemctl --no-pager status "$SERVICE_NAME" || true; }
cmd_enable()  { require_root; systemctl enable "$SERVICE_NAME";  ok "Auto-start on boot enabled."; }
cmd_disable() { require_root; systemctl disable "$SERVICE_NAME"; ok "Auto-start on boot disabled."; }

cmd_status() {
    systemctl --no-pager status "$SERVICE_NAME" 2>/dev/null || info "Service is not installed or not running."
}

cmd_logs() {
    info "Logs for ${SERVICE_NAME} (Ctrl+C to exit)..."
    journalctl -u "$SERVICE_NAME" -f --no-pager 2>/dev/null || {
        echo "journald not available, falling back to the application log file..."
        tail -f "$APP_DIR/logs/finance-aggregator.log"
    }
}

command="${1:-}"
[[ $# -gt 0 ]] && shift
case "$command" in
    install)   cmd_install "$@" ;;
    uninstall) cmd_uninstall ;;
    start)     cmd_start ;;
    stop)      cmd_stop ;;
    restart)   cmd_restart ;;
    status)    cmd_status ;;
    logs)      cmd_logs ;;
    enable)    cmd_enable ;;
    disable)   cmd_disable ;;
    unit)      cmd_unit "$@" ;;
    *)         print_usage; exit 1 ;;
esac
