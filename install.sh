#!/usr/bin/env bash
#
# install.sh — DVD Server installer for Linux (Debian/Ubuntu/Armbian)
#
# Usage:
#     sudo ./install.sh                  # install + systemd service
#     sudo ./install.sh --no-service     # install only, no systemd unit
#     sudo ./install.sh --port 4251      # override port
#     sudo ./install.sh --user username
#     sudo ./install.sh --dir /opt/dvdserver
#
set -euo pipefail

SERVICE_NAME="dvdserver"
PORT=4251
INSTALL_DIR=""
TARGET_USER=""
WITH_SERVICE=1
WITH_APT=1

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
die()    { red "ERROR: $*" >&2; exit 1; }

usage() {
    sed -n '3,12p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)         PORT="$2"; shift 2 ;;
        --dir)          INSTALL_DIR="$2"; shift 2 ;;
        --user)         TARGET_USER="$2"; shift 2 ;;
        --service-name) SERVICE_NAME="$2"; shift 2 ;;
        --no-service)   WITH_SERVICE=0; shift ;;
        --no-apt)       WITH_APT=0; shift ;;
        -h|--help)      usage ;;
        *) die "Unknown argument: $1 (use --help)" ;;
    esac
done

[[ $EUID -eq 0 ]] || die "Run with sudo."

# ---- who will own / run the service ----
if [[ -z "$TARGET_USER" ]]; then
    TARGET_USER="${SUDO_USER:-}"
    [[ -n "$TARGET_USER" ]] || TARGET_USER="$(logname 2>/dev/null || true)"
fi
[[ -n "$TARGET_USER" && "$TARGET_USER" != "root" ]] || \
    die "Could not determine a non-root user. Pass --user NAME."
id "$TARGET_USER" >/dev/null 2>&1 || die "User '$TARGET_USER' does not exist."

TARGET_GROUP="$(id -gn "$TARGET_USER")"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[[ -d "$TARGET_HOME" ]] || die "Home $TARGET_HOME for '$TARGET_USER' does not exist."

as_user() {
    if command -v runuser >/dev/null 2>&1; then
        runuser -u "$TARGET_USER" -- "$@"
    else
        sudo -u "$TARGET_USER" -H -- "$@"
    fi
}

# ---- resolve install dir ----
if [[ -z "$INSTALL_DIR" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "$SCRIPT_DIR/dvd_server_backend.py" ]]; then
        INSTALL_DIR="$SCRIPT_DIR"
    else
        INSTALL_DIR="$TARGET_HOME/dvdserver"
    fi
fi
[[ -d "$INSTALL_DIR" ]] || die "Install directory does not exist: $INSTALL_DIR"
INSTALL_DIR="$(cd "$INSTALL_DIR" && pwd)"
[[ -f "$INSTALL_DIR/dvd_server_backend.py" ]] || \
    die "dvd_server_backend.py not found in $INSTALL_DIR"

green "Installing DVD Server"
echo   "  User:          $TARGET_USER ($TARGET_GROUP)"
echo   "  Home:          $TARGET_HOME"
echo   "  Install dir:   $INSTALL_DIR"
echo   "  Service name:  $SERVICE_NAME"
echo   "  Port:          $PORT"
echo   "  systemd unit:  $([[ $WITH_SERVICE -eq 1 ]] && echo yes || echo no)"
echo

# ---- apt dependencies ----
if [[ $WITH_APT -eq 1 ]]; then
    echo "==> Checking apt dependencies"
    MISSING=()
    command -v python3 >/dev/null 2>&1 || MISSING+=("python3")
    command -v ffmpeg  >/dev/null 2>&1 || MISSING+=("ffmpeg")
    dpkg -s python3-venv >/dev/null 2>&1 || MISSING+=("python3-venv")

    if [[ ${#MISSING[@]} -gt 0 ]]; then
        echo "  Missing: ${MISSING[*]}"
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y "${MISSING[@]}"
    else
        echo "  All present."
    fi
fi

command -v python3 >/dev/null 2>&1 || die "python3 not found."
if ! command -v ffmpeg >/dev/null 2>&1; then
    yellow "WARNING: ffmpeg not on PATH — audio-track switching and embedded"
    yellow "         subtitle extraction will not work until it is installed."
fi

# ---- venv + deps ----
VENV_DIR="$INSTALL_DIR/venv"
if [[ ! -x "$VENV_DIR/bin/python3" ]]; then
    echo "==> Creating virtualenv at $VENV_DIR"
    as_user python3 -m venv "$VENV_DIR" || \
        die "venv creation failed (python3-venv installed?)"
else
    echo "==> Reusing existing virtualenv"
fi

echo "==> Installing Python packages"
if [[ -f "$INSTALL_DIR/requirements.txt" ]]; then
    as_user "$VENV_DIR/bin/pip" install --upgrade pip wheel
    as_user "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
else
    yellow "requirements.txt not found — installing defaults inline."
    as_user "$VENV_DIR/bin/pip" install --upgrade pip wheel
    as_user "$VENV_DIR/bin/pip" install flask flask-cors waitress zeroconf
fi

# ---- make sure dvds/ exists and ownership is right ----
mkdir -p "$INSTALL_DIR/dvds"
chown -R "$TARGET_USER:$TARGET_GROUP" "$INSTALL_DIR"

# ---- systemd unit ----
if [[ $WITH_SERVICE -eq 1 ]]; then
    command -v systemctl >/dev/null 2>&1 || \
        die "systemctl not found. Re-run with --no-service."

    UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
    echo "==> Writing systemd unit $UNIT_PATH"

    cat > "$UNIT_PATH" <<EOF
[Unit]
Description=DVD MKV Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$TARGET_USER
Group=$TARGET_GROUP
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/python3 $INSTALL_DIR/dvd_server_backend.py
Restart=on-failure
RestartSec=5

# Basic hardening — safe for a LAN media server.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$INSTALL_DIR/dvds

[Install]
WantedBy=multi-user.target
EOF

    chmod 644 "$UNIT_PATH"
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"

    sleep 1
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        green "Service '$SERVICE_NAME' is running."
    else
        red "Service failed to start. Recent logs:"
        journalctl -u "$SERVICE_NAME" -n 25 --no-pager || true
        exit 1
    fi

    IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    echo
    green "Done."
    echo   "  URL:      http://${IP:-localhost}:$PORT"
    echo   "  Status:   systemctl status $SERVICE_NAME"
    echo   "  Logs:     journalctl -u $SERVICE_NAME -f"
    echo   "  Stop:     sudo systemctl stop $SERVICE_NAME"
    echo   "  Remove:   sudo systemctl disable --now $SERVICE_NAME && sudo rm $UNIT_PATH"
else
    echo
    green "Done (no service)."
    echo   "  Run manually:"
    echo   "    cd $INSTALL_DIR && $VENV_DIR/bin/python3 dvd_server_backend.py"
fi

echo
echo "Drop DVD folders into:"
echo "    $INSTALL_DIR/dvds/<Genre>/<DVD Folder>/*.mkv"
echo "    $INSTALL_DIR/dvds/<DVD Folder>/*.mkv       # appears as Uncategorized"
echo "Documentation:"
echo "    External programs + download links: $INSTALL_DIR/EXTERNAL_PROGRAMS.md"
echo "    Python dependencies:                $INSTALL_DIR/requirements.txt"