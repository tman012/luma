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
- Prod's AutoRenamer skips torrents carrying the tag (or a category equal to
  it), in both its tracked-download path and its Radarr category scan. Nothing
  else in prod changed. Edge case: if prod grabs a release luma-test already
  grabbed (same hash, now tagged), prod drops it rather than copying it.
- A torrent removed from qBit is dropped after 3 consecutive misses. qBit
  being unreachable is not a miss.
- Prod's Downloads Guard (oldest-first cleanup of the downloads folder) is not
  tag-aware and can delete luma-test's finished downloads. Unchanged on purpose
  (out of scope); flag for a later pass.

## Mounts (Ops)

`docker-compose.test.yml` reads, with nothing hard-coded:

| Variable | Container path | Notes |
|---|---|---|
| `LUMA_TEST_DOWNLOADS_DIR` | `/downloads` (read-only) | Same path qBit saves to; `/downloads/incomplete` is skipped |
| `LUMA_TEST_MEDIA_DIR` | `/media` | Parent of the library folders; any subfolder can be added, spaces included |
| `LUMA_TEST_QBIT_TAG` | env | Default `luma-test`; set the same value on prod |

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
- Copies go to a hidden `.name.luma-partial` file (O_EXCL, no symlink follow),
  then are linked into place. Symlinks in downloads are never followed.

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
