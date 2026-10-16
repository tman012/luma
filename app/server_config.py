"""Server configuration - port, bind address, URL base, HTTPS."""

import os
from pathlib import Path


def get_port(settings=None) -> int:
    """Get server port from settings or environment.
    
    Priority:
    1. LUMA_PORT env var
    2. settings general.port, network.flask_port (the Network tab) or security.port
    3. Default 5001
    """
    # Env var takes precedence
    port_str = os.environ.get('LUMA_PORT', '').strip()
    if port_str:
        return int(port_str)
    
    # Try settings
    if settings:
        port = (settings.get('general.port') or settings.get('network.flask_port')
                or settings.get('security.port'))
        if port:
            return int(port)
    
    # Default
    return 5001


def get_bind_address(settings=None) -> str:
    """Get bind address from settings or environment.
    
    Priority:
    1. LUMA_BIND env var
    2. settings['general']['bind'] or settings['security']['bind']
    3. Default 0.0.0.0
    """
    # Env var takes precedence
    bind = os.environ.get('LUMA_BIND', '').strip()
    if bind:
        return bind
    
    # Try settings
    if settings:
        bind = settings.get('general.bind') or settings.get('security.bind')
        if bind:
            return str(bind)
    
    # Default
    return '0.0.0.0'


def get_url_base(settings=None) -> str:
    """Get URL base from settings or environment (e.g., '/luma' or '').
    
    Priority:
    1. LUMA_URL_BASE env var
    2. settings['general']['url_base'] or settings['security']['url_base']
    3. Default ''
    """
    # Env var takes precedence
    base = os.environ.get('LUMA_URL_BASE', '').strip()
    
    # Try settings if no env var
    if not base and settings:
        base = (settings.get('general.url_base') or settings.get('security.url_base') or '').strip()
    
    # Ensure starts with / if not empty, and no trailing slash
    if base:
        if not base.startswith('/'):
            base = '/' + base
        base = base.rstrip('/')
    return base


def get_https_cert(settings=None) -> tuple[str, str] | None:
    """Get HTTPS cert and key paths, or None if not configured.
    
    Priority:
    1. LUMA_HTTPS_CERT and LUMA_HTTPS_KEY env vars
    2. settings['security']['https_cert'] and settings['security']['https_key']
    3. None
    """
    # Env vars take precedence
    cert = os.environ.get('LUMA_HTTPS_CERT', '').strip()
    key = os.environ.get('LUMA_HTTPS_KEY', '').strip()
    
    # Try settings if no env vars
    if not cert and not key and settings:
        cert = (settings.get('security.https_cert') or '').strip()
        key = (settings.get('security.https_key') or '').strip()
    
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
