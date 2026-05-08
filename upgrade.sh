#!/usr/bin/env bash
set -euo pipefail

APP_NAME="oracle-forge"
TARGET="${ORACLE_FORGE_TARGET:-/opt/oracle-forge}"
SERVICE_NAME="oracle-forge"
PORT="${ORACLE_FORGE_PORT:-7860}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_ROOT="/root/oracle-forge-upgrade-backups"
TS="$(date '+%Y%m%d_%H%M%S')"

echo "== Oracle Forge 神谕台 upgrader =="
echo "Source: $SRC_DIR"
echo "Target: $TARGET"
echo "Port:   $PORT"
echo

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: please run as root."
  exit 1
fi

if [ ! -d "$TARGET" ]; then
  echo "ERROR: target installation not found: $TARGET"
  echo "Run install.sh first."
  exit 1
fi

echo "Installing required tools..."
apt update
apt install -y python3 python3-venv python3-pip rsync curl screen

mkdir -p "$BACKUP_ROOT"
BACKUP_DIR="$BACKUP_ROOT/${APP_NAME}_${TS}"

echo
echo "Creating backup:"
echo "  $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"

rsync -a "$TARGET"/ "$BACKUP_DIR"/ \
  --exclude='.venv/' \
  --exclude='venv/' \
  --exclude='env/' \
  --exclude='workspace/source/' \
  --exclude='workspace/build/' \
  --exclude='workspace/data/' \
  --exclude='workspace/uploads/' \
  --exclude='workspace/downloads/' \
  --exclude='workspace/logs/' \
  --exclude='*.log' \
  --exclude='*.screen.log' || true

echo
echo "Preserving local config..."
TMP_KEEP="/tmp/oracle-forge-keep-${TS}"
mkdir -p "$TMP_KEEP"

[ -f "$TARGET/.env" ] && cp -a "$TARGET/.env" "$TMP_KEEP/.env"
[ -f "$TARGET/config.yaml" ] && cp -a "$TARGET/config.yaml" "$TMP_KEEP/config.yaml"

echo
echo "Copying new version files..."
rsync -a "$SRC_DIR"/ "$TARGET"/ \
  --exclude='.git/' \
  --exclude='.github/' \
  --exclude='.env' \
  --exclude='config.yaml' \
  --exclude='.venv/' \
  --exclude='venv/' \
  --exclude='env/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  --exclude='workspace/' \
  --exclude='logs/' \
  --exclude='tmp/' \
  --exclude='cache/' \
  --exclude='uploads/' \
  --exclude='downloads/' \
  --exclude='backups/' \
  --exclude='dist/' \
  --exclude='build/' \
  --exclude='*.log' \
  --exclude='*.screen.log' \
  --exclude='*.tar.gz' \
  --exclude='*.zip'

echo
echo "Restoring local config..."
[ -f "$TMP_KEEP/.env" ] && cp -a "$TMP_KEEP/.env" "$TARGET/.env"
[ -f "$TMP_KEEP/config.yaml" ] && cp -a "$TMP_KEEP/config.yaml" "$TARGET/config.yaml"
rm -rf "$TMP_KEEP"

echo
echo "Ensuring runtime directories..."
mkdir -p "$TARGET/workspace/logs"
mkdir -p "$TARGET/workspace/uploads"
mkdir -p "$TARGET/workspace/downloads"
mkdir -p "$TARGET/workspace/source"
mkdir -p "$TARGET/workspace/build"
mkdir -p "$TARGET/workspace/data"
mkdir -p "$TARGET/backups"

if [ ! -f "$TARGET/.env" ] && [ -f "$TARGET/.env.example" ]; then
  cp "$TARGET/.env.example" "$TARGET/.env"
  chmod 600 "$TARGET/.env"
fi

if [ ! -f "$TARGET/config.yaml" ] && [ -f "$TARGET/config.example.yaml" ]; then
  cp "$TARGET/config.example.yaml" "$TARGET/config.yaml"
  chmod 600 "$TARGET/config.yaml"
fi

echo
echo "Updating Python virtual environment..."
if [ ! -d "$TARGET/.venv" ]; then
  python3 -m venv "$TARGET/.venv"
fi

"$TARGET/.venv/bin/python" -m pip install --upgrade pip setuptools wheel

if [ -f "$TARGET/requirements.txt" ]; then
  "$TARGET/.venv/bin/pip" install -r "$TARGET/requirements.txt"
else
  "$TARGET/.venv/bin/pip" install fastapi 'uvicorn[standard]' pyyaml python-multipart requests aiofiles jinja2
fi

echo
echo "Python syntax check..."
export PYTHONDONTWRITEBYTECODE=1
[ -f "$TARGET/app.py" ] && "$TARGET/.venv/bin/python" -B -m py_compile "$TARGET/app.py"
[ -f "$TARGET/runner.py" ] && "$TARGET/.venv/bin/python" -B -m py_compile "$TARGET/runner.py"

echo
echo "Updating systemd service..."
if [ -f "$TARGET/systemd/oracle-forge.service" ]; then
  cp "$TARGET/systemd/oracle-forge.service" "/etc/systemd/system/${SERVICE_NAME}.service"
else
  cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<SERVICE_EOF
[Unit]
Description=Oracle Forge 神谕台
After=network.target

[Service]
Type=simple
WorkingDirectory=${TARGET}
Environment=PYTHONUNBUFFERED=1
Environment=ORACLE_FORGE_HOME=${TARGET}
Environment=ORACLE_FORGE_PORT=${PORT}
ExecStart=${TARGET}/.venv/bin/uvicorn app:app --host 0.0.0.0 --port ${PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
SERVICE_EOF
fi

systemctl daemon-reload
systemctl restart "$SERVICE_NAME"

echo
echo "Checking service status..."
sleep 2
systemctl status "$SERVICE_NAME" --no-pager || true

echo
echo "Checking local HTTP endpoint..."
curl -s -i "http://127.0.0.1:${PORT}/" | head -n 10 || true

echo
echo "== Upgrade complete =="
echo "Backup:"
echo "  $BACKUP_DIR"
echo
echo "Useful commands:"
echo "  systemctl status ${SERVICE_NAME} --no-pager"
echo "  journalctl -u ${SERVICE_NAME} -n 120 --no-pager"
echo "  systemctl restart ${SERVICE_NAME}"
