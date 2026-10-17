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

## Reverse proxy

`security.trusted_proxies` starts empty, so `X-Forwarded-For` is ignored and
every proxied request looks like it comes from the proxy. Behind a reverse
proxy, set it (Settings, or `security.trusted_proxies` in settings.json,
comma-separated) or LAN users get 401:

- proxy on the same host with host networking (for example Caddy):
  `127.0.0.1/32, ::1/128`
- otherwise: the proxy's own IP as a single address (`/32` or `/128`)

List only proxies you control.

The proxy must also pass the original `Host` (or `X-Forwarded-Host`): Luma
uses it to tell the main site from the request-portal host name and LAN names
from public ones. Caddy's `reverse_proxy` keeps the client's `Host` and sets
`X-Forwarded-For` / `X-Forwarded-Host` by default; don't override them with
`header_up Host {upstream_hostport}`. Example (replace the placeholders; with
host networking Luma listens on the host's port):

```caddyfile
luma.example.com, requests.example.com {
    reverse_proxy 127.0.0.1:<luma port> {
        header_up Host {host}
        header_up X-Forwarded-Host {host}
    }
}
```

Then set `security.trusted_proxies` to `127.0.0.1/32, ::1/128` and
`request_portal.hostname` to the portal name.

## HTTPS on the plain-HTTP port

Gunicorn serves plain HTTP on the Luma port (5001 by default). Browsers that
try https:// first used to hang there until timeout. The worker class in
gunicorn.conf.py (app.tls_guard.TLSRejectingThreadWorker, a gthread worker)
now closes a connection right away when its first bytes are a TLS
ClientHello, so the handshake fails at once and the browser can fall back to
http://. No compose, entrypoint or Caddy change is needed. When Luma itself
serves HTTPS (a certificate is set) the check is off. Luma never sends
Strict-Transport-Security; keep it that way.

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

## Fresh-install test build (luma-test, port 5002)

`docker-compose.test.yml` runs a second, empty Luma with `LUMA_MINIMAL=1`:
Get Started (Plex sign-in, server pick, Set password, access), sign-in and the
main screen. No background threads start, and every other route returns 404.

```bash
docker compose -p luma-test -f docker-compose.test.yml up -d --build
```

- Container `luma-test`, image tag `luma:minimal-test` (never `luma:latest`),
  port 5002 (host networking, like production).
- Its own named volumes `luma-test_luma-test-data` and
  `luma-test_luma-test-cache`. No `.env`, host data folder, media folders,
  ADB keys or Plex database are mounted.
- Start over from an empty install:
  `docker compose -p luma-test -f docker-compose.test.yml down -v`.
- Production (`docker compose up` in the production folder, port 5001) is not
  affected; always pass `-p luma-test -f docker-compose.test.yml` for the test
  build.

With `LUMA_MINIMAL` unset, Luma runs exactly as before.
