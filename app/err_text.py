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
_PATTERNS = (SECRET_QS, SECRET_AUTH)
MASK = '***'


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
    return isinstance(value, str) and MASK in value and bool(_masked_parts(value))


def _unmask_str(new: str, old):
    masked = _masked_parts(new)
    if not masked:
        return new
    if not isinstance(old, str):
        # nothing stored to restore from: drop the masked value, never store the marker
        out, last = [], 0
        for s, e, _ in masked:
            out.append(new[last:s]); last = e
        return ''.join(out) + new[last:]
    if new == redact(old):
        return old
    secrets = _secret_parts(old)
    if len(secrets) != len(masked) or any(p != sp for (_, _, p), (sp, _) in zip(masked, secrets)):
        return old                                    # ambiguous: keep what is stored
    out, last = [], 0
    for (s, e, _), (_, val) in zip(masked, secrets):
        out.append(new[last:s]); out.append(val); last = e
    merged = ''.join(out) + new[last:]
    return old if has_mask(merged) else merged


def unmask(new, old):
    """*new* with every backstop-masked credential put back from *old* (same shape:
    dicts by key, lists by index). Unchanged masked strings keep the stored value; an
    edited one keeps the edit with the stored secrets substituted; ambiguous keeps stored."""
    if isinstance(new, str):
        return _unmask_str(new, old)
    if isinstance(new, dict):
        o = old if isinstance(old, dict) else {}
        return {k: unmask(v, o.get(k)) for k, v in new.items()}
    if isinstance(new, list):
        o = old if isinstance(old, list) else []
        return [unmask(v, o[i] if i < len(o) else None) for i, v in enumerate(new)]
    return new
