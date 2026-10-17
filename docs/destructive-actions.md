# Destructive actions

Actions that delete files need the **owner signed in with a session**
(`start_flow.require_signed_in_owner`, or the `start_flow.signed_in_owner_required`
route decorator). LAN access alone is not enough.

| Who | Answer |
| --- | --- |
| Owner, signed in with a session (LAN or remote) | Allowed |
| Implicit LAN owner (LAN sign-in off), service key (kiosk, companion apps), no session | 401 `{"error": "Sign in first", "sign_in": true}` |
| Other admins, plain, guest and Plex-approved users | 403 `{"error": "Owner only"}` |

The 401 carries `"sign_in": true` so the UI can offer a sign-in for that action;
the sign-in page then shows its form even to an implicit LAN owner sent there
with `state.signIn`.

## Listed actions

- `POST /api/downloads-guard/run` with `{"dry_run": false}` (or any false value):
  Downloads Guard card, "Clean up now (deletes for real)". Permanently deletes
  the oldest downloads. A dry run (`dry_run` true or absent) deletes nothing and
  keeps the access it had before.
- `POST /api/storage/downloads/cleanup`: the storage sheet's "Free up …" delete.
  Permanently deletes the oldest downloads until the target is freed. The route
  carries `@_signed_in_owner_only` (as in cursor/owner-session-guard 09d32bd).
  The storage sheet shows "Sign in to free up space" instead of the delete for
  an implicit LAN owner, and after a 401.

- `/api/downloads/remove/<hash>` with `deleteFiles` (query) or `delete_files`
  (body) true: removes the torrent and its files on disk. The check runs before
  qBittorrent is contacted. Without files, removing keeps today's access.
  Activity shows "Sign in to delete files" instead of "Delete torrent + files"
  to an implicit LAN owner, and after a 401 (from cursor/tv-phase1 56b522d).
- `PUT /api/settings` when the change turns on or widens automatic deletion
  (Downloads Guard). `app/auto_delete_guard._widens_auto_delete(old, new)`
  compares the saved settings with the result of the same merge the save
  does. It reads values with Downloads Guard's own parsers and limits. A
  change widens when it:
  - turns `automation.downloads_guard.enabled` on;
  - raises `min_free_gb` (the guard fires at more free space);
  - raises `reclaim_gb` (each run deletes more);
  - raises `daily_cap_gb`, or sets it to 0 (no cap);
  - lowers `check_interval_minutes` (runs more often);
  - changes `media.downloads_root` in any way, clearing it included,
    **guard on or off**. It is the folder the guard and the manual cleanup
    delete from (`auto_delete_guard.DELETE_ROOTS` lists every such setting).
    The stored value is compared as saved, with no resolving and no fallback.
    Even the same folder written differently counts, and so does a change in
    the same save that turns the guard off. A save that leaves it unchanged
    is fine.
  Turning the guard off (even with other number edits in the same save) or
  tightening any of these stays open to any admin, as does the tracker
  preference, which only sets what is spent first. A refused save changes
  nothing. The check is atomic: the route settles whether the caller is the
  signed-in owner first, then `SettingsManager.update(veto=...)` decides
  whether the change widens under the same lock as the write, against the
  stored settings and the merged result. A save that lands in between can't
  make a stale check pass. Non-admins get 403 as before. In Settings, Save becomes "Sign in
  to save" after a 401 and opens /login. A new edit to that card brings Save
  back.

Both cleanup routes take an optional JSON object. Any other non-empty body (a list,
string, number, `null`, or text that isn't JSON) gets 400
`{"error": "Invalid request"}` and deletes nothing. The run route checks the
body first; the cleanup route checks the session first, then the body.

From outside the LAN, a request with no session is stopped by the app-wide
sign-in check first. It is still a 401, but its body is that check's own.
