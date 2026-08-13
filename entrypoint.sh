#!/bin/sh
# Boot the Aether panel + block runner directly (no docker required inside the
# container, unlike `monitor.sh up`). Generates an access token on first run,
# exposes the web assets, then execs the backend as PID-managed process.
set -eu

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

mkdir -p state blocks web

# Access token — same shape monitor.sh init_all uses.
if [ ! -f state/token ]; then
  head -c 24 /dev/urandom | base64 | tr -d '=+/\n' > state/token
  chmod 600 state/token
fi

# backend.py serves static files from ./web; link the panel assets in.
for asset in index.html script style; do
  if [ ! -e "web/$asset" ]; then
    ln -s "../$asset" "web/$asset"
  fi
done

echo "Aether blocks panel"
echo "Board: http://${MONITOR_BIND:-0.0.0.0}:${MONITOR_PORT:-8080}/"
echo "Token: $(cat state/token)"

exec python3 backend.py \
  --root "$APP_DIR" \
  --monitor-dir "$APP_DIR" \
  --port "${MONITOR_PORT:-8080}" \
  --bind "${MONITOR_BIND:-0.0.0.0}"
