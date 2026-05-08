#!/usr/bin/env bash
set -euo pipefail

APP_NAME="oracle-forge"
TARGET="${ORACLE_FORGE_TARGET:-/opt/oracle-forge}"
SERVICE_NAME="oracle-forge"
PORT="${ORACLE_FORGE_PORT:-7860}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_ROOT="/root/oracle-forge-install-backups"
TS="$(date '+%Y%m%d_%H%M%S')"

echo "== Oracle Forge 神谕台 installer =="
echo "Source: $SRC_DIR"
echo "Target: $TARGET"
echo "Port:   $PORT"
echo

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: please run as root."
  exit 1
fi

echo "Installing system dependencies..."
apt update
apt install -y python3 python3-venv python3-pip rsync curl screen

echo
echo "Preparing target directory..."
mkdir -p "$TARGET"

if [ -d "$TARGET" ] && [ -n "$(ls -A "$TARGET" 2>/dev/null || true)" ]; then
  mkdir -p "$BACKUP_ROOT"
  BACKUP_DIR="$BACKUP_ROOT/${APP_NAME}_${TS}"
  echo "Existing installation detected. Creating backup:"
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
fi

echo
echo "Copying application files..."
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
echo "Creating runtime directories..."
mkdir -p "$TARGET/workspace/logs"
mkdir -p "$TARGET/workspace/uploads"
mkdir -p "$TARGET/workspace/downloads"
mkdir -p "$TARGET/workspace/source"
mkdir -p "$TARGET/workspace/build"
mkdir -p "$TARGET/workspace/data"
mkdir -p "$TARGET/backups"

echo
echo "Creating default config files if missing..."
if [ ! -f "$TARGET/.env" ] && [ -f "$TARGET/.env.example" ]; then
  cp "$TARGET/.env.example" "$TARGET/.env"
  chmod 600 "$TARGET/.env"
  echo "Created: $TARGET/.env"
fi

if [ ! -f "$TARGET/config.yaml" ] && [ -f "$TARGET/config.example.yaml" ]; then
  cp "$TARGET/config.example.yaml" "$TARGET/config.yaml"
  chmod 600 "$TARGET/config.yaml"
  echo "Created: $TARGET/config.yaml"
fi

echo
echo "Creating Python virtual environment..."
python3 -m venv "$TARGET/.venv"
"$TARGET/.venv/bin/python" -m pip install --upgrade pip setuptools wheel

if [ -f "$TARGET/requirements.txt" ]; then
  echo "Installing Python dependencies from requirements.txt..."
  "$TARGET/.venv/bin/pip" install -r "$TARGET/requirements.txt"
else
  echo "requirements.txt not found. Installing fallback dependencies..."
  "$TARGET/.venv/bin/pip" install fastapi 'uvicorn[standard]' pyyaml python-multipart requests aiofiles jinja2
fi

echo
echo "Python syntax check..."
export PYTHONDONTWRITEBYTECODE=1
[ -f "$TARGET/app.py" ] && "$TARGET/.venv/bin/python" -B -m py_compile "$TARGET/app.py"
[ -f "$TARGET/runner.py" ] && "$TARGET/.venv/bin/python" -B -m py_compile "$TARGET/runner.py"

echo
echo "Installing systemd service..."
mkdir -p "$TARGET/systemd"

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

echo
echo "Reloading systemd..."
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo
echo "Checking service status..."
sleep 2
systemctl status "$SERVICE_NAME" --no-pager || true

echo
echo "Checking local HTTP endpoint..."
curl -s -i "http://127.0.0.1:${PORT}/" | head -n 10 || true

echo
echo "== Install complete =="
echo "Visit:"
echo "  http://SERVER_IP:${PORT}"
echo
echo "Useful commands:"
echo "  systemctl status ${SERVICE_NAME} --no-pager"
echo "  journalctl -u ${SERVICE_NAME} -n 120 --no-pager"
echo "  systemctl restart ${SERVICE_NAME}"
