#!/usr/bin/env bash
# Runs every time the Codespace starts: the virtual screen, then Folio on port 7860.
cd "$(dirname "$0")/.."
mkdir -p /workspaces/folio-data
rm -f /tmp/.X99-lock
pkill -f "gunicorn.*webapp:app" 2>/dev/null
setsid nohup Xvfb :99 -screen 0 1280x900x24 >/tmp/xvfb.log 2>&1 &
setsid nohup gunicorn -w 1 --threads 16 --timeout 0 -b 0.0.0.0:7860 webapp:app >/tmp/folio.log 2>&1 &
sleep 2
echo "Folio is starting on port 7860 (log: /tmp/folio.log)"
