"""Watchlist image store (trial): local copies of each Watchlist item's sheet backdrop and clearlogo
on the server's data volume, so a Watchlist sheet opens from Luma itself instead of TMDb.

Sources are the TMDb originals, stored byte for byte (no lossy downscale and no 1920 ceiling). Phone copies are
separate files (backdrop@1280, logo@300) resized from those sources. Desktop requests the source; a phone requests
?w= and never receives the raw original.

Location: $LUMA_WL_IMAGE_DIR, else $LUMA_DATA_DIR/wl_images (app.paths). Bounded: LUMA_WL_IMAGE_CAP_MB (default
512 MB): past it the least recently served files go first; a title removed from the watchlist is pruned at once.
report() is the measured size of the source and variant files on disk, not an estimate.

Only watchlist items are stored. Downloads run on one background worker (never inside a request). A failed fetch
is retried after FAIL_RETRY_S (never remembered for good). Files: <root>/<movie|tv>/<id>/<backdrop|logo>.<ext>.
A backdrop left by the old w1920 ceiling (no source-original mark) is fetched again once, as the original.
A phone copy is published by writing a unique temp and atomically replacing the final name, so overlapping
requests, store instances, and the worker cannot trip over one shared `.part` file. A lost attempt reuses a
valid winner or misses; it does not raise and it does not serve the raw original.
Nothing here is cached on any device: responses are Cache-Control: no-store, and the endpoint is under /api/
(the service worker never caches /api/).
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
# Phone copies only (the sheet's 1280 backdrop, the w300 logo). The source file is the unmodified original.
VARIANTS = {'backdrop': 1280, 'logo': 300}
# Cache revision on the URL (`?r=`). v348–v350 served the unversioned path as public, immutable for a week;
# this value makes every new request a different URL. `w` is still the only size parameter. Keep in step with
# WL_IMAGE_REV in frontend/src/services/localArt.ts.
URL_REV = '2'
# Written beside a backdrop that was stored as the unmodified TMDb original. Absent on a file left by the w1920 ceiling.
SOURCE_MARK = 'source-original'
WORKER_GAP_S = 0.05
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


def variant_bytes(body: bytes, max_w: int, keep_alpha: bool) -> Optional[bytes]:
    """A phone copy at most max_w wide, or None when `body` is not a decodable image.

    An image already that narrow is returned unchanged. A wider one is resized: a logo stays a PNG with its
    alpha; a backdrop becomes a JPEG. The stored source never passes through here.
    """
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(body))
        im.load()
        width, height = im.size
    except Exception:
        return None
    if width <= max_w:
        return body
    h = max(1, round(height * max_w / width))
    out = BytesIO()
    if keep_alpha:
        im.convert('RGBA').resize((max_w, h), Image.LANCZOS).save(out, 'PNG', optimize=True)
    else:
        im.convert('RGB').resize((max_w, h), Image.LANCZOS).save(out, 'JPEG', quality=86, optimize=True)
    return out.getvalue()


def bytes_servable(body: bytes, max_w: int) -> bool:
    """True when `body` decodes and is at most max_w wide. A phone response must not be the raw original."""
    try:
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(body)) as im:
            im.load()
            return 0 < im.width <= max_w
    except Exception:
        return False


def variant_servable(path: Path, max_w: int) -> bool:
    """True when `path` decodes and is at most max_w wide. A phone response must not be the raw original."""
    try:
        return bytes_servable(path.read_bytes(), max_w)
    except OSError:
        return False


def _file_sig(path: Path):
    """Identity of the bytes at `path` now. A refresh replaces the file; a resize started earlier must not
    publish over the copy made from the new bytes."""
    st = path.stat()
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


# Patched in tests so overlapping replaces can be forced without replacing os.replace process-wide.
_atomic_replace = os.replace


def _decodable(path: Path) -> bool:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.width > 0 and im.height > 0
    except Exception:
        return False


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

    def _source_mark(self, key: Key) -> Path:
        return self._dir(key) / SOURCE_MARK

    def _backdrop_current(self, key: Key) -> bool:
        """A missing backdrop is not a stale one. A file without the original mark is the old w1920 ceiling."""
        if not self.path(key, 'backdrop'):
            return True
        return self._source_mark(key).is_file()

    def _good_variant(self, key: Key, kind: str) -> Optional[Path]:
        """A phone copy that decodes at the variant width. A file that is missing, unreadable, or too wide is not."""
        w = VARIANTS[kind]
        p = self.path(key, kind, w)
        if p is not None and variant_servable(p, w):
            return p
        return None

    def _variants_missing(self, key: Key) -> bool:
        for kind in VARIANTS:
            src = self.path(key, kind)
            if src and self._good_variant(key, kind) is None and _decodable(src):
                return True
        return False

    def _publish(self, directory: Path, filename: str, body: bytes, keep: Optional[Callable[[], bool]] = None) -> bool:
        """Atomically publish `body` as `directory/filename`.

        Each attempt has its own temp (`.<filename>.<pid>.<tid>.<ns>.part`), so two callers never share a name:
        not two requests, not the worker, not a second store on this directory. The replace is atomic on the
        same filesystem. `keep` is checked after the bytes are on disk and before the replace; a false result
        (the title was removed, or the source was refreshed) drops this attempt. The temp is always removed.
        False means this attempt did not publish. It does not mean the final file is absent: another caller
        may have published a valid one. This never raises for a missing temp or a missing directory.
        """
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f'.{filename}.{os.getpid()}.{threading.get_ident()}.{time.monotonic_ns()}.part'
        published = False
        try:
            tmp.write_bytes(body)
            if keep is not None and not keep():
                return False
            _atomic_replace(tmp, directory / filename)
            published = True
            return True
        except OSError:
            return False
        finally:
            try:
                if tmp.is_file():
                    tmp.unlink()
            except OSError:
                pass
            if not published:
                try:
                    directory.rmdir()
                except OSError:
                    pass

    def _needs_fetch(self, key: Key) -> bool:
        if not all(self.present(key).values()) or not self._backdrop_current(key):
            return True
        return self._variants_missing(key)

    def _drop_other_exts(self, directory: Path, stem: str, ext: str) -> None:
        for old in CTYPES:
            if old == ext:
                continue
            o = directory / f'{stem}.{old}'
            try:
                if o.is_file():
                    o.unlink()
            except OSError:
                pass

    def _make_variant(self, key: Key, kind: str) -> bool:
        """Build the phone copy from the current source. Overlapping callers each publish their own temp.

        A source that changes mid-resize is not published over the newer copy (bounded retries, not a stall).
        A temp lost to a concurrent remove is a miss for this attempt when no valid winner is left. A file
        that exists but is too wide is rebuilt. This does not raise FileNotFoundError.
        """
        w = VARIANTS[kind]
        for _attempt in range(3):
            src = self.path(key, kind)
            if src is None:
                return self._good_variant(key, kind) is not None
            try:
                sig = _file_sig(src)
                raw = src.read_bytes()
                if len(raw) != sig[3] or _file_sig(src) != sig:
                    continue
            except OSError:
                continue
            body = variant_bytes(raw, w, keep_alpha=kind == 'logo')
            if body is None:
                return False
            ext = _sniff(body) or src.suffix[1:]

            def _same_source(src: Path = src, sig=sig) -> bool:
                try:
                    return src.is_file() and _file_sig(src) == sig
                except OSError:
                    return False

            if not self._publish(self._dir(key), f'{kind}@{w}.{ext}', body, keep=_same_source):
                # A refreshed source is retried from the new bytes. A removed source, or a lost temp, is a miss
                # unless a valid winner is already in place.
                try:
                    if src.is_file() and _file_sig(src) != sig:
                        continue
                except OSError:
                    pass
                return self._good_variant(key, kind) is not None
            if not _same_source():
                continue
            self._drop_other_exts(self._dir(key), f'{kind}@{w}', ext)
            return self._good_variant(key, kind) is not None
        return self._good_variant(key, kind) is not None

    def ensure_variant(self, key: Key, kind: str) -> Optional[Path]:
        """The phone copy, built from the stored source when it is missing or not a valid width.

        None when it cannot be built. Never a network fetch, never the raw original, and never an exception
        because another caller published or removed the same copy first.
        """
        got = self._good_variant(key, kind)
        if got is not None:
            return got
        self._make_variant(key, kind)
        return self._good_variant(key, kind)

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
        """Queue a fetch for a title still missing a file, a phone copy, or an original backdrop (not while its
        last failure is recent)."""
        if not self._needs_fetch(key):
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
            names = {f'{k}{v}.{e}' for k in KINDS for v in ('', f'@{VARIANTS[k]}') for e in CTYPES} | {'textless', SOURCE_MARK}
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
            # v350: background work yields between titles, so a backfill (every stored title's phone copies after an
            # update) never crowds out the requests a sheet open is waiting on.
            time.sleep(WORKER_GAP_S)

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
        """Download what the title is missing. True when every image it has is stored as the original."""
        with self._lock:
            if key not in self._wanted:
                return False
        if not self._needs_fetch(key):
            return True
        if all(self.present(key).values()) and self._backdrop_current(key):
            for kind in KINDS:
                if self.path(key, kind) and self._good_variant(key, kind) is None:
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
            have = self.path(key, kind)
            stale = kind == 'backdrop' and bool(have) and not self._backdrop_current(key)
            if have and not stale:
                if self._good_variant(key, kind) is None:
                    self._make_variant(key, kind)
                continue
            if not url:
                if stale:   # keep the capped file; retry later rather than dropping the only copy
                    ok = False
                    self._fail(key, ValueError('no original backdrop'))
                continue
            try:
                body = self._download(url)
                ext = _sniff(body or b'')
                if not ext or not body or len(body) > MAX_FILE_BYTES:
                    raise ValueError('not an image')
                with self._lock:
                    if key not in self._wanted:   # removed while downloading
                        return False
                d = self._dir(key)

                def _still_wanted() -> bool:
                    with self._lock:
                        return key in self._wanted

                if not self._publish(d, f'{kind}.{ext}', body, keep=_still_wanted):
                    with self._lock:
                        if key not in self._wanted:
                            return False
                    raise OSError('source publish lost')
                self._drop_other_exts(d, kind, ext)
                self._make_variant(key, kind)
                if kind == 'backdrop':
                    self._source_mark(key).write_bytes(b'')
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

    def report(self) -> Dict[str, int]:
        """Measured disk use of this store's source and variant files (stat sizes), and the cap. Not an estimate.
        The source-original mark and the textless flag are empty markers and are not counted."""
        source_bytes = variant_total = 0
        n = 0
        for _, sz, p in self.files():
            n += 1
            if '@' in p.name:
                variant_total += sz
            else:
                source_bytes += sz
        return {'bytes': source_bytes + variant_total, 'files': n, 'cap': self.cap,
                'source_bytes': source_bytes, 'variant_bytes': variant_total}

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
            if p.name.startswith('backdrop.') and '@' not in p.name:
                try:
                    self._source_mark((p.parent.parent.name, int(p.parent.name))).unlink(missing_ok=True)
                except (OSError, ValueError):
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
        """Startup: leftover partial files go, the cap is applied, and the measured size is reported."""
        if self.root.is_dir():
            for p in self.root.glob('*/*/.*.part'):
                try:
                    p.unlink()
                except OSError:
                    pass
        self.enforce_cap()
        info = self.report()
        logger.info('wl-images: %s files, %s bytes (sources %s, variants %s), cap %s',
                    info['files'], info['bytes'], info['source_bytes'], info['variant_bytes'], info['cap'])
