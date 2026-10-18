"""App logs persisted under /app/data/logs (LUMA_LOG_DIR), rotated 10 MB x 5, in addition to stdout.

Security (Ops/PM): every handler (file and stream) formats with RedactingFormatter, which redacts
the WHOLE formatted record, message, exception text and traceback included (credential query params,
X-Api-Key / Authorization / X-Plex-Token style headers, "password"/"token"/"api_key" style key-value
pairs, Bearer tokens). Log files are created 0600 and stay 0600 through rotation (opened with mode
0600 under a 077 umask, then chmod'ed; rotated files chmod'ed in doRollover). At boot, existing
luma.log* files are rewritten redacted (0600, atomic replace) before the handler attaches.
"""
from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from typing import Iterable, Optional

LOG_DIR_ENV = 'LUMA_LOG_DIR'
DEFAULT_DIR = '/app/data/logs'
FILE_NAME = 'luma.log'
MAX_BYTES = 10 * 1024 * 1024
BACKUPS = 5
FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
FILE_MODE = 0o600
MASK = '***'

_KEYS = (r'api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|auth[_-]?token|token|passkey|password|passwd|pwd'
         r'|secret|client[_-]?secret|session[_-]?id|sid|x[_-]?plex[_-]?token|plex[_-]?token|rsskey|authkey|torrent_pass')
_PATTERNS = (
    # query/form params: ?api_key=..., &token=..., X-Plex-Token=...
    re.compile(r'(?i)((?<![A-Za-z0-9_])(?:' + _KEYS + r')=)[^&\s\'",;)]+'),
    # headers: X-Api-Key: ..., Authorization: Basic/Bearer ..., Cookie: ...
    re.compile(r'(?i)((?:x-api-key|x-plex-token|x-auth-token|api-key|authorization|proxy-authorization|cookie|set-cookie)'
               r'["\']?\s*[:=]\s*["\']?)(?:(?:bearer|basic|token)\s+)?[^\s"\',}]+'),
    # JSON / repr key-values: "password": "x", 'api_key': 'x', password: x
    re.compile(r'(?i)((?<![A-Za-z0-9_])["\']?(?:' + _KEYS + r')["\']?\s*:\s*["\']?)[^\s"\',}]+'),
    # a bare bearer token anywhere
    re.compile(r'(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+'),
)


def redact(text: str) -> str:
    out = str(text)
    for pat in _PATTERNS:
        out = pat.sub(lambda m: m.group(1) + MASK, out)
    return out


class RedactingFormatter(logging.Formatter):
    """Formats the record as usual (traceback and stack included), then redacts the whole line."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def log_dir() -> str:
    return os.environ.get(LOG_DIR_ENV) or DEFAULT_DIR


def _private(path: str) -> None:
    try:
        os.chmod(path, FILE_MODE)
    except OSError:
        pass


class PrivateRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler whose files are 0600 from creation, and after every rollover."""

    def _open(self):
        old = os.umask(0o077)
        try:
            fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_CLOEXEC', 0), FILE_MODE)
        finally:
            os.umask(old)
        _private(self.baseFilename)
        return os.fdopen(fd, self.mode if 'a' in self.mode else 'a', encoding=self.encoding, errors=self.errors)

    def rotate(self, source: str, dest: str) -> None:
        super().rotate(source, dest)
        _private(dest)

    def doRollover(self) -> None:
        super().doRollover()
        for i in range(1, self.backupCount + 1):
            p = self.rotation_filename(f'{self.baseFilename}.{i}')
            if os.path.exists(p):
                _private(p)
        if os.path.exists(self.baseFilename):
            _private(self.baseFilename)


def scrub(directory: Optional[str] = None) -> int:
    """Boot: rewrite every existing luma.log* redacted (atomic, 0600). Returns how many files changed."""
    d = directory or log_dir()
    n = 0
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return 0
    for name in names:
        if not (name == FILE_NAME or name.startswith(FILE_NAME + '.')) or '.scrub-' in name:
            continue
        path = os.path.join(d, name)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
            clean = redact(text)
            if clean != text:
                tmp = f'{path}.scrub-{os.getpid()}'
                old = os.umask(0o077)
                try:
                    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), FILE_MODE)
                finally:
                    os.umask(old)
                with os.fdopen(fd, 'w', encoding='utf-8') as out:
                    out.write(clean)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(tmp, path)
                n += 1
            _private(path)
        except OSError as e:
            logging.getLogger(__name__).warning('Log scrub skipped %s: %s', name, type(e).__name__)
    return n


def redact_handlers(root: Optional[logging.Logger] = None) -> None:
    """Give every handler on *root* (stdout included) the redacting formatter, keeping its format."""
    root = root or logging.getLogger()
    for h in root.handlers:
        if not isinstance(h.formatter, RedactingFormatter):
            fmt = h.formatter._fmt if h.formatter is not None else FORMAT    # noqa: SLF001
            datefmt = h.formatter.datefmt if h.formatter is not None else None
            h.setFormatter(RedactingFormatter(fmt, datefmt))


def attach(filters: Iterable[logging.Filter] = (), root: Optional[logging.Logger] = None) -> Optional[RotatingFileHandler]:
    """Add the rotating file handler to the root logger once; never raises (stdout keeps working)."""
    root = root or logging.getLogger()
    path = os.path.join(log_dir(), FILE_NAME)
    for h in root.handlers:
        if isinstance(h, RotatingFileHandler) and os.path.abspath(getattr(h, 'baseFilename', '')) == os.path.abspath(path):
            return h
    try:
        os.makedirs(log_dir(), mode=0o700, exist_ok=True)
        try:
            os.chmod(log_dir(), 0o700)            # an older 0750 folder too
        except OSError:
            pass
        scrub()
        h = PrivateRotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding='utf-8', delay=True)
    except OSError as e:
        logging.getLogger(__name__).warning('File logs off (%s): %s', log_dir(), type(e).__name__)
        return None
    h.setFormatter(RedactingFormatter(FORMAT))
    h.setLevel(logging.INFO)
    for f in filters:
        h.addFilter(f)
    root.addHandler(h)
    return h
