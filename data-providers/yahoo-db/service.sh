#!/bin/bash
# ──────────────────────────────────────────────────────────────
# Stock Data Service - Daemon Management Script
# Installs, starts, stops, and monitors the systemd service.
# ──────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="stock-data-service"
SERVICE_FILE="$SCRIPT_DIR/${SERVICE_NAME}.service"
SYSTEMD_DIR="/etc/systemd/system"
CURRENT_USER="$(whoami)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_usage() {
    echo "Usage: $0 {install|uninstall|start|stop|restart|status|logs|enable|disable}"
    echo ""
    echo "Commands:"
    echo "  install     Install and enable the systemd service"
    echo "  uninstall   Stop, disable, and remove the systemd service"
    echo "  start       Start the service"
    echo "  stop        Stop the service"
    echo "  restart     Restart the service"
    echo "  status      Show service status"
    echo "  logs        Show service logs (follow mode)"
    echo "  enable      Enable auto-start on boot"
    echo "  disable     Disable auto-start on boot"
}

require_root() {
    if [[ $EUID -ne 0 ]]; then
        echo -e "${RED}Error: This command requires root privileges. Use sudo.${NC}"
        exit 1
    fi
}

cmd_install() {
    require_root
    echo -e "${YELLOW}Installing ${SERVICE_NAME} service...${NC}"

    # Generate service file with correct paths
    VENV_PYTHON="${YAHOODB_VENV:-$SCRIPT_DIR/.venv-linux}/bin/python"

    echo -e "${YELLOW}Validating virtual environment as ${SUDO_USER:-$CURRENT_USER}...${NC}"
    sudo -u "${SUDO_USER:-$CURRENT_USER}" -H env YAHOODB_VENV="${YAHOODB_VENV:-}" "$SCRIPT_DIR/run.sh" --setup-only

    if [[ ! -x "$VENV_PYTHON" ]]; then
        echo -e "${RED}Error: Virtual environment not found at $VENV_PYTHON${NC}"
        echo "Run ./run.sh --setup-only to create it."
        exit 1
    fi

    # Create service file dynamically with correct paths
    cat > "${SYSTEMD_DIR}/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Stock Data Service - Yahoo Finance Sync and Flask Dashboard
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=${SUDO_USER:-$CURRENT_USER}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${VENV_PYTHON} ${SCRIPT_DIR}/main.py
Restart=on-failure
RestartSec=10
StartLimitIntervalSec=300
StartLimitBurst=5
StandardOutput=file:${SCRIPT_DIR}/stock_service.log
StandardError=file:${SCRIPT_DIR}/stock_service.log
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "${SERVICE_NAME}"
    echo -e "${GREEN}Service installed and enabled successfully.${NC}"
    echo -e "Start with: ${YELLOW}sudo $0 start${NC}"
}

cmd_uninstall() {
    require_root
    echo -e "${YELLOW}Uninstalling ${SERVICE_NAME} service...${NC}"

    systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
    systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
    rm -f "${SYSTEMD_DIR}/${SERVICE_NAME}.service"
    systemctl daemon-reload

    echo -e "${GREEN}Service uninstalled successfully.${NC}"
}

cmd_start() {
    require_root
    echo -e "${YELLOW}Starting ${SERVICE_NAME}...${NC}"
    systemctl start "${SERVICE_NAME}"
    echo -e "${GREEN}Service started.${NC}"
    systemctl --no-pager status "${SERVICE_NAME}"
}

cmd_stop() {
    require_root
    echo -e "${YELLOW}Stopping ${SERVICE_NAME}...${NC}"
    systemctl stop "${SERVICE_NAME}"
    echo -e "${GREEN}Service stopped.${NC}"
}

cmd_restart() {
    require_root
    echo -e "${YELLOW}Restarting ${SERVICE_NAME}...${NC}"
    systemctl restart "${SERVICE_NAME}"
    echo -e "${GREEN}Service restarted.${NC}"
    systemctl --no-pager status "${SERVICE_NAME}"
}

cmd_status() {
    systemctl --no-pager status "${SERVICE_NAME}" 2>/dev/null || echo -e "${YELLOW}Service is not installed or not running.${NC}"
}

cmd_logs() {
    echo -e "${YELLOW}Showing logs for ${SERVICE_NAME} (Ctrl+C to exit)...${NC}"
    journalctl -u "${SERVICE_NAME}" -f --no-pager 2>/dev/null || {
        echo "Falling back to log file..."
        tail -f "${SCRIPT_DIR}/stock_service.log"
    }
}

cmd_enable() {
    require_root
    systemctl enable "${SERVICE_NAME}"
    echo -e "${GREEN}Service enabled for auto-start on boot.${NC}"
}

cmd_disable() {
    require_root
    systemctl disable "${SERVICE_NAME}"
    echo -e "${GREEN}Service disabled from auto-start on boot.${NC}"
}

# Main
case "${1:-}" in
    install)   cmd_install ;;
    uninstall) cmd_uninstall ;;
    start)     cmd_start ;;
    stop)      cmd_stop ;;
    restart)   cmd_restart ;;
    status)    cmd_status ;;
    logs)      cmd_logs ;;
    enable)    cmd_enable ;;
    disable)   cmd_disable ;;
    *)         print_usage; exit 1 ;;
esac
