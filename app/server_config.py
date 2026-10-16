"""Server configuration - port, bind address, URL base, HTTPS."""

import os
from pathlib import Path


def get_port() -> int:
    """Get server port from settings or environment."""
    return int(os.environ.get('LUMA_PORT', '5000'))


def get_bind_address() -> str:
    """Get bind address from settings or environment."""
    return os.environ.get('LUMA_BIND', '0.0.0.0')


def get_url_base() -> str:
    """Get URL base from settings or environment (e.g., '/luma' or '')."""
    base = os.environ.get('LUMA_URL_BASE', '').strip()
    # Ensure starts with / if not empty, and no trailing slash
    if base:
        if not base.startswith('/'):
            base = '/' + base
        base = base.rstrip('/')
    return base


def get_https_cert() -> tuple[str, str] | None:
    """Get HTTPS cert and key paths, or None if not configured."""
    cert = os.environ.get('LUMA_HTTPS_CERT', '').strip()
    key = os.environ.get('LUMA_HTTPS_KEY', '').strip()
    
    if cert and key:
        cert_path = Path(cert)
        key_path = Path(key)
        
        if cert_path.exists() and key_path.exists():
            return (str(cert_path), str(key_path))
        else:
            raise FileNotFoundError(f"HTTPS cert or key not found: {cert}, {key}")
    
    return None


def get_session_secret(settings) -> str:
    """Get or generate session secret."""
    # Try settings first
    secret = settings.get('security.session_secret', '')
    
    if not secret:
        # Try environment
        secret = os.environ.get('LUMA_SESSION_SECRET', '').strip()
    
    if not secret:
        # Generate and save
        import secrets as py_secrets
        secret = py_secrets.token_urlsafe(32)
        settings.update({'security': {'session_secret': secret}})
    
    return secret
