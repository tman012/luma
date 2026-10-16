# Luma

Luma is a self-hosted home for your Plex library: browse and search what you
own, see what's new, and play it on the TVs in your house. Add TMDB, Radarr,
Sonarr and qBittorrent later for discovery, requests and downloads.

## Quick start

You need Docker with the Compose plugin and a Plex Media Server.

```sh
git clone <this repository's URL> luma
cd luma
cp .env.example .env    # optional, see below
docker compose up -d --build
```

Open `http://<server-ip>:5001` and tap **Get Started**: sign in with Plex,
choose when Luma asks for a PIN, and you're done. Nothing else is required;
connect the other services from **Settings** whenever you like.

### Minimal `.env`

```ini
LUMA_PORT=5001        # web UI port
LUMA_DATA_DIR=./data  # settings, database and keys live here
```

Everything else in `.env.example` is optional. Secrets (session key, service
key, push keys) are generated on first start and kept in the data folder.

### Port

Luma listens on **5001** by default. The port saved under Settings → Network
applies (gunicorn and the container health check both read it); `LUMA_PORT`
in the environment overrides it. The container uses host networking so it can
reach Plex and TVs on your network.

## Access

**Settings → Security → Require PIN**

- **Disabled for local addresses** (default, and what an install with no
  saved choice uses): devices on your home network open Luma directly;
  anyone else signs in.
- **Enabled**: everyone signs in.

"Local" means a private (LAN) client address. Behind a reverse proxy, add its
address under **Trusted proxies**; only then is its `X-Forwarded-For` (and
`X-Forwarded-Proto` / `X-Forwarded-Host`) believed. An optional
`security.trusted_proxy_header` (off by default) names a different client
address header sent by those proxies.

No-sign-in access also needs a known address in the browser bar: an IP
address, `localhost`, or a name listed under **Host names** (for example
`luma.lan`). A trusted proxy's `X-Forwarded-Host` is only believed for those
names. Any other name gets the sign-in screen, which blocks DNS-rebinding
pages. Luma sends no CORS headers, and API calls made by another site with the
no-sign-in access are refused.

Account requests (**Sign up** on the sign-in screen) are accepted from the
local network; from anywhere else only when **Allow sign-up from anywhere** is
on (off by default).

## Deploy Notes

- **Cloudflare / home-WAN trust is removed.** `CF-Connecting-IP` is ignored
  and the home WAN address no longer counts as local. Only direct LAN
  connections and `X-Forwarded-For` from trusted proxies count as local.
- **Before cutover, Ops checks how the kiosk and every home device reach
  Luma.** Any device that uses the tunnel hostname instead of the LAN IP is
  either pointed at the LAN IP or signed in once (30-day remember-me), so the
  kiosk never lands on a sign-in screen.
- Existing installs keep LAN access without sign-in: with no saved
  `security.auth_required`, Luma uses `disabled_local`.
- The old `luma_session` cookie is no longer accepted; browsers that send it
  get it expired and sign in again with `luma_auth_session` when remote.
- The container runs `gunicorn -c gunicorn.conf.py app.main:app` (1 worker,
  8 threads); startup work runs in `start_services()` from `post_worker_init`.
- **Host names.** A home device that opens Luma by a name (not an IP address)
  needs that name under Settings → Security → Host names, or it gets the
  sign-in screen. Behind a reverse proxy, list the public name there too so
  its `X-Forwarded-Host` is honored.
- Remote sign-up is off by default; turn on **Allow sign-up from anywhere** if
  friends should keep requesting accounts from outside.
- The health check is `scripts/healthcheck.py` (follows port, URL base and
  HTTPS from settings).

## Updating

```sh
git pull
docker compose up -d --build
```
