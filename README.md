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

Luma listens on **5001** by default (`LUMA_PORT`). The container uses host
networking so it can reach Plex and TVs on your network.

## Access

**Settings → Security → Require PIN**

- **Disabled for local addresses** (default): devices on your home network
  open Luma directly; anyone else needs the PIN.
- **Always**: everyone enters the PIN.
- **Never**: no PIN.

Behind a reverse proxy or tunnel, add its address under **Trusted proxies**
so Luma can tell local and remote visitors apart.

## Deploy Notes

**Security update**: Cloudflare home-WAN trust is removed. Only direct LAN connections and X-Forwarded-For from trusted proxies count as local.

Before updating production:
- Check how the kiosk and home devices reach Luma
- Any device using the tunnel hostname instead of the LAN IP must either:
  - Be pointed at the LAN address, or
  - Sign in once (30-day remember-me)
- This ensures the kiosk never shows a login screen unexpectedly

## Updating

```sh
git pull
docker compose up -d --build
```
