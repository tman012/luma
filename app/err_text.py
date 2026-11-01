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
