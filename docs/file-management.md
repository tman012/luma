# File management (luma-test / minimal builds)

Movies only for now. Luma copies (or hardlinks) finished downloads into library
folders the owner adds in Settings > Library paths. It is a separate, empty
store (`file_management.json` in the data dir, mode 0600). The seeded
`media.*` settings and `*_ROOT` defaults are never read or shown.

## Isolation from prod (one shared qBittorrent)

- A grab on a minimal build is added to qBit with the tag `LUMA_TEST_QBIT_TAG`
  (default `luma-test`) and tracked by hash in Luma's own store.
- The mover asks qBit only for hashes it tracks (`torrents/info?hashes=`); it
  never lists the whole client. A tracked torrent without the tag is dropped,
  never copied (for example, a release prod grabbed first).
- Only the tag counts, never a category. A `LUMA_TEST_QBIT_TAG` equal to an
  app's category (`luma`, `radarr`, `sonarr`, ...) is refused and `luma-test`
  used instead, with a warning in the log.
- Prod's AutoRenamer skips torrents carrying the tag, in both its tracked-download path and its Radarr category scan. Nothing
  else in prod changed. Edge case: if prod grabs a release luma-test already
  grabbed (same hash, now tagged), prod drops it rather than copying it.
- A torrent removed from qBit is dropped after 3 consecutive misses. qBit
  being unreachable is not a miss.
- Prod's Downloads Guard and the storage pill's manual cleanup skip each tagged
  torrent's own entry. A folder that holds one (a category folder) is never
  deleted whole; its other entries are offered one by one. Both delete nothing
  while qBittorrent can't be read, and the manual cleanup is owner only.
- Destructive file actions need the owner signed in with a session, even
  when LAN sign-in is off (PM ruling). They are: the manual cleanup
  (`/api/storage/downloads/cleanup`), removing a download with
  `deleteFiles=true` (`/api/downloads/remove/<hash>`), a real Downloads Guard
  run (`/api/downloads-guard/run` with `"dry_run": false`), and any TV or
  movie delete later. Removing a download without its files and a Downloads
  Guard dry run keep today's access. Routes use `start_flow.require_signed_in_owner` (or the
  `signed_in_owner_required` decorator): the implicit LAN owner, the service
  key (kiosk, companion apps) and no session get 401 with `"sign_in": true`;
  other admins and plain, guest and Plex-approved users get 403. The storage
  sheet shows "Sign in to free up space", Activity "Sign in to delete
  files" and Downloads Guard "Sign in to clean up" instead of the delete
  (also after a 401), and /login
  shows the form to an implicit LAN owner sent there for that action.
  Everything else keeps today's LAN behavior.
- The cleanup and Downloads Guard run routes take an optional JSON object;
  any other non-empty body (a list, string, number, `null`, or text that isn't
  JSON) gets 400 `{"error": "Invalid request"}` (`main._json_object_body`). The
  run route checks the body first; cleanup checks the session first.
- Settings that turn on or widen automatic deletion need the signed-in owner
  too (PM ruling): `PUT /api/settings` when the change turns on or widens
  Downloads Guard. `app/auto_delete_guard._widens_auto_delete(old, new)`
  (shared with cursor/sources-followup) compares the saved settings with the
  result of the same merge the save does, reading values with Downloads
  Guard's own parsers and limits. A change widens when it:
  - turns `automation.downloads_guard.enabled` on;
  - raises `min_free_gb` (the guard fires at more free space);
  - raises `reclaim_gb` (each run deletes more);
  - raises `daily_cap_gb`, or sets it to 0 (no cap);
  - lowers `check_interval_minutes` (runs more often);
  - changes `media.downloads_root` while the guard is on (it would delete in
    another folder).

  Turning the guard off (even with other edits in the same save) or
  tightening any of these stays open to any admin, as does the tracker
  preference, which only sets what is spent first. The implicit LAN owner,
  the service key and no session get 401 `"sign_in": true`; other admins 403.
  A refused save changes nothing. In Settings, Save becomes "Sign in to save"
  after a 401 and opens /login; a new edit to that card brings Save back.
- Tracker routes (`/api/trackers*`) answer the service key with 401
  `"sign_in": true` too, like the routes above.
- `LUMA_TEST_QBIT_TAG` is refused (falls back to `luma-test`) when it equals an
  app's category: Radarr/Sonarr/..., `LUMA_QBIT_APP_CATEGORY`, or the
  `qbittorrent.app_category` setting.
- qBittorrent bans an IP after 5 failed sign-ins. The mover stops after 3
  rejected sign-ins and keeps that count on disk (`qbit_login.json`, 0600,
  keyed by a hash of the credentials), so a restart or a Save with the same
  credentials doesn't reset it. New credentials or a successful Test of the
  saved ones do.

## Mounts (Ops)

`docker-compose.test.yml` reads, with nothing hard-coded:

| Variable | Container path | Notes |
|---|---|---|
| `LUMA_TEST_DOWNLOADS_DIR` | `/downloads` (read-only) | Same path qBit saves to; `/downloads/incomplete` is skipped |
| `LUMA_TEST_MEDIA_DIR` | `/media` | Parent of the library folders; any subfolder can be added, spaces included |
| `LUMA_TEST_QBIT_TAG` | env | Default `luma-test`; set the same value on prod |
| `LUMA_BROWSE_ROOTS` | env | Folders the library-path picker may browse, `:` or `,` separated (default `/media`); the downloads folder and every configured Movies and TV library path are always added. Nothing outside them is listed or opened, symlinks pointing out included |
| `LUMA_DOWNLOADS_ROOT` | env | Any absolute path (default `/downloads`); the only folder the mover reads from, and the folder storage cleanup measures. When qBittorrent reports another path (e.g. `/downloads` while this container sees `/storage/downloads`), the Library paths path map is required; a grab whose save path doesn't map under it shows "Path not mapped" in Settings > Library paths and Activity right away |

Unset, both fall back to empty named volumes so the app still starts with zero
paths. `docs/examples/docker-compose.library-paths.yml` is a generic override
with placeholder host variables. The optional path map (Settings) translates
qBit's paths to Luma's when they differ.

Hardlink falls back to copy whenever linking fails. With `/downloads` mounted
read-only and as a separate mount from `/media`, that is always the case on
:5002 (EXDEV), so Copy is the effective mode there.

## Copying

- Destination: `<library path>/<Title (Year)>/<Title (Year)>.<ext>` (`- ptN`
  for multi-part). Rules are checked in order (released within N months or
  years, resolution, source); first enabled match wins, else the fallback.
- Existing destination: skipped and recorded as `exists`.
- Free space is checked before copying (size + 64 MB margin); a short disk, or
  ENOSPC/EDQUOT mid-copy, records `insufficient space` and is not retried.
- Files 0664, folders 0775. Running as root (euid 0), each new file and folder
  is chowned to the uid:gid of the configured library path (for example
  1000:1000); otherwise ownership is left alone. No chmod 777, no Telegram.
- Copies go to a hidden `.name.<random>.luma-partial` file (O_EXCL, 0600, no
  symlink follow), then are linked into place, or renamed with
  `RENAME_NOREPLACE` where links aren't supported. A partial that is already
  there is someone else's and is never touched; a failed copy removes its own
  partial only while it is still the same inode. Symlinks in downloads are
  never followed.
- Sources: only files inside `LUMA_DOWNLOADS_ROOT`, whatever save path qBit
  reports. Each file is opened `O_NOFOLLOW` and must be the same inode that
  was checked (device, inode and size from the scan, rechecked on the open
  file; a change waits for the next pass); a hardlink links that open inode.
  A save path outside the root waits for a path map (Settings) and is listed as `files not found` after an
  hour; changing the path map puts grabs skipped for a path error back in the
  queue.
- Destination: the library folder and the title folder are opened as directory
  descriptors; modes and owners are set on open descriptors, never by path.
  A failed pass removes the files it put in place and the folder it created.
- qBit's "don't create subfolder" layout (content path equal to the save
  path) files only the torrent's own files, listed by `torrents/files`.
- A library path inside, equal to, or holding the downloads folder is refused.
- A missing or unreadable downloads folder: the mover doesn't sign in to
  qBittorrent at all; waiting grabs are listed as `files not found` after an
  hour. Settings > Library paths shows the downloads folder (Missing) and the
  grabs whose files are not found.

# TV scoping note (later pass)

Prod's TV handling (`auto_rename.generate_destination_path`, TV branch, and
`process_tv_season_pack`):

- Root by quality: 4K to `tv_4k_root`, 1080p remux to `tv_remux_root`, else `tv_root`.
- `Show/Season NN/Show - SxxEyy - Episode Title.ext`, with the episode title from TMDB.
- Season packs: every video file goes through the same path; files without
  SxxEyy keep their original name in `Season NN`; with no season at all, they
  go loose in the show folder.

Proposed for luma-test:

1. Rules gain a media type (Movies / TV). TV rules reuse resolution and source
   (no release window: first-air dates don't map cleanly). A separate TV fallback.
2. Naming: `Show (Year)/Season NN/Show (Year) - SxxEyy[-Eyy] - Title.ext`;
   multi-episode files (S01E01E02, S01E01-02) handled; specials to `Season 00`.
   Unknown episode numbers are skipped and recorded (`unrecognized`), never dropped loose.
3. Episode titles: one TMDB season call per pack (not one per episode), cached;
   title optional, sanitized with `safe_component`.
4. Season packs: per-file `exists` handling so a re-grab fills only gaps; the
   space precheck sums the whole pack.
5. Same isolation (tag + tracked hash), path safety, 664/775 and root chown.
6. Tests: pack with mixed SxxEyy and extras, multi-episode, specials, Unicode
   and colon titles, partial overlap with existing episodes, insufficient space.

Estimate: about 1.5 days including UI (Movies/TV switch on rules, TV fallback).
