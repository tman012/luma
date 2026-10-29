"""Remote push-import: Luma decides src+dest; host helper via Unix socket.

Container never runs ssh and never mounts keys. It HTTP-POSTs over a
bind-mounted Unix socket to the Ops host helper, which runs:

  /home/tbosley02/bin/luma-host-remote-mover.sh cp|mv SRC DEST

Locked call (HTTP over AF_UNIX):
  POST /move
  {"mode":"cp|mv","src":"/scratch/downloads/...","dest":"/media/...",
   "tmp":"/media/.../.luma-<hex16>.luma-partial"}   (v324: full path, same folder as dest)

Socket (in luma-test): host /home/tbosley02/luma-mover/mover.sock mounted
under /run/luma-mover. Defaults try:
  1) LUMA_REMOTE_MOVER_SOCK (if set)
  2) /run/luma-mover/mover.sock   (sock file inside mounted dir)
  3) /run/luma-mover             (sock file mounted directly at that path)

Path map (container → .72):
  LUMA_DOWNLOADS_ROOT / /storage/downloads / /downloads
      → LUMA_REMOTE_DOWNLOADS_ROOT (default /scratch/downloads)
  /media… → LUMA_REMOTE_MEDIA_ROOT (default /media)
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import posixpath
import socket
import stat
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ENV_ENABLE = 'LUMA_REMOTE_MOVER'
ENV_SOCK = 'LUMA_REMOTE_MOVER_SOCK'
ENV_DOWNLOADS = 'LUMA_REMOTE_DOWNLOADS_ROOT'
ENV_MEDIA = 'LUMA_REMOTE_MEDIA_ROOT'
ENV_TIMEOUT = 'LUMA_REMOTE_MOVER_TIMEOUT_S'

# Ops FINAL: in-container socket (host ~/luma-mover bind-mounted → /run/luma-mover).
DEFAULT_SOCK = '/run/luma-mover/mover.sock'
DEFAULT_SOCK_CANDIDATES = (DEFAULT_SOCK,)
DEFAULT_DOWNLOADS = '/scratch/downloads'
DEFAULT_MEDIA = '/media'
DEFAULT_TIMEOUT_S = 3600

_DOWNLOADS_ALIASES = ('/storage/downloads', '/downloads')


class RemoteMoverError(RuntimeError):
    """Remote copy cannot run (misconfigured) or the host helper failed."""


class _UnixHTTPConnection(http.client.HTTPConnection):
    """http.client over AF_UNIX (stdlib-only; no requests)."""

    def __init__(self, sock_path: str, timeout: float = 60):
        super().__init__('localhost', timeout=timeout)
        self._unix = sock_path

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if self.timeout is not None:
            s.settimeout(self.timeout)
        s.connect(self._unix)
        self.sock = s


def _truthy(raw: str) -> bool:
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def enabled() -> bool:
    """Remote mode: explicit LUMA_REMOTE_MOVER, else ON when LUMA_INSTANCE=test."""
    raw = os.environ.get(ENV_ENABLE)
    if raw is not None and str(raw).strip() != '':
        return _truthy(str(raw))
    return (os.environ.get('LUMA_INSTANCE') or '').strip().lower() == 'test'


def sock_candidates() -> List[str]:
    """Ordered socket paths to try (env first, then defaults)."""
    env = (os.environ.get(ENV_SOCK) or '').strip()
    out: List[str] = []
    if env:
        out.append(env)
    for p in DEFAULT_SOCK_CANDIDATES:
        if p not in out:
            out.append(p)
    return out


def resolve_sock() -> str:
    """LUMA_REMOTE_MOVER_SOCK if set, else /run/luma-mover/mover.sock."""
    env = (os.environ.get(ENV_SOCK) or '').strip()
    if env:
        return env
    return DEFAULT_SOCK


def config() -> Dict[str, object]:
    downloads = (os.environ.get(ENV_DOWNLOADS) or DEFAULT_DOWNLOADS).strip() or DEFAULT_DOWNLOADS
    media = (os.environ.get(ENV_MEDIA) or DEFAULT_MEDIA).strip() or DEFAULT_MEDIA
    try:
        timeout = int(os.environ.get(ENV_TIMEOUT) or DEFAULT_TIMEOUT_S)
    except ValueError as e:
        raise RemoteMoverError(f'{ENV_TIMEOUT} must be an int') from e
    if not os.path.isabs(downloads) or not os.path.isabs(media):
        raise RemoteMoverError('remote downloads/media roots must be absolute')
    sock = resolve_sock()
    return {
        'sock': sock,
        'downloads_root': downloads.rstrip('/') or downloads,
        'media_root': media.rstrip('/') or '/media',
        'timeout': max(30, timeout),
    }


def _container_downloads_root() -> str:
    from app import file_management as fm
    return fm.downloads_root().rstrip('/') or '/downloads'


def _under(path: str, root: str) -> bool:
    root = root.rstrip('/')
    return path == root or path.startswith(root + os.sep)


def to_remote(container_path: str, cfg: Optional[Dict[str, object]] = None) -> str:
    """Map a container absolute path to the path on the remote host (.72)."""
    cfg = cfg or config()
    raw = os.path.abspath(container_path)
    try:
        path = os.path.realpath(raw)
    except OSError:
        path = raw

    rem_dl = str(cfg['downloads_root'])
    rem_media = str(cfg['media_root'])

    locals_dl = []
    dl_c = _container_downloads_root()
    for cand in (dl_c, *_DOWNLOADS_ALIASES):
        try:
            locals_dl.append(os.path.realpath(cand))
        except OSError:
            locals_dl.append(cand.rstrip('/'))
        locals_dl.append(cand.rstrip('/'))
    seen = set()
    dl_roots = []
    for r in locals_dl:
        r = r.rstrip('/')
        if r and r not in seen:
            seen.add(r)
            dl_roots.append(r)

    for root in dl_roots:
        if _under(path, root) or _under(raw, root):
            base = path if _under(path, root) else raw
            rel = base[len(root):].lstrip('/')
            return rem_dl if not rel else f'{rem_dl}/{rel}'

    media_c = '/media'
    if _under(path, media_c) or _under(raw, media_c):
        base = path if _under(path, media_c) else raw
        rel = base[len(media_c):].lstrip('/')
        return rem_media if not rel else f'{rem_media}/{rel}'

    raise RemoteMoverError(
        f'path not under downloads root or /media (cannot map for remote mover): {container_path}')


def _assert_remote_bounds(rsrc: str, rdest: str, cfg: Dict[str, object]) -> None:
    if not _under(rsrc, str(cfg['downloads_root'])):
        raise RemoteMoverError(
            f'remote SRC must be under {cfg["downloads_root"]}: {rsrc}')
    if not _under(rdest, str(cfg['media_root'])):
        raise RemoteMoverError(
            f'remote DEST must be under {cfg["media_root"]}: {rdest}')


def mkdir_p(container_dir: str, cfg: Optional[Dict[str, object]] = None) -> None:
    """No-op: host/remote script creates parents. Validates dest under /media."""
    cfg = cfg or config()
    remote = to_remote(container_dir, cfg)
    if not _under(remote, str(cfg['media_root'])):
        raise RemoteMoverError(
            f'remote DEST dir must be under {cfg["media_root"]}: {remote}')


class _HelperStoppedWaiting(Exception):
    """The helper stopped answering for this request (timeout). The copy on .72 may still be running."""


# v324: socket errors that mean "the helper stopped waiting", not "the copy never started".
# v324b: an empty reply (helper restarted under KillMode=process while cp keeps running on .72) or a
# reset/broken connection after the request went out is the same as a timeout: keep watching the copy.
_KEEP_WAITING_ERRORS: Tuple[type, ...] = (
    socket.timeout, TimeoutError, http.client.RemoteDisconnected, http.client.BadStatusLine,
    http.client.IncompleteRead, ConnectionResetError, BrokenPipeError, ConnectionAbortedError)

# v324: watching the copy through /media after the helper stopped waiting.
POLL_S = 15
NO_GROWTH_S = 600
MIN_DEADLINE_S = 1800
MIN_RATE_BPS = 5 * 1024 * 1024
STABLE_POLLS = 2            # no readable source size: final size must hold this many polls with the temp gone
RC_TIMEOUT = 124            # deployed helper: copy still running when the helper gave up waiting
SCRIPT_FAIL_RCS = frozenset({2, 3, 4, 5, 6})   # deployed script refused/failed: report at once
_sleep = time.sleep
_clock = time.monotonic


def _http_post_unix(sock_path: str, path: str, body: bytes, timeout: int) -> Tuple[int, str]:
    conn = _UnixHTTPConnection(sock_path, timeout=float(timeout))
    sent = False
    try:
        conn.connect()
        sent = True          # connected: from here a drop may leave a copy running on .72
        conn.request(
            'POST', path, body=body,
            headers={'Content-Type': 'application/json', 'Content-Length': str(len(body)),
                     'Accept': 'application/json', 'Host': 'localhost'},
        )
        resp = conn.getresponse()
        raw = resp.read().decode() or ''
        return resp.status, raw
    except FileNotFoundError as e:
        raise RemoteMoverError(
            f'mover socket missing: {sock_path} '
            f'(mount host ~/luma-mover/mover.sock → /run/luma-mover/; '
            f'or set {ENV_SOCK})') from e
    except _KEEP_WAITING_ERRORS as e:
        if sent:
            raise _HelperStoppedWaiting(f'{type(e).__name__}: {e}') from e
        raise RemoteMoverError(f'mover socket unreachable ({sock_path}): {e}') from e
    except (ConnectionRefusedError, ConnectionError, OSError) as e:
        raise RemoteMoverError(
            f'mover socket unreachable ({sock_path}): {e} '
            f'(Ops: luma-host-remote-mover listener on .62)') from e
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _size(path: str) -> Optional[int]:
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return None
    return st.st_size if stat.S_ISREG(st.st_mode) else None


def deadline_s(size: Optional[int]) -> float:
    """max(1800 s, twice the time to copy *size* bytes at 5 MiB/s)."""
    if not size or size <= 0:
        return float(MIN_DEADLINE_S)
    return float(max(MIN_DEADLINE_S, 2 * size / MIN_RATE_BPS))


def wait_for_copy(tmp_c: str, final_c: str, src_size: Optional[int], label: str = '') -> str:
    """Watch the temp and final file (container paths under /media) after the helper stopped waiting.

    Success: final file present with the source size and the temp gone. With no readable source size,
    success needs the temp gone and the final size unchanged over STABLE_POLLS polls (fail-closed
    otherwise). Failure: the temp vanished with no final file, nothing grew for NO_GROWTH_S, or the
    deadline passed."""
    start = _clock()
    limit = deadline_s(src_size)
    last_change = start
    last_seen: Tuple[Optional[int], Optional[int]] = (None, None)
    seen_tmp = False
    stable = 0
    last_final: Optional[int] = None
    while True:
        tsz, fsz = _size(tmp_c), _size(final_c)
        now = _clock()
        if tsz is not None:
            seen_tmp = True
        if tsz is None and fsz is not None:
            if src_size is not None:
                if fsz == src_size:
                    logger.info('Remote mover: copy finished after helper stopped waiting (%s)', label)
                    return 'copied'
                raise RemoteMoverError(f'copy landed with the wrong size ({fsz} != {src_size}): {label}')
            stable = stable + 1 if (fsz == last_final and fsz > 0) else (1 if fsz > 0 else 0)
            last_final = fsz
            if stable >= STABLE_POLLS:
                logger.info('Remote mover: copy finished (size stable, source size unknown) (%s)', label)
                return 'copied'
        elif tsz is None and fsz is None and seen_tmp:
            raise RemoteMoverError(f'copy stopped: temp file gone and no final file ({label})')
        if (tsz, fsz) != last_seen:
            last_seen = (tsz, fsz)
            last_change = now
        if now - last_change >= NO_GROWTH_S:
            raise RemoteMoverError(f'copy stopped: no progress for {NO_GROWTH_S}s ({label})')
        if now - start >= limit:
            raise RemoteMoverError(f'copy did not finish within {int(limit)}s ({label})')
        _sleep(POLL_S)


def transfer(src_container: str, dest_container: str, mode: str = 'cp') -> str:
    """POST /move over the host Unix socket. Prefer mode=cp (keeps seeding).

    v324: the helper copies to *tmp* (a full path in the destination folder, named by
    library_mover.partial_name()) and renames into place without replacing. When the helper stops
    waiting (socket timeout, HTTP 5xx, rc 124) the copy is watched through /media instead of failing.

    Returns 'copied' / 'moved' / 'exists'.
    """
    from app import library_mover
    cfg = config()
    op = 'mv' if mode in ('mv', 'move') else 'cp'
    rsrc = to_remote(src_container, cfg)
    rdest = to_remote(dest_container, cfg)
    _assert_remote_bounds(rsrc, rdest, cfg)
    tname = library_mover.partial_name()
    rtmp = posixpath.join(posixpath.dirname(rdest), tname)
    tmp_c = os.path.join(os.path.dirname(dest_container), tname)
    src_size = _size(src_container)
    label = f'{op} {rsrc} -> {rdest}'
    # Locked Ops JSON: field name is "mode" (not "op"); v324 adds "tmp" (full path, same folder as dest).
    payload = json.dumps({'mode': op, 'src': rsrc, 'dest': rdest, 'tmp': rtmp}).encode()
    try:
        status, raw = _http_post_unix(str(cfg['sock']), '/move', payload, int(cfg['timeout']))
    except _HelperStoppedWaiting as e:
        logger.warning('Remote mover: helper stopped waiting (%s); watching the copy (%s)', e, label)
        return _watched(wait_for_copy(tmp_c, dest_container, src_size, label), op)
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        if status >= 500:
            logger.warning('Remote mover: HTTP %s with no JSON; watching the copy (%s)', status, label)
            return _watched(wait_for_copy(tmp_c, dest_container, src_size, label), op)
        raise RemoteMoverError(
            f'host mover bad JSON HTTP {status}: {raw[:200]!r} ({label})') from e

    # PM ALIGN response: {"ok": bool, "rc": N, "output": "..."}
    ok = data.get('ok')
    rc = data.get('rc', 0)
    try:
        rc_i = int(rc) if rc is not None else 0
    except (TypeError, ValueError):
        rc_i = -1
    output = str(data.get('output') or data.get('error') or '')

    if status == 409 or rc_i == 17 or 'exist' in output.lower():
        return 'exists'
    if rc_i in SCRIPT_FAIL_RCS:
        raise RemoteMoverError(f'host mover failed HTTP {status} rc={rc_i}: {output or "script error"} ({label})')
    # A 5xx carrying a helper verdict (rc) is final; a bare 5xx (no rc) means the helper stopped waiting.
    if rc_i == RC_TIMEOUT or (status >= 500 and 'rc' not in data and ok is not True):
        logger.warning('Remote mover: helper stopped waiting (HTTP %s rc=%s); watching the copy (%s)',
                       status, rc_i, label)
        return _watched(wait_for_copy(tmp_c, dest_container, src_size, label), op)
    if ok is False or status >= 400 or rc_i != 0:
        err = output or f'HTTP {status} rc={rc_i}'
        raise RemoteMoverError(f'host mover failed HTTP {status} rc={rc_i}: {err} ({label})')
    if ok is True or (status == 200 and rc_i == 0):
        how = 'moved' if op == 'mv' else 'copied'
        logger.info('Remote mover via unix socket: %s %s -> %s', op, rsrc, rdest)
        return how
    raise RemoteMoverError(f'host mover unexpected response HTTP {status}: {raw[:200]!r} ({label})')


def _watched(result: str, op: str) -> str:
    return 'moved' if (result == 'copied' and op == 'mv') else result


def health(cfg: Optional[Dict[str, object]] = None) -> bool:
    """Optional GET /health over the mover socket. True when helper answers ok."""
    cfg = cfg or config()
    conn = _UnixHTTPConnection(str(cfg['sock']), timeout=min(15, float(cfg['timeout'])))
    try:
        conn.request('GET', '/health', headers={'Host': 'localhost', 'Accept': 'application/json'})
        resp = conn.getresponse()
        raw = resp.read().decode() or ''
        if resp.status != 200:
            return False
        try:
            data = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return True  # 200 with empty/non-JSON still counts as up
        if 'ok' in data:
            return bool(data.get('ok'))
        return True
    except OSError:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass
