"""Remote push-import: Luma decides src+dest; host helper via Unix socket.

Container never runs ssh and never mounts keys. It HTTP-POSTs over a
bind-mounted Unix socket to the Ops host helper, which runs:

  /home/tbosley02/bin/luma-host-remote-mover.sh cp|mv SRC DEST

Locked call (HTTP over AF_UNIX):
  POST /move
  {"mode":"cp|mv","src":"/scratch/downloads/...","dest":"/media/..."}

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
    except (ConnectionRefusedError, ConnectionError, OSError) as e:
        raise RemoteMoverError(
            f'mover socket unreachable ({sock_path}): {e} '
            f'(Ops: luma-host-remote-mover listener on .62)') from e
    finally:
        try:
            conn.close()
        except Exception:
            pass


def transfer(src_container: str, dest_container: str, mode: str = 'cp') -> str:
    """POST /move over the host Unix socket. Prefer mode=cp (keeps seeding).

    Returns 'copied' / 'moved' / 'exists'.
    """
    cfg = config()
    op = 'mv' if mode in ('mv', 'move') else 'cp'
    rsrc = to_remote(src_container, cfg)
    rdest = to_remote(dest_container, cfg)
    _assert_remote_bounds(rsrc, rdest, cfg)
    # Locked Ops JSON: field name is "mode" (not "op").
    payload = json.dumps({'mode': op, 'src': rsrc, 'dest': rdest}).encode()
    status, raw = _http_post_unix(str(cfg['sock']), '/move', payload, int(cfg['timeout']))
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
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
        raise RemoteMoverError(
            f'host mover failed HTTP {status} rc={rc_i}: {err} '
            f'({op} {rsrc} -> {rdest})')
    if ok is True or (status == 200 and rc_i == 0):
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
