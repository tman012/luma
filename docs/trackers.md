# Trackers (built-in Prowlarr replacement)

Luma searches the owner's trackers directly, with no Prowlarr or Jackett.

## Data

- `$LUMA_DATA_DIR/trackers.json` (0600): trackers, settings, status. Secret fields are encrypted.
- `$LUMA_DATA_DIR/tracker_secrets.key` (0600): the per-install encryption key, created when the first secret is saved.
- `$LUMA_DATA_DIR/tracker_guard.json`: request counts and backoffs, so a restart can't reset them.

Back up both together. Without the key, saved credentials are unreadable and each tracker shows "Credentials unreadable" until its secrets are entered again.

A fresh install has neither file and zero trackers. Nothing is preconfigured.

## Types

Torznab, Newznab, AvistaZ, BeyondHD, CinemaZ, ExoticaZ, IPTorrents, MoreThanTV, PrivateHD. Each type lists its own fields (`GET /api/trackers/types`); fields with `"secret": true` are masked.

## API (owner only)

Every route needs the install owner signed in with a session. Without one, including on the LAN with sign-in off, the answer is 401; other users and the service API key get 403.

| Method | Path | |
|---|---|---|
| GET | /api/trackers/types | types, field schemas, category options |
| GET | /api/trackers | trackers with `status.state` ok, error or untested |
| POST | /api/trackers | add |
| GET / PUT / DELETE | /api/trackers/:id | read, edit, delete |
| PUT | /api/trackers/:id/enabled | `{"enabled": bool}` |
| POST | /api/trackers/:id/test | test the saved tracker, updates status |
| POST | /api/trackers/test | test an unsaved form; `id` fills masked secrets only when the URL is unchanged |

Secrets come back as `********`. Sending `********` back keeps the stored value; an empty string clears an optional secret. A stored secret only goes to the URL it was saved with: changing the URL clears the stored secrets (required ones must be entered again, or the edit answers 400), and a Test whose URL differs from the saved one never uses them.

Test goes through the same limiter as searches: it counts toward 40 user requests per tracker per hour, 300 per tracker per day and 120 per hour across all trackers (AvistaZ family: 20 per hour, burst 5). A 429 answer starts a backoff that honors `Retry-After` (else 1 h, doubling up to 6 h) and a ban answer a 24 h one; both are saved and refuse Test until they end. An unsaved form counts against its site host. Validation errors answer 400 with `{"error": "Invalid", "fields": {"url": "Invalid URL"}}`. Test answers `{"ok": true, "message": "Connected"}` or `{"ok": false, "error": "Unauthorized"}`.

Secrets are never logged, never sent to the browser and never put in a URL Luma shows or logs. BeyondHD's API takes its key in the request path and Torznab/Newznab take `apikey` as a query parameter; those requests go only to the tracker itself and are not logged.

## Network safety

Luma only contacts public addresses. Before every request and every redirect it resolves the tracker's host, refuses it if any answer is loopback, private (RFC 1918 or unique-local), link-local (cloud metadata included), CGNAT, 0.0.0.0, multicast, reserved, or one of this host's own addresses, or if the port is Luma's or Prowlarr's/Jackett's (9696, 9117, 5001, 5002, and `LUMA_PORT`). The connection then goes to the checked address only, so a DNS change in between can't redirect it. Redirects must keep the scheme (http to https is fine), host and port. Site types (everything but Torznab and Newznab) need an https URL. A whole request, redirects and body included, ends after 60 s. Test answers "Address not allowed" for a refused target.
