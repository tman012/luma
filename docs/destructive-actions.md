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

From outside the LAN, a request with no session is stopped by the app-wide
sign-in check first. It is still a 401, but its body is that check's own.
