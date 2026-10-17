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

## Not yet on this branch

- `POST /api/storage/downloads/cleanup` (storage sheet "free up space") gets the
  same guard from cursor/owner-session-guard (09d32bd) when that branch is merged.
