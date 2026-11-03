"""Watchlist image store (trial): local copies of each Watchlist item's sheet backdrop (full resolution) and clearlogo
on the server's data volume, so a Watchlist sheet opens from Luma itself instead of TMDb.

Location: $LUMA_WL_IMAGE_DIR, else $LUMA_DATA_DIR/wl_images (app.paths). Bounded: LUMA_WL_IMAGE_CAP_MB (default
512 MB): past it the least recently served files go first; a title removed from the watchlist is pruned at once.

Only watchlist items are stored. Downloads run on one background worker (never inside a request). A failed fetch
is retried after FAIL_RETRY_S (never remembered for good). Files: <root>/<movie|tv>/<id>/<backdrop|logo>.<ext>.
Nothing here is cached on any device: the endpoint is under /api/ (the service worker never caches /api/).
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Set, Tuple

from app.paths import data_dir

logger = logging.getLogger(__name__)

TYPES = ('movie', 'tv')
KINDS = ('backdrop', 'logo')
DEFAULT_CAP_MB = 512
FAIL_RETRY_S = 600
MAX_FILE_BYTES = 25 * 1024 * 1024
# v348 (Designer): backdrops are kept at most this wide (TMDb w1920, or resized here): a 3840 original is 1.4-1.9 MB.
BACKDROP_MAX_W = 1920
# v349: phone copies (Designer's phone sizes: the sheet's 1280 backdrop, the w300 logo), resized here from the stored
# image; served with ?w=<width>.
VARIANTS = {'backdrop': 1280, 'logo': 300}
_MAGIC = ((b'\xff\xd8\xff', 'jpg', 'image/jpeg'), (b'\x89PNG\r\n\x1a\n', 'png', 'image/png'),
          (b'RIFF', 'webp', 'image/webp'))
CTYPES = {'jpg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp'}

Key = Tuple[str, int]
# resolve(type, id) -> {'backdrop': url|None, 'logo': url|None, 'backdrop_textless': bool}  (None: the title has none)
Resolver = Callable[[str, int], Dict[str, Optional[str]]]
# download(url) -> bytes (raises on failure)
Downloader = Callable[[str], bytes]


def store_root() -> Path:
    explicit = os.environ.get('LUMA_WL_IMAGE_DIR')
    return Path(explicit) if explicit else data_dir() / 'wl_images'


def cap_bytes() -> int:
    try:
        mb = int(os.environ.get('LUMA_WL_IMAGE_CAP_MB') or DEFAULT_CAP_MB)
    except ValueError:
        mb = DEFAULT_CAP_MB
    return max(1, mb) * 1024 * 1024


def valid(type_: str, id_: str, kind: Optional[str] = None) -> Optional[Key]:
    """(type, id) for a request's raw values, or None: ASCII digits only, type movie|tv, kind backdrop|logo."""
    if type_ not in TYPES or (kind is not None and kind not in KINDS):
        return None
    s = str(id_ or '')
    if not (s.isascii() and s.isdigit()) or len(s) > 10 or int(s) <= 0:
        return None
    return type_, int(s)


def fit_backdrop(body: bytes, max_w: int = BACKDROP_MAX_W) -> bytes:
    """A backdrop wider than max_w, resized to it (JPEG); anything else as it is (or when Pillow can't read it)."""
    return _fit(body, max_w, keep_alpha=False)


def _fit(body: bytes, max_w: int, keep_alpha: bool) -> bytes:
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(body))
        if im.width <= max_w:
            return body
        h = max(1, round(im.height * max_w / im.width))
        out = BytesIO()
        if keep_alpha:   # a logo stays a PNG with its transparency
            im.convert('RGBA').resize((max_w, h), Image.LANCZOS).save(out, 'PNG', optimize=True)
        else:
            im.convert('RGB').resize((max_w, h), Image.LANCZOS).save(out, 'JPEG', quality=86, optimize=True)
        return out.getvalue()
    except Exception:
        return body


def _sniff(body: bytes) -> Optional[str]:
    for magic, ext, _ in _MAGIC:
        if body.startswith(magic):
            if ext == 'webp' and body[8:12] != b'WEBP':
                continue
            return ext
    return None


class WlImageStore:
    def __init__(self, resolve: Resolver, download: Downloader, root: Optional[Path] = None,
                 cap: Optional[int] = None, clock: Callable[[], float] = time.time, start_worker: bool = True):
        self.root = Path(root) if root is not None else store_root()
        self.cap = cap if cap is not None else cap_bytes()
        self._resolve, self._download, self._clock = resolve, download, clock
        self._lock = threading.Lock()
        self._wanted: Set[Key] = set()
        self._failed: Dict[Key, float] = {}
        self._queued: Set[Key] = set()
        self._q: 'queue.Queue[Optional[Key]]' = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._start = start_worker

    # -- paths -------------------------------------------------------------------------------------------------
    def _dir(self, key: Key) -> Path:
        return self.root / key[0] / str(key[1])

    def path(self, key: Key, kind: str, w: Optional[int] = None) -> Optional[Path]:
        """The stored file; `w`: its phone copy (VARIANTS width) instead."""
        d = self._dir(key)
        name = kind if w is None else f'{kind}@{w}'
        for ext in CTYPES:
            p = d / f'{name}.{ext}'
            if p.is_file():
                return p
        return None

    def _variants_missing(self, key: Key) -> bool:
        return any(self.path(key, k) and not self.path(key, k, w) for k, w in VARIANTS.items())

    def _make_variant(self, key: Key, kind: str) -> None:
        src = self.path(key, kind)
        if not src:
            return
        w = VARIANTS[kind]
        body = _fit(src.read_bytes(), w, keep_alpha=kind == 'logo')
        ext = _sniff(body) or src.suffix[1:]
        for old in CTYPES:
            o = self._dir(key) / f'{kind}@{w}.{old}'
            if o.exists() and old != ext:
                o.unlink()
        tmp = self._dir(key) / f'.{kind}@{w}.part'
        tmp.write_bytes(body)
        os.replace(tmp, self._dir(key) / f'{kind}@{w}.{ext}')

    def present(self, key: Key) -> Dict[str, bool]:
        return {k: self.path(key, k) is not None for k in KINDS}

    def textless(self, key: Key) -> bool:
        """The stored backdrop is TMDb's textless art (no language): it may sit behind a logo."""
        return (self._dir(key) / 'textless').is_file()

    # -- the watchlist -----------------------------------------------------------------------------------------
    def sync(self, type_: str, ids: Iterable[object]) -> None:
        """The `type_` watchlist as it is now: new titles are queued, titles gone are pruned."""
        keys = {k for k in (valid(type_, str(i)) for i in ids) if k}
        # Reviewer #2: an empty answer (a hiccup as easily as an emptied list) never wipes the store; the cap and
        # per-title removals still bound it.
        if not keys:
            return
        self.sync_type(keys, {type_})

    def sync_type(self, keys: Set[Key], types: Iterable[str]) -> None:
        types = set(types)
        with self._lock:
            gone = {k for k in self._wanted if k[0] in types and k not in keys}
            self._wanted = {k for k in self._wanted if k[0] not in types} | keys
        for k in gone:
            self.remove(k)
        for t in types:   # files on disk for titles no longer wanted (a restart, a removal while down)
            base = self.root / t
            if base.is_dir():
                for d in base.iterdir():
                    if d.is_dir() and d.name.isdigit() and (t, int(d.name)) not in keys:
                        self.remove((t, int(d.name)))
        for k in keys:
            self.want(k)

    def want(self, key: Key) -> None:
        """Queue a fetch for a title still missing a file (not while its last failure is recent)."""
        if all(self.present(key).values()) and not self._variants_missing(key):
            return
        with self._lock:
            self._wanted.add(key)
            failed = self._failed.get(key)
            if key in self._queued or (failed is not None and self._clock() - failed < FAIL_RETRY_S):
                return
            self._queued.add(key)
        self._q.put(key)
        self._ensure_worker()

    def remove(self, key: Key) -> None:
        with self._lock:
            self._wanted.discard(key)
            self._failed.pop(key, None)
        d = self._dir(key)
        if d.is_dir():
            names = {f'{k}{v}.{e}' for k in KINDS for v in ('', f'@{VARIANTS[k]}') for e in CTYPES} | {'textless'}
            for f in d.iterdir():
                if f.name not in names and not (f.name.startswith('.') and f.name.endswith('.part')):
                    continue
                try:
                    f.unlink()
                except OSError:
                    pass
            try:
                d.rmdir()
            except OSError:
                pass

    # -- the worker --------------------------------------------------------------------------------------------
    def _ensure_worker(self) -> None:
        if not self._start or (self._worker and self._worker.is_alive()):
            return
        self._worker = threading.Thread(target=self._run, name='wl-images', daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while True:
            key = self._q.get()
            if key is None:
                return
            try:
                self.fetch(key)
            except Exception as e:   # never kills the worker
                logger.warning('wl-images: %s %s failed: %s', key[0], key[1], e)
            finally:
                with self._lock:
                    self._queued.discard(key)

    def drain(self) -> None:
        """Run everything queued now, in this thread (tests, a one-shot reconcile)."""
        while True:
            try:
                key = self._q.get_nowait()
            except queue.Empty:
                return
            if key is None:
                continue
            try:
                self.fetch(key)
            finally:
                with self._lock:
                    self._queued.discard(key)

    def fetch(self, key: Key) -> bool:
        """Download what the title is missing. True when every image it has is stored."""
        with self._lock:
            if key not in self._wanted:
                return False
        if all(self.present(key).values()):
            for kind in KINDS:
                if not self.path(key, kind, VARIANTS[kind]):
                    self._make_variant(key, kind)
            return True
        try:
            urls = self._resolve(key[0], key[1]) or {}
        except Exception as e:
            self._fail(key, e)
            return False
        ok = True
        for kind in KINDS:
            url = urls.get(kind)
            if self.path(key, kind):
                if not self.path(key, kind, VARIANTS[kind]):
                    self._make_variant(key, kind)
                continue
            if not url:
                continue
            try:
                body = self._download(url)
                if kind == 'backdrop' and body:
                    body = fit_backdrop(body)
                ext = _sniff(body or b'')
                if not ext or len(body) > MAX_FILE_BYTES:
                    raise ValueError('not an image')
                with self._lock:
                    if key not in self._wanted:   # removed while downloading
                        return False
                d = self._dir(key)
                d.mkdir(parents=True, exist_ok=True)
                tmp = d / f'.{kind}.part'
                tmp.write_bytes(body)
                os.replace(tmp, d / f'{kind}.{ext}')
                self._make_variant(key, kind)
                if kind == 'backdrop':
                    mark = d / 'textless'
                    if urls.get('backdrop_textless'):
                        mark.write_bytes(b'')
                    elif mark.exists():
                        mark.unlink()
            except Exception as e:
                ok = False
                self._fail(key, e)
        if ok:
            with self._lock:
                self._failed.pop(key, None)
        self.enforce_cap()
        return ok

    def _fail(self, key: Key, err: Exception) -> None:
        logger.info('wl-images: %s %s: %s (retry in %ss)', key[0], key[1], err, FAIL_RETRY_S)
        with self._lock:
            self._failed[key] = self._clock()

    # -- disk --------------------------------------------------------------------------------------------------
    def files(self):
        if not self.root.is_dir():
            return []
        out = []
        # Reviewer #1: only this store's own files (<movie|tv>/<digits>/<kind>[@w].<ext>), even in a shared directory.
        names = {f'{k}{v}.{e}' for k in KINDS for v in ('', f'@{VARIANTS[k]}') for e in CTYPES}
        for t in TYPES:
            base = self.root / t
            if not base.is_dir():
                continue
            for d in base.iterdir():
                if not (d.is_dir() and d.name.isascii() and d.name.isdigit()):
                    continue
                for p in d.iterdir():
                    if p.name in names and p.is_file():
                        st = p.stat()
                        out.append((st.st_mtime, st.st_size, p))
        return out

    def size(self) -> int:
        return sum(s for _, s, _ in self.files())

    def enforce_cap(self) -> None:
        """Past the cap, the least recently served files go first (serving touches a file's mtime)."""
        fs = sorted(self.files(), key=lambda x: x[0])
        total = sum(s for _, s, _ in fs)
        for _, s, p in fs:
            if total <= self.cap:
                break
            try:
                p.unlink()
                total -= s
            except OSError:
                pass
            try:
                p.parent.rmdir()
            except OSError:
                pass

    def touch(self, p: Path) -> None:
        try:
            now = self._clock()
            os.utime(p, (now, now))
        except OSError:
            pass

    def reconcile(self) -> None:
        """Startup: leftover partial files go, the cap is applied."""
        if self.root.is_dir():
            for p in self.root.glob('*/*/.*.part'):
                try:
                    p.unlink()
                except OSError:
                    pass
        self.enforce_cap()
