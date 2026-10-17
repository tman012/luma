#!/bin/bash

# Entrypoint script for Luma
# Connects to configured ADB devices from settings, then starts gunicorn.

echo "================================================"
echo "Luma - Starting"
echo "================================================"

# Set HOME for ADB
export HOME=/app/home
mkdir -p $HOME/.android

# LUMA_DISABLE_ADB=1 (e.g. a dev instance) or LUMA_MINIMAL=1: never start ADB or connect to devices.
if [ "${LUMA_DISABLE_ADB}" = "1" ] || [ "${LUMA_DISABLE_ADB}" = "true" ] \
   || [ "${LUMA_MINIMAL}" = "1" ] || [ "${LUMA_MINIMAL}" = "true" ]; then
    echo "LUMA_DISABLE_ADB is set: skipping ADB server and device connections."
    echo "================================================"
    exec gunicorn -c gunicorn.conf.py app.main:app
fi

# Kill any stale ADB server from a previous run, then start fresh.
# Timeouts prevent hangs when devices are unreachable.
adb kill-server 2>/dev/null
timeout 5 adb start-server 2>/dev/null

# Attempt to connect to rooms from settings.json (if it exists)
SETTINGS_FILE="/app/data/settings.json"
if [ -f "$SETTINGS_FILE" ] && command -v python3 &>/dev/null; then
    echo "Reading rooms from settings..."
    # Extract host:connectPort pairs from settings
    ROOMS=$(python3 -c "
import json, sys
try:
    with open('$SETTINGS_FILE') as f:
        data = json.load(f)
    rooms = data.get('devices', {}).get('rooms', [])
    for r in rooms:
        host = r.get('host', '')
        port = r.get('connectPort', 5555)
        if host:
            print(f'{host}:{port}')
except Exception:
    pass
" 2>/dev/null)

    if [ -n "$ROOMS" ]; then
        while IFS= read -r target; do
            echo "Connecting to ${target}..."
            timeout 10 adb connect "$target" 2>&1 | head -1
        done <<< "$ROOMS"
        sleep 2
        echo "ADB device status:"
        adb devices 2>/dev/null | grep -v "^List"
    else
        echo "No rooms configured in settings (will be configured via UI)."
    fi
else
    # Fallback: if SHIELD_PLEX_IP env var is set, connect to it
    if [ -n "${SHIELD_PLEX_IP}" ]; then
        ADB_PORT=${SHIELD_ADB_PORT:-5555}
        echo "Connecting to ${SHIELD_PLEX_IP}:${ADB_PORT} (from env)..."
        timeout 10 adb connect "${SHIELD_PLEX_IP}:${ADB_PORT}" 2>&1 | head -1
        sleep 2
    else
        echo "No settings file or SHIELD_PLEX_IP found. ADB will be configured via UI."
    fi
fi

echo "================================================"
echo "Starting Luma application..."
echo "================================================"

# Start the application with gunicorn (see gunicorn.conf.py)
exec gunicorn -c gunicorn.conf.py app.main:app
