# GTV ADB Native Shuffle — RESULT

**Date:** 2026-09-06 (America/New_York)  
**Device:** Living Room only — `192.168.1.108:5555` (Smart TV Pro / chromecast_gtv)  
**Plex clientIdentifier:** `f1ffa6f2e5d19010-com-plexapp-android`  
**Test show:** The Office (US) `ratingKey=37955`

## What works

Native Plex Shuffle via ADB UI automation (same path as manual TV Shuffle):

1. Wake + force-stop/relaunch Plex
2. Reveal sidebar (`DPAD_LEFT`) → open **Search**
3. Type show title → tap matching **Show** result
4. On show details → tap **More (…)**** → tap **Shuffle**
5. Verify Living Room session `playing` for that show

E2E (automated): started **S5E10 Moroccan Christmas**, stayed `playing` ~30s+ with advancing `viewOffset`.

This creates Plex’s native local shuffled continuous play queue (not a homemade playlist, not remote `playMedia` / queue mutation).

## How to invoke

### Standalone (host / docker-server)

```bash
cd /home/tbosley02/media-search-app
export PYTHONPATH=/home/tbosley02/media-search-app
# optional on host:
# export ADB_PATH=/home/tbosley02/Android/Sdk/platform-tools/adb
python3 -m app.gtv_adb_shuffle --ip 192.168.1.108 --port 5555 --rating-key 37955
```

Or:

```bash
./data/gtv_adb_shuffle/run_office_shuffle.sh 37955
```

Power off Living Room only:

```bash
python3 -m app.gtv_adb_shuffle --ip 192.168.1.108 --port 5555 --power-off
# or: adb -s 192.168.1.108:5555 shell input keyevent KEYCODE_SLEEP
```

### Luma (wired)

- Module: `app/gtv_adb_shuffle.py`
- `_plex_play_gtv` in `app/main.py`: when `play_queue_id` is set (series/season Shuffle from UI), **chromecast_gtv** uses ADB UI native Shuffle instead of failing `playMedia`.
- **Shield / Family Room / android_tv** path unchanged (HTTP `playMedia` + playQueue).

## Failed approaches (do not retry as primary)

- Companion `playMedia` (GTV not in `/clients`)
- Injecting external PlayQueue via deep-link
- `PUT /playQueues/{id}/shuffle` on GTV-owned queue (Next ignores)
- Homemade random playlists as fake shuffle

## Artifacts

UI dumps + screenshots under this directory (`data/gtv_adb_shuffle/`).
