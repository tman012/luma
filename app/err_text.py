"""Fixed client-facing error text. Exception detail never leaves the server: it is
logged with credentials masked, and the caller gets a fixed message instead."""
import logging
import re

_logger = logging.getLogger('app.errors')

SECRET_QS = re.compile(r'((?:api_?key|access_token|token|apikey|passkey)=)[^&\s\'"\\]+', re.I)
SECRET_AUTH = re.compile(r'((?:authorization|x-plex-token|x-api-key)[\'"]?\s*[:=]\s*[\'"]?(?:bearer\s+|basic\s+)?)[^\s\'",&}\\]+', re.I)

GENERIC = 'Something went wrong'


def redact(value) -> str:
    """Text with credential query params and auth headers masked."""
    return SECRET_AUTH.sub(r'\1***', SECRET_QS.sub(r'\1***', str(value)))


def public(exc, message: str = GENERIC, where: str = '') -> str:
    """Log the exception (redacted) and return the fixed text for the client."""
    try:
        _logger.warning('%s error (%s): %s', where or 'API', type(exc).__name__, redact(exc))
    except Exception:
        pass
    return message


# ── Settings round-trip: never persist the backstop's mask ─────────────
# The /api backstop masks credential-looking parts of ANY string (a private RSS feed URL's
# passkey=, a webhook's token=). Settings echoes those strings back on save, so every
# settings write runs the submitted values through unmask() against what is stored.
from urllib.parse import urlsplit as _urlsplit

_PATTERNS = (SECRET_QS, SECRET_AUTH)
MASK = '***'
_ENC_MASK = re.compile(r'%2a%2a%2a', re.I)
_ITEM_KEYS = ('id', 'name', 'key')


class UnmaskAmbiguous(ValueError):
    """A masked value can't be restored safely: the user has to re-enter the secret."""

    def __init__(self, path: str = ''):
        self.message = f'Re-enter the hidden value for {path or "this setting"}'   # app-authored, safe to show
        super().__init__(self.message)
        self.path = path


def _norm(text: str) -> str:
    return _ENC_MASK.sub(MASK, text)


def _masked_parts(text: str):
    """[(start, end, prefix_casefold)] of masked values in text, in order."""
    out = []
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            if text[m.end(1):m.end()] == MASK:
                out.append((m.end(1), m.end(), m.group(1).casefold()))
    return sorted(out)


def _secret_parts(text: str):
    """[(prefix_casefold, value)] of credential values in text, in order."""
    out = []
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            out.append((m.start(), m.group(1).casefold(), text[m.end(1):m.end()]))
    return [(p, v) for _, p, v in sorted(out)]


def has_mask(value) -> bool:
    if not isinstance(value, str):
        return False
    v = _norm(value)
    return v.strip() == MASK or (MASK in v and bool(_masked_parts(v)))


def _origin(text: str):
    try:
        u = _urlsplit(text.strip())
    except ValueError:
        return None
    if u.scheme and u.netloc:
        return (u.scheme.casefold(), (u.hostname or '').casefold(), u.port)
    return None


def _strip_masks(text: str) -> str:
    out, last = [], 0
    for s, e, _ in _masked_parts(text):
        out.append(text[last:s]); last = e
    return ''.join(out) + text[last:]


def _unmask_str(new: str, old, strict: bool, path: str):
    if MASK not in new and '%2' not in new.casefold():
        return new
    norm = _norm(new)
    if norm.strip() == MASK:                          # a typed/echoed bare mask is never a value
        if isinstance(old, str):
            return old
        if strict:
            raise UnmaskAmbiguous(path)
        return ''
    masked = _masked_parts(norm)
    if not masked:
        return new
    if isinstance(old, str) and norm == redact(old):
        return old
    if not isinstance(old, str) or _origin(norm) != _origin(old):
        # nothing stored, or another scheme/host: the stored secret must not follow the URL
        if strict:
            raise UnmaskAmbiguous(path)
        return old if isinstance(old, str) else _strip_masks(norm)
    stored = {}
    for p, v in _secret_parts(old):
        stored.setdefault(p, []).append(v)
    out, last = [], 0
    for s, e, p in masked:
        vals = stored.get(p) or []
        if not vals or (len(set(vals)) > 1 and len(vals) != sum(1 for *_, q in masked if q == p)):
            if strict:
                raise UnmaskAmbiguous(path)
            return old
        out.append(norm[last:s]); out.append(vals.pop(0)); last = e
    merged = ''.join(out) + norm[last:]
    if has_mask(merged):
        if strict:
            raise UnmaskAmbiguous(path)
        return old
    return merged                                      # stored params not re-sent are dropped (the edit)


def _item_key(item):
    if isinstance(item, dict):
        for k in _ITEM_KEYS:
            if item.get(k) not in (None, ''):
                return (k, item[k])
        return None
    if isinstance(item, str):
        o = _origin(_norm(item))
        return ('origin', o) if o else None
    return None


def _match_list(new: list, old: list):
    """Old counterpart for each new item: by id/name/key (dicts) or scheme+host (URL strings);
    position only when neither side has such keys and the lengths agree."""
    index = {}
    for o in old:
        k = _item_key(o)
        if k is not None:
            index.setdefault(k, []).append(o)
    out = []
    for i, n in enumerate(new):
        k = _item_key(n)
        if k is not None:
            cands = index.get(k) or []
            out.append(cands[0] if len(cands) == 1 else None)
        elif len(new) == len(old) and all(_item_key(o) is None for o in old):
            out.append(old[i])
        else:
            out.append(None)
    return out


def unmask(new, old, strict: bool = False, path: str = ''):
    """*new* with every backstop-masked credential put back from *old*. Dicts match by key;
    list items by id/name/key or URL scheme+host. Unchanged masked strings keep the stored
    value; an edit keeps the edit with the stored secrets substituted (same scheme+host
    only). When that can't be done safely, strict raises UnmaskAmbiguous; otherwise the
    stored value is kept (or the masked part dropped). The mask itself is never returned."""
    if isinstance(new, str):
        return _unmask_str(new, old, strict, path)
    if isinstance(new, dict):
        o = old if isinstance(old, dict) else {}
        return {k: unmask(v, o.get(k), strict, f'{path}.{k}' if path else str(k)) for k, v in new.items()}
    if isinstance(new, list):
        o = old if isinstance(old, list) else []
        return [unmask(v, ov, strict, path) for v, ov in zip(new, _match_list(new, o))]
    return new

def remote_reason(exc, default: str, where: str = 'remote_mover') -> str:
    """Fixed, diagnosable text for a remote mover failure (no paths, stderr or hosts)."""
    text = str(exc).lower()
    if 'stall' in text:
        message = 'remote copy stalled'
    elif 'vanish' in text:
        message = 'remote partial vanished'
    elif type(exc).__name__ == 'RemoteConnectionLost':
        message = 'remote connection lost'
    else:
        message = default
    return public(exc, message, where)
