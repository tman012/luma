# Luma Bot API surface

Base URL (LAN): `http://192.168.1.62:5001` (also via Caddy `https://tflixapp.com` / LAN hostname)

## Auth

Two methods (same as Alfred / SPA):

1. **Service API key** (preferred for bots): header `X-API-Key: <SERVICE_API_KEY>`
   - Env inside `luma` container: `SERVICE_API_KEY`
   - `before_request` treats a matching key as admin user `"Alfred"` (`id=0`)
2. **Session cookie**: `luma_session=<token>` from `POST /api/auth/login` `{email|name, pin}`

Notes:
- `GET /api/settings/rooms` **requires** auth.
- `POST /api/player/play`, `/api/plex/playqueue`, `/api/watchlist`, `/api/indexer/grab` currently accept unauthenticated LAN calls (validation-only 400), but bots should always send `X-API-Key`.

---

## SPA endpoints (canonical — match app exactly)

### Rooms
`GET /api/settings/rooms` → `{ rooms: [{id,name,devices:[{id,name,host,platform,connectPort,services:[{id,type,...}]}]}], defaultPlaybackTarget }`

Living Room (live):
- room `903c8ca9-f9c1-462b-bcc4-44712a777868`
- device `529d415f` (Living Room TV, `192.168.1.108`)
- plex service `1374c89c`

### Library search
`GET /api/library/search?q=...` → `{ movies: [...], tv_shows: [...], ... }`
Each item has `rating_key` / `ratingKey`, `title`, `year`, `type`, `tmdb_id`.

### Shuffle play queue
`POST /api/plex/playqueue`
```json
{ "rating_key": 32133, "shuffle": true }
```
→ `{ "success": true, "play_queue_id": "22138", "selected_rating_key": "32357", "shuffled": true }`

### Play (send-to-room)
`POST /api/player/play`
```json
{
  "rating_key": 32133,
  "room_id": "903c8ca9-f9c1-462b-bcc4-44712a777868",
  "device_id": "529d415f",
  "service_id": "1374c89c",
  "client_ip": "192.168.1.108",
  "play_queue_id": 22138,
  "resume_offset": 0
}
```
SPA shuffle flow (`PlaybackModal`): create playqueue → `startPlayback(..., playQueueId, deviceId, serviceId)`.
Omit `play_queue_id` for normal play. `resume: true` auto-fills offset from Plex.

### Movie download (Download / Add button)
`POST /api/watchlist`
```json
{ "tmdb_id": 123, "title": "Some Movie", "year": 2024, "quality_profile": "TFlix", "media_type": "movie" }
```
→ `{ success, action: "added"|"updated", radarr_id, search_issued, message }`
Idempotent Radarr ensure + search (same as ExpandedLayout upgrade / MobileUpgradeSheet).

### Specific release grab (Sources panel)
`POST /api/radarr/movie/grab` `{ guid, indexer_id, movie_id, tmdb_id?, title? }`

### Latest sheet grab
`POST /api/indexer/grab` `{ guid, indexer_id, tmdb_id?, title?, year?, torrent_name?, info_hash?, download_url?, ... }`

### TV download
`POST /api/tv/watchlist` (Sonarr ensure; see sonarrApi / watchlist TV path)
`POST /api/sonarr/episode/download` `{ guid, indexerId, ... }` via `downloadRelease`

### Queue / progress
`GET /api/radarr/queue` → `{ downloads: [...] }`

---

## Bot convenience wrappers (`/api/bot/*`)

Registered in `app/bot_api.py`. Resolve room-by-name / title → then call the SPA routes above in-process.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/bot/rooms` | List rooms (wraps settings/rooms) |
| GET | `/api/bot/search?q=` | Fast library lookup (`/api/library/search/fast`) |
| GET | `/api/bot/library?title=` | Same as search; adds `owned` bool |
| POST | `/api/bot/play` | title/rk + room name + optional shuffle → playqueue + play |
| POST | `/api/bot/download` | title/tmdb → `/api/watchlist` or `/api/tv/watchlist` |


### Fast library lookup (`GET /api/bot/search` / `GET /api/bot/library`)

Uses the in-memory Plex index (`GET /api/library/search/fast`) -- typically a few
milliseconds. Falls back to live Plex `/api/library/search` only if the fast
index is down. Prefer these for "do I already have X?" checks.

```bash
curl -sS -H "X-API-Key: $KEY" \
  "http://192.168.1.62:5001/api/bot/library?title=Junior"
```

### Download chain (bot -> library)

`POST /api/bot/download` -> `POST /api/watchlist` (Radarr ensure + search) ->
qBittorrent (Radarr category) -> Luma `auto_renamer.check_radarr_downloads` ->
`process_completed_torrent` (copy/rename into `/media/movies|recent|...`).

Radarr Completed Download Handling stays OFF; the app owns move/import.
Do **not** call qBit/Radarr clients directly from the bot.

SPA equivalents for a specific release (Sources panel):
`POST /api/indexer/grab` / `POST /api/downloads/add` + `POST /api/downloads/track`
(same `process_completed_torrent` path via manual tracking).

### `POST /api/bot/play`
```json
{
  "title": "Paw Patrol",          // or rating_key: 32133
  "media_type": "show",           // optional hint
  "room": "Living Room",
  "shuffle": true,
  "device_id": "529d415f",        // optional; defaults to plex-capable device
  "service_id": "1374c89c"
}
```

### `POST /api/bot/download`
```json
{ "title": "Some Movie", "year": 2024, "quality_profile": "TFlix", "media_type": "movie" }
```

---

## Example curls

### (a) Shuffle Paw Patrol on Living Room
Raw SPA (two calls):
```bash
KEY=... # SERVICE_API_KEY from luma env
# 1) playqueue
curl -sS -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"rating_key":32133,"shuffle":true}' \
  http://192.168.1.62:5001/api/plex/playqueue
# 2) play (use play_queue_id from step 1)
curl -sS -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"rating_key":32133,"room_id":"903c8ca9-f9c1-462b-bcc4-44712a777868","device_id":"529d415f","service_id":"1374c89c","client_ip":"192.168.1.108","play_queue_id":PQ_ID}' \
  http://192.168.1.62:5001/api/player/play
```

One-shot bot wrapper:
```bash
curl -sS -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"title":"Paw Patrol","room":"Living Room","shuffle":true,"media_type":"show"}' \
  http://192.168.1.62:5001/api/bot/play
```

### (b) Download a movie
```bash
curl -sS -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"title":"The Matrix","year":1999,"quality_profile":"TFlix"}' \
  http://192.168.1.62:5001/api/bot/download
# equivalent SPA:
curl -sS -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"tmdb_id":603,"title":"The Matrix","year":1999,"quality_profile":"TFlix","media_type":"movie"}' \
  http://192.168.1.62:5001/api/watchlist
```

Do **not** use Family Room Shield in routine tests; Living Room is OK.
