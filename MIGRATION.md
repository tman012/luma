# Migrating an existing install to this branch

This branch removes the legacy Jinja UI, dead code, the android-kiosk app and every
install-specific value (IPs, domains, e-mail addresses, chat ids, host paths). Values that
used to be hardcoded now come from `.env` or `data/settings.json`, and they default to empty. An
existing install keeps working once it supplies them. Run through this list **before** you
pull and rebuild.

## 1. Add the new `.env` values

`docker-compose.yml` is now generic. Ports, image name, host folders and the values below
are read from `.env` (see `.env.example`). It has no `env_file:`, so only the listed
variables reach the container.

| Variable | What it replaces | Needed when |
|---|---|---|
| `APP_URL` | The hardcoded public URL in e-mail and Telegram links | You want links to use your public URL. If it is blank, the request's own URL is used. |
| `TELEGRAM_ADMIN_CHAT_ID` | The hardcoded admin chat id | You get Telegram admin alerts (new requests, pending users). It can also go in `data/telegram_bot.json` as `admin_chat_id`. If it is blank, alerts are skipped. |
| `TELEGRAM_ACTION_SECRET` | Signed the Telegram approve/deny links | Removed. Links are now signed with `SECRET_KEY` (full HMAC-SHA256), expire after 24 hours, work once, and open a confirm page; nothing changes until you press its button. Links sent before the upgrade no longer work. |
| `KODI_NAS_BASE` | The hardcoded `smb://<nas>` share root | You play through Kodi and rely on the share fallback. `settings.kodi.nas_base` still wins when it is set. |
| `TZ` | It was set in compose | Schedules (03:00 scan, 22:50 roulette, ...) follow the container clock. **Set it**, otherwise it is UTC. |
| `PLEX_DB`, `LUMA_PLEX_DB_DIR` | Compose `environment:` and the `/mnt/plexdb` mount | You use the read-only Plex DB. |
| `SHIELD_PLEX_IP` | The compose default | Optional. It only seeds the first room on a brand-new settings file. |
| `LUMA_IMAGE`, `LUMA_CONTAINER_NAME`, `LUMA_PORT` | Fixed names in compose | You want to keep your existing image/container name or port (default `luma:latest`, `luma`, `5001`). |
| `LUMA_DATA_HOST_DIR`, `LUMA_CACHE_HOST_DIR`, `LUMA_ADB_KEYS_DIR`, `LUMA_DOWNLOADS_DIR`, `LUMA_MOVIES_DIR`, `LUMA_RECENT_DIR`, `LUMA_TV_DIR`, `LUMA_4K_DIR`, `LUMA_FULLRIPS_DIR`, `LUMA_TV_4K_DIR`, `LUMA_TV_REMUX_DIR` | Hardcoded host paths | Always: point them at your existing folders (defaults are `./data`, `./cache`, `./.android`, `./media/...`). Older `.env` files used `LUMA_DATA_DIR` for the host data folder: rename it to `LUMA_DATA_HOST_DIR` (inside the container `LUMA_DATA_DIR` is always `/app/data`). |
| `LUMA_CA_CERT_PATH` | Optional | Your local-CA root lives somewhere other than `/caddy-data/caddy/pki/authorities/local/root.crt`. |

## 2. Move personal extras into `docker-compose.override.yml`

`docker-compose.override.yml` is gitignored, and Compose merges it automatically. Put anything specific to one host in this file:
- **Live-code bind mounts**, e.g. `./app:/app/app` and `./static:/app/static`. Keep the static mount if the host's
  `static/` holds untracked files such as `hero_cache/`.
- **The reverse proxy.** The `Caddyfile` and the `caddy` service were removed from the repo. Before pulling, copy your
  `Caddyfile` to `Caddyfile.local` (also gitignored). Then define the `caddy` service in the override with
  `./Caddyfile.local:/etc/caddy/Caddyfile:ro`. Mount its data volume into `luma` at `/caddy-data:ro`
  if you serve `/ca.crt`. If the proxy service keeps its old container name, `docker compose up` keeps using it.

Check the merged result with `docker compose config` before `up -d`.

## 3. Settings migrations (automatic, on first start)

### Automation switches

Settings → Automation is now a short list of features. Each one has a switch plus its key options:

| Feature (switch) | Background jobs it controls | Activity tag |
|---|---|---|
| **Auto-grab** (`automation.auto_grab.enabled`) | Sources: Latest feed (`latest_auto_grab`), Trending movies (`trending_watchlist`), Trending TV (`trending_tv_watchlist`), Anticipated (`anticipated_watchlist`), Now Available (`now_available_watchlist`) | unchanged |
| **4K & Upgrades** (`automation.upgrades.enabled`) | `upgrade_4k_auto_grab`, daily upgrade scan (`upgrade_scheduler`, `run_hour`) | unchanged |
| **Genre Roulette** | `genre_roulette` | unchanged |
| **Movie Night** | `movie_night` | unchanged |
| **Franchise Completer** | `franchise_completer` | unchanged |
| **Watchlist Release Dates** | `watchlist_digital_dates` | unchanged |

A grouped job runs only when **both** its group switch and its own "Use this source" toggle are on.
**New installs start with everything off.**

When an existing `settings.json` has no `auto_grab`/`upgrades` block yet, the
`_migrate_automation_features` migration does the following:
- It pins each job's current effective state. `upgrade_scheduler` and `watchlist_digital_dates` used to be on
  when their key was missing, so they are written as `enabled: true`.
- It turns a group switch on if any of its jobs was running.
- It leaves every interval, hour, limit and option untouched.

Net effect: exactly the jobs that ran before keep running, on the same schedules. Job threads, state files and
Activity tags did not change. `GET /api/automation/features` shows the effective state.

### Other settings

- `radarr.preferred_profile_name` / `sonarr.preferred_profile_name` (new, empty) replace a hardcoded profile name.
  They are only consulted when `default_quality_profile_id` is unset. If you relied on the old name lookup, set
  `default_quality_profile_id`, or put your profile name here.
- `qbittorrent.app_category` now drives every download Luma starts. Before, the category was hard-coded in three
  places. The default for new installs is `luma`. An existing install keeps whatever value its settings already hold, so
  make sure it is the category your earlier downloads used.
- `plex.search_section_ids` (new, empty) lists the Plex sections that quick search indexes. Before, the section ids were
  hard-coded; now every movie and TV section is discovered automatically. To index exactly the same sections as before,
  list their ids here.
- Daily Kiosk: the sports team and news feed no longer have built-in defaults (they still only show when enabled).
  Saved choices are kept. A custom feed that is not one of the presets still shows in the picker.
- Harmony volume buttons need the room's *Volume Device* to be set. There is no built-in fallback device anymore.
- `rooms` defaults to empty on a new install. Existing rooms are untouched.
- `push.vapid_email` defaults to empty and falls back to `LUMA_ADMIN_EMAIL`. Existing values are untouched.
- The AI/voice keys (OpenAI, ElevenLabs, Anthropic) are dropped from settings by an earlier migration. Remove them from `.env` too.

### Database location

Luma keeps using the database file it used before. The path is chosen in this
order:

1. `DATABASE_PATH` in the environment. The shipped `docker-compose.yml` sets
   `DATABASE_PATH=/app/data/media.db`, so a compose install keeps that exact
   file.
2. A saved `media.database_path` setting.
3. `media.db` in the data folder (`LUMA_DATA_DIR`, default `/app/data`).
   If that file does not exist yet but a database already sits at the old
   default `/app/data/media.db`, Luma uses the existing file and logs a
   warning that names both paths. It never creates a new, empty database next
   to an existing one.

To check, look for one `Database:` line in the container log after start:

    docker compose logs luma | grep "Database:"

It reads `Database: /app/data/media.db (from env)` (or `from setting` /
`from data dir`). A `Database: using the existing ... instead of ...` warning
means the old file was picked up; pin it so the choice is explicit.

To pin the path, keep `DATABASE_PATH` in the compose environment, or save
`media.database_path` (Settings, or `data/settings.json` under `media`) with the
full container path of the file. Both point at the same file; the environment
wins if both are set.

### Access and host names

- `security.access_mode` is deleted (an old `pin` becomes `auth_required: enabled`; `pin_remote`/`open` become
  `disabled_local`).
- `security.hostnames` is new and seeded **once**, only when the key doesn't exist yet: from names LAN clients
  used in the last 30 days (`data/lan_hosts.json`, which this release starts writing, so it is empty on the first
  upgrade); otherwise from private names in `APP_URL` plus the server's host name and its `.local` / `.lan`
  forms. The log line `Seeded security.hostnames: ...` shows the result. Clearing the field later never re-seeds.
- Without any setup, a LAN device can use an IP, `localhost`, the server's host name or a home-network name
  (`docker-server`, `*.local`, `*.lan`, `*.home.arpa`, `*.internal`). Other names need **Host names**. A name never
  skips sign-in for public, proxied-public or Cloudflare requests.
- `security.allow_signup` is new (off): sign-up from outside the LAN is refused until it is turned on.

## 4. Visible changes

- The legacy pages `/activity`, `/browse_libraries`, `/browse_media` and `/legacy/explore` redirect to the React
  app (`/`, `/browse`, `/explore`). The API routes that only those pages used now return 404.
- Notifications say "ready on Luma!". The CA download is named `Luma-CA.crt`.
- Settings → Services no longer has the Fire-tablet APK steps. `/api/version` still answers already-installed kiosks.
- TV season packs are now renamed episode by episode as intended. An undefined variable used to make every episode in a
  pack fail.
- The Daily Kiosk's leftover voice-assistant hint line is removed.
- The Luma wordmark is served as WebP (126 KB) with a 256 KB PNG fallback (it was 2.1 MB).

## 5. Clean up afterwards

- Delete `static/models/` (the old voice model, about 40 MB) from the host.
- Remove the stale `ANTHROPIC_*`, `OPENAI_*`, `ELEVENLABS_*`, `TWILIO_*` and indexer keys that `.env.example` no longer lists.
