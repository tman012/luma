# Trackers (built-in Prowlarr replacement)

Luma searches the owner's trackers directly, with no Prowlarr or Jackett.

## Data

- `$LUMA_DATA_DIR/trackers.json` (0600): trackers, settings, status. Secret fields are encrypted.
- `$LUMA_DATA_DIR/tracker_secrets.key` (0600): the per-install encryption key, created when the first secret is saved.

Back up both together. Without the key, saved credentials are unreadable and each tracker shows "Credentials unreadable" until its secrets are entered again.

A fresh install has neither file and zero trackers. Nothing is preconfigured.

## Types

Torznab, Newznab, AvistaZ, BeyondHD, CinemaZ, ExoticaZ, IPTorrents, MoreThanTV, PrivateHD. Each type lists its own fields (`GET /api/trackers/types`); fields with `"secret": true` are masked.

## API (owner only)

| Method | Path | |
|---|---|---|
| GET | /api/trackers/types | types, field schemas, category options |
| GET | /api/trackers | trackers with `status.state` ok, error or untested |
| POST | /api/trackers | add |
| GET / PUT / DELETE | /api/trackers/:id | read, edit, delete |
| PUT | /api/trackers/:id/enabled | `{"enabled": bool}` |
| POST | /api/trackers/:id/test | test the saved tracker, updates status |
| POST | /api/trackers/test | test an unsaved form; `id` fills masked secrets |

Secrets come back as `********`. Sending `********` back keeps the stored value; an empty string clears an optional secret. Validation errors answer 400 with `{"error": "Invalid", "fields": {"url": "Invalid URL"}}`. Test answers `{"ok": true, "message": "Connected"}` or `{"ok": false, "error": "Unauthorized"}`.

Secrets are never logged, never sent to the browser and never put in a URL Luma shows or logs. BeyondHD's API takes its key in the request path and Torznab/Newznab take `apikey` as a query parameter; those requests go only to the tracker itself and are not logged.
