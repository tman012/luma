"""Remote push-import: Luma decides src+dest; host helper via Unix socket.

Container never runs ssh and never mounts keys. It HTTP-POSTs over a
bind-mounted Unix socket to the Ops host helper, which runs:

  /home/tbosley02/bin/luma-host-remote-mover.sh cp|mv SRC DEST

Locked call (HTTP over AF_UNIX):
  POST /move
  {"mode":"cp|mv","src":"/scratch/downloads/...","dest":"/media/...",
   "tmp":"/media/.../.luma-<hex16>.luma-partial"}     (v324: tmp, same dir as dest)

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
import socket
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

# v324 remote import safety — temp naming is owned by LUMA and is the exact
# local-copy rule: library_mover.partial_name() → '.luma-<hex16>.luma-partial'
# in the SAME folder as the final name (no title in it). Luma sends it as
# "tmp" (absolute, remote-mapped); the .72 script copies SRC→tmp, checks size,
# renames tmp→dest without replacing, and removes tmp on any failure. The
# script must NOT add a suffix of its own. Luma never suffixes "dest".
# Explicit script failures (usage / bounds / src missing / copy-verify-rename
# failed): real failures, reported immediately.
_SCRIPT_FAIL_RCS = frozenset({2, 3, 4, 5, 6})
# Wait-out policy when the helper gives up before the copy does (.62 helper
# timeout=600 → HTTP 500 rc=124 while cp keeps running on .72).
POLL_INTERVAL_S = 15.0
STALL_S = 600.0            # partial must grow at least once per 10 min
MIN_WAIT_S = 1800.0        # floor for the size-based overall deadline
MIN_RATE_BPS = 5 * 1024 * 1024   # deadline = max(MIN_WAIT_S, 2 * size / 5 MiB/s)
_sleep = time.sleep
_now = time.monotonic

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


# v324b: an empty reply (no status line: RemoteDisconnected / BadStatusLine), a reset, a broken pipe or an
# aborted connection means the helper stopped waiting (luma-mover restart, KillMode=process) while cp on .72
# keeps going. Same as a timeout: poll tmp/final, fail only if the copy really stopped.
_HELPER_STOPPED_WAITING = (socket.timeout, TimeoutError, http.client.RemoteDisconnected, http.client.BadStatusLine,
                           ConnectionResetError, BrokenPipeError, ConnectionAbortedError)


def _http_post_unix(sock_path: str, path: str, body: bytes, timeout: int) -> Tuple[int, str]:
    conn = _UnixHTTPConnection(sock_path, timeout=float(timeout))
    try:
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
    except (http.client.RemoteDisconnected, http.client.BadStatusLine) as e:
        raise RemoteMoverError(f'mover helper sent an empty reply ({sock_path}): {e!r}') from e
    except (ConnectionRefusedError, ConnectionError, OSError) as e:
        raise RemoteMoverError(
            f'mover socket unreachable ({sock_path}): {e} '
            f'(Ops: luma-host-remote-mover listener on .62)') from e
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _size_of(path: str) -> Optional[int]:
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            total = 0
            for dp, _dn, fns in os.walk(path):
                for fn in fns:
                    try:
                        total += os.lstat(os.path.join(dp, fn)).st_size
                    except OSError:
                        pass
            return total
        return os.lstat(path).st_size
    except OSError:
        return None


def _dest_observable(dest_container: str) -> bool:
    """True when Luma can see the dest folder through its own /media mount."""
    return os.path.isdir(os.path.dirname(dest_container) or '/')


def _deadline_for(src_size: Optional[int]) -> float:
    size = max(0, int(src_size or 0))
    return max(MIN_WAIT_S, 2.0 * size / MIN_RATE_BPS)


def remote_temp_for(dest_container: str) -> str:
    """Container path of the temp file for *dest_container*: same folder,
    library_mover.partial_name() (the local copy's rule)."""
    from app import library_mover
    return os.path.join(os.path.dirname(dest_container), library_mover.partial_name())


def _is_partial_name(path: str) -> bool:
    from app import library_mover
    b = os.path.basename(path)
    return b.endswith((library_mover.PARTIAL_SUFFIX, '.luma.partial', '.lumapart'))


def wait_for_remote_copy(src_container: str, dest_container: str, tmp_container: str) -> str:
    """The helper stopped waiting but .72 may still be copying. Watch the final
    name and the script's partial through the /media mount. 'copied' once the
    final name has the source size and no partial is left; RemoteMoverError when
    the partial vanished without a final, stalled, or the deadline passed."""
    src_size = _size_of(src_container)
    part = tmp_container
    start = _now()
    deadline = start + _deadline_for(src_size)
    last_part = None
    last_growth = start
    while True:
        final = _size_of(dest_container)
        psize = _size_of(part)
        if final is not None and psize is None:
            if src_size is None or final == src_size:
                logger.info('Remote mover: copy finished after helper wait: %s', dest_container)
                return 'copied'
            if final > src_size:
                raise RemoteMoverError(
                    f'remote copy size mismatch at {dest_container}: {final} != {src_size}')
            # smaller final with no partial: a pre-partial script still copying in place.
        if psize is None and final is None and last_part is not None:
            raise RemoteMoverError(
                f'remote copy failed: partial removed without final ({dest_container})')
        progress = psize if psize is not None else final
        t = _now()
        if progress is not None and progress != last_part:
            last_part = progress
            last_growth = t
        elif progress is None and last_part is None and t - start >= STALL_S:
            raise RemoteMoverError(
                f'remote copy never started (no partial/final after {int(STALL_S)}s): {dest_container}')
        if progress is not None and t - last_growth >= STALL_S:
            raise RemoteMoverError(f'remote copy stalled at {progress} B: {dest_container}')
        if t >= deadline:
            raise RemoteMoverError(f'remote copy did not finish by deadline: {dest_container}')
        _sleep(POLL_INTERVAL_S)


def _verify_final(src_container: str, dest_container: str, tmp_container: str) -> None:
    if not _dest_observable(dest_container):
        return   # mount not visible here (e.g. unit tests / odd layouts): trust script verify
    src_size = _size_of(src_container)
    final = _size_of(dest_container)
    if final is None:
        # helper said OK but the final name is not visible yet (CIFS cache): wait it out.
        wait_for_remote_copy(src_container, dest_container, tmp_container)
        return
    if src_size is not None and final != src_size:
        raise RemoteMoverError(
            f'remote copy size mismatch at {dest_container}: {final} != {src_size}')


def transfer(src_container: str, dest_container: str, mode: str = 'cp',
             tmp_container: Optional[str] = None) -> str:
    """POST /move over the host Unix socket. Prefer mode=cp (keeps seeding).

    *dest_container* is the FINAL name. The temp is remote_temp_for(dest)
    (library_mover.partial_name(), same folder) and goes out as "tmp"; the
    .72 script copies to it, verifies, renames, cleans up. A helper timeout
    (rc 124 / HTTP 5xx without an explicit script rc / socket timeout) is not a
    failure: Luma waits for the copy via its /media mount.

    Returns 'copied' / 'moved' / 'exists'.
    """
    if _is_partial_name(dest_container):
        raise RemoteMoverError(f'dest must be the final name, not a partial: {dest_container}')
    cfg = config()
    op = 'mv' if mode in ('mv', 'move') else 'cp'
    rsrc = to_remote(src_container, cfg)
    rdest = to_remote(dest_container, cfg)
    _assert_remote_bounds(rsrc, rdest, cfg)
    tmp_container = tmp_container or remote_temp_for(dest_container)
    rtmp = to_remote(tmp_container, cfg)
    if os.path.dirname(rtmp) != os.path.dirname(rdest):
        raise RemoteMoverError(f'temp must sit next to dest: {rtmp} vs {rdest}')
    observable = _dest_observable(dest_container)
    # Size-based socket timeout so Luma itself never gives up first.
    timeout = max(int(cfg['timeout']), int(_deadline_for(_size_of(src_container))))
    # Locked Ops JSON: field name is "mode" (not "op").
    payload = json.dumps({'mode': op, 'src': rsrc, 'dest': rdest, 'tmp': rtmp}).encode()
    try:
        status, raw = _http_post_unix(str(cfg['sock']), '/move', payload, timeout)
    except RemoteMoverError as e:
        if observable and op == 'cp' and isinstance(e.__cause__, _HELPER_STOPPED_WAITING):
            logger.warning('Remote mover: helper stopped waiting (%s), waiting for copy: %s',
                           type(e.__cause__).__name__, rdest)
            return wait_for_remote_copy(src_container, dest_container, tmp_container)
        raise
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        if observable and op == 'cp' and status >= 500:
            logger.warning('Remote mover: HTTP %s non-JSON, waiting for copy: %s', status, rdest)
            return wait_for_remote_copy(src_container, dest_container, tmp_container)
        raise RemoteMoverError(
            f'host mover bad JSON HTTP {status}: {raw[:200]!r} '
            f'({op} {rsrc} -> {rdest})') from e

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
    if ok is False or status >= 400 or rc_i != 0:
        err = output or f'HTTP {status} rc={rc_i}'
        if 'exist' in err.lower():
            return 'exists'
        helper_gave_up = rc_i == 124 or 'timed out' in err.lower() or 'timeout' in err.lower() \
            or (status >= 500 and rc_i not in _SCRIPT_FAIL_RCS)
        if observable and op == 'cp' and helper_gave_up:
            logger.warning('Remote mover: helper gave up (HTTP %s rc=%s), waiting for copy: %s',
                           status, rc_i, rdest)
            return wait_for_remote_copy(src_container, dest_container, tmp_container)
        raise RemoteMoverError(
            f'host mover failed HTTP {status} rc={rc_i}: {err} '
            f'({op} {rsrc} -> {rdest})')
    if ok is True or (status == 200 and rc_i == 0):
        if op == 'cp':
            _verify_final(src_container, dest_container, tmp_container)
        how = 'moved' if op == 'mv' else 'copied'
        logger.info('Remote mover via unix socket: %s %s -> %s', op, rsrc, rdest)
        return how
    raise RemoteMoverError(
        f'host mover unexpected response HTTP {status}: {raw[:200]!r} '
        f'({op} {rsrc} -> {rdest})')


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
