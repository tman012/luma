#!/usr/bin/env bash
# luma-host-remote-mover (reference; Ops owns production): runs on the Docker host, SSHs to the download
# host and runs the remote mover there.
# Usage: luma-host-remote-mover.sh cp|mv <src> <dest> [tmp]   (paths on the download host)
# OpenSSH joins remote argv into ONE shell string without quoting — use printf %q.
# v324: optional 4th arg TMP passed through (quoted the same way).
# Optional env LUMA_TRANSFER_HOST (library server transfer address) is forwarded to the remote mover
# as a quoted VAR=value prefix; unset → the remote command is exactly as before.
set -euo pipefail
MODE="${1:-}"; SRC="${2:-}"; DEST="${3:-}"; TMP="${4:-}"
KEY="${LUMA_REMOTE_SSH_KEY:-$HOME/.ssh/id_ed25519}"
HOST="${LUMA_REMOTE_SSH_HOST:?set LUMA_REMOTE_SSH_HOST}"
USER="${LUMA_REMOTE_SSH_USER:-$(id -un)}"
REMOTE_MOVER="${LUMA_REMOTE_MOVER_PATH:-bin/luma-remote-mover.sh}"
if [ "$MODE" != "cp" ] && [ "$MODE" != "mv" ]; then
  echo "usage: $0 cp|mv <src> <dest> [tmp]" >&2
  exit 2
fi
if [ -z "$SRC" ] || [ -z "$DEST" ]; then
  echo "usage: $0 cp|mv <src> <dest> [tmp]" >&2
  exit 2
fi
# Single remote command string: each token shell-quoted so spaces and () survive.
if [ -n "$TMP" ]; then
  REMOTE_CMD=$(printf '%q ' "$REMOTE_MOVER" "$MODE" "$SRC" "$DEST" "$TMP")
else
  REMOTE_CMD=$(printf '%q ' "$REMOTE_MOVER" "$MODE" "$SRC" "$DEST")
fi
if [ -n "${LUMA_TRANSFER_HOST:-}" ]; then
  REMOTE_CMD="LUMA_TRANSFER_HOST=$(printf '%q' "$LUMA_TRANSFER_HOST") $REMOTE_CMD"
fi
exec ssh -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=20 -i "$KEY" \
  "${USER}@${HOST}" "$REMOTE_CMD"
