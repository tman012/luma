#!/bin/bash

# Entrypoint script for Luma
# Connects to configured ADB devices from settings, then starts gunicorn.

echo "================================================"
echo "Luma - Starting"
echo "================================================"

# Set HOME for ADB (LUMA_ADB_HOME overrides for test key persistence)
export HOME="${LUMA_ADB_HOME:-/app/home}"
mkdir -p "$HOME/.android"

# Explicit disable: never start ADB (dev compose). MINIMAL alone must NOT imply this —
# test sets LUMA_ADB_HOME + LUMA_ADB_COMMANDS so Pair/Connect can run.
_ADB_OFF=$(echo "${LUMA_DISABLE_ADB:-}" | tr '[:upper:]' '[:lower:]')
if [ "$_ADB_OFF" = "1" ] || [ "$_ADB_OFF" = "true" ] || [ "$_ADB_OFF" = "yes" ] || [ "$_ADB_OFF" = "on" ]; then
    echo "LUMA_DISABLE_ADB is set: skipping ADB server and device connections."
    echo "================================================"
    exec gunicorn -c gunicorn.conf.py app.main:app
fi

_MINIMAL=$(echo "${LUMA_MINIMAL:-}" | tr '[:upper:]' '[:lower:]')
_PAIR_CFG=
if [ -n "${LUMA_ADB_COMMANDS:-}" ] || [ -n "${LUMA_ADB_HOME:-}" ]; then
    _PAIR_CFG=1
fi

# Start ADB when Pair is configured (test) or this is a full install (not MINIMAL).
if [ -n "$_PAIR_CFG" ] || { [ "$_MINIMAL" != "1" ] && [ "$_MINIMAL" != "true" ]; }; then
    adb kill-server 2>/dev/null
    timeout 5 adb start-server 2>/dev/null
    if [ -n "$_PAIR_CFG" ]; then
        echo "ADB server ready (LUMA_ADB_HOME/COMMANDS). Pair/Connect via UI; no boot auto-connect."
    fi
fi

# Full install: connect rooms from settings (or SHIELD_PLEX_IP). Skip on MINIMAL —
# test must not touch TVs until the owner taps Pair.
if [ "$_MINIMAL" != "1" ] && [ "$_MINIMAL" != "true" ]; then
    SETTINGS_FILE="/app/data/settings.json"
    if [ -f "$SETTINGS_FILE" ] && command -v python3 &>/dev/null; then
        echo "Reading rooms from settings..."
        ROOMS=$(python3 -c "
import json
try:
    with open('$SETTINGS_FILE') as f:
        data = json.load(f)
    rooms = data.get('devices', {}).get('rooms', [])
    for r in rooms:
        for d in (r.get('devices') or [r]):
            if not isinstance(d, dict):
                continue
            host = d.get('host') or r.get('host') or r.get('ip') or ''
            port = d.get('connectPort') or r.get('connectPort') or r.get('adb_port') or 5555
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
        if [ -n "${SHIELD_PLEX_IP}" ]; then
            ADB_PORT=${SHIELD_ADB_PORT:-5555}
            echo "Connecting to ${SHIELD_PLEX_IP}:${ADB_PORT} (from env)..."
            timeout 10 adb connect "${SHIELD_PLEX_IP}:${ADB_PORT}" 2>&1 | head -1
            sleep 2
        else
            echo "No settings file or SHIELD_PLEX_IP found. ADB will be configured via UI."
        fi
    fi
fi

echo "================================================"
echo "Starting Luma application..."
echo "================================================"

exec gunicorn -c gunicorn.conf.py app.main:app
