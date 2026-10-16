# Luma Deployment Notes

## Folders and mounts

| Container path | Host side (compose variable, default) | Contents |
|---|---|---|
| `/app/data` (`LUMA_DATA_DIR`) | `LUMA_DATA_HOST_DIR`, `./data` | `media.db`, `settings.json`, `hero_content.json`, other state. Back this up. |
| `/app/cache` (`LUMA_CACHE_DIR`) | `LUMA_CACHE_HOST_DIR`, `./cache` | Rebuildable caches: `explore/`, `library/`, `hero/` (hero images). |
| `/app/spa` | none (built into the image) | The React SPA. Kept outside `/app/static` so a bind-mounted `./static` cannot hide it. Override with `LUMA_SPA_DIR`. |

`LUMA_DATA_DIR` and `LUMA_CACHE_DIR` are container paths and are set in
`docker-compose.yml`. Put host folders in `.env` only as `LUMA_DATA_HOST_DIR` /
`LUMA_CACHE_HOST_DIR`. Older `.env` files that set `LUMA_DATA_DIR=./data`
should rename it to `LUMA_DATA_HOST_DIR`. `HERO_CACHE_DIR` (container path)
defaults to `$LUMA_CACHE_DIR/hero` and normally stays unset.

Both mounts must be writable by the container user. On start Luma creates and
deletes a temp file in each directory and opens every SQLite database there for
write; if any check fails it logs the reason and exits with status 1 (so a
read-only or wrongly owned mount fails the deploy instead of logging
"readonly database" errors later).

## Hero cache

- Images live in `$LUMA_CACHE_DIR/hero` and are served at `/static/hero_cache/<file>`.
- URLs from before the move still work: if a file is not in the hero cache dir,
  it is served from the legacy `<static>/hero_cache` folder.
- Refresh/heal runs only in a background scheduler, never from a request, and
  at most one refresh runs at a time (also across processes sharing the cache).
- On start: no `hero_content.json` -> one refresh. A `hero_content.json` that
  references images missing from the hero cache dir (e.g. after a container
  recreate with a fresh cache) -> heal immediately. Then a daily refresh at
  05:00 plus a heal check every 5 minutes.

## First deploy / upgrade

```bash
mkdir -p data cache
docker compose up -d --build
```

Clear the hero image cache (it is rebuilt on the next start):

```bash
docker compose stop luma && rm -rf cache/hero && docker compose start luma
```

Backup: `tar czf luma-data.tgz data` (the cache folder can be rebuilt).

## Single gunicorn worker (required)

gunicorn.conf.py runs `workers = 1` with threads. The tracker guard
(GUARD_LIMITS in app/indexer_api.py, used by Prowlarr, Torznab and the
built-in indexer manager alike), the /api/indexer/download token store and
the Telegram used-link store hold live state in the one process and persist
it to the data dir. Do not raise `workers`: each worker would apply its own
limits and they would overwrite each other's state files.
