#!/usr/bin/env bash
# Living Room GTV ONLY — native Plex Shuffle via ADB UI
set -euo pipefail
ROOT=/home/tbosley02/media-search-app
export PYTHONPATH="$ROOT:${PYTHONPATH:-}"
export ADB_PATH="${ADB_PATH:-/home/tbosley02/Android/Sdk/platform-tools/adb}"
TOKEN=$(grep -E '^PLEX_TOKEN=' "$ROOT/.env" | head -1 | cut -d= -f2- | tr -d "\"'")
exec python3 -m app.gtv_adb_shuffle \
  --ip 192.168.1.108 \
  --port 5555 \
  --rating-key "${1:-37955}" \
  --plex-url http://192.168.1.25:32400 \
  --plex-token "$TOKEN" \
  --client-id f1ffa6f2e5d19010-com-plexapp-android
