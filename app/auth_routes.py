"""Authentication routes for forms-based login."""

import logging
import time
import secrets
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, make_response, redirect, url_for, render_template_string, session, current_app
from werkzeug.security import check_password_hash
import app.user_auth as user_auth
import app.access as access

logger = logging.getLogger(__name__)

bp = Blueprint('auth', __name__)

# Session cookie name, and the retired cookie that is only ever expired
AUTH_COOKIE = 'luma_auth_session'
LEGACY_SESSION_COOKIE = 'luma_session'


def _get_client_ip(request, settings) -> str:
    """Get client IP using trusted-proxy-aware access.client_ip()."""
    ip = access.client_ip(request, settings)
    return ip if ip else request.remote_addr or '0.0.0.0'


def _is_safe_redirect(url: str, url_base: str = '') -> bool:
    """True only for a same-site path such as /movies?x=1.

    Refused: empty values, anything not starting with exactly one '/', any
    backslash or character <= 0x20 (browsers strip tab/newline, so '/\t/evil.com'
    becomes '//evil.com'), DEL, and anything urlsplit reads as having a scheme
    or host. The path is checked again after percent-decoding.
    """
    from urllib.parse import unquote, urlsplit

    if not url or not isinstance(url, str):
        return False
    decoded_path = unquote(url.split('?', 1)[0].split('#', 1)[0])
    for candidate in (url, decoded_path):
        if not candidate.startswith('/') or candidate.startswith('//'):
            return False
        if '\\' in candidate or any(ord(ch) <= 0x20 or ord(ch) == 0x7f for ch in candidate):
            return False
        try:
            parts = urlsplit(candidate)
        except ValueError:
            return False
        if parts.scheme or parts.netloc:
            return False
    return True


def _safe_redirect_url(next_param: str, default: str = '/') -> str:
    """Return a safe redirect URL, applying URL base prefix.
    
    If next_param is unsafe, returns default with URL base.
    """
    url_base = current_app.config.get('URL_BASE', '')
    
    if not _is_safe_redirect(next_param, url_base):
        return url_base + default if url_base else default
    
    # Apply URL base prefix if not already there
    if url_base and next_param != url_base and not next_param.startswith(url_base + '/'):
        return url_base + next_param
    
    return next_param


def _generate_csrf_token() -> str:
    """Generate a CSRF token and store in session."""
    token = secrets.token_urlsafe(32)
    session['csrf_token'] = token
    return token


def _validate_csrf_token(token: str) -> bool:
    """Validate CSRF token against session."""
    expected = session.get('csrf_token')
    if not expected or not token:
        return False
    return secrets.compare_digest(expected, token)


def is_same_origin(value: str) -> bool:
    """True when an Origin or Referer value has exactly this request's scheme://netloc.

    The single origin check used by logout and the API CSRF guard. Scheme and
    host reflect X-Forwarded-Proto/Host only for trusted proxies (see
    access.TrustedProxyMiddleware). Prefix tricks such as
    http://127.0.0.1:5302.evil.com never match http://127.0.0.1:5302.
    """
    from urllib.parse import urlsplit

    if not value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if not parsed.scheme or not parsed.netloc:
        return False
    return (parsed.scheme.lower() == request.scheme.lower()
            and parsed.netloc.lower() == request.host.lower())


# Simple HTML login page
LOGIN_PAGE_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <meta name="theme-color" content="#15102a">
    <meta name="color-scheme" content="dark">
    <title>Sign in - Luma</title>
    <meta name="apple-mobile-web-app-title" content="Luma">
    <link rel="icon" href="{{ url_for('static', filename='images/icon-192.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='images/apple-touch-icon.png') }}">
    <style>
        /* Mirrors the SPA theme tokens in frontend/src/index.css (@theme);
           tests/test_login_theme_tokens.py keeps them in sync. */
        :root {
            --color-kiosk-black: #15102a;
            --color-glass-5: rgb(255 255 255 / 0.05);
            --color-glass-10: rgb(255 255 255 / 0.10);
            --color-glass-15: rgb(255 255 255 / 0.15);
            --color-glass-20: rgb(255 255 255 / 0.20);
            --color-accent: #3b82f6;
            --color-accent-hover: #60a5fa;
            --color-text-primary: rgb(255 255 255 / 0.95);
            --color-text-secondary: rgb(255 255 255 / 0.70);
            --color-text-muted: rgb(255 255 255 / 0.50);
            --font-luma: system-ui, -apple-system, sans-serif;
            /* Shared with the SPA: StartUI NAVY_BG and fieldInput. */
            --navy-bg: __NAVY_BG__;
            --field-bg: rgb(255 255 255 / 0.08);
            --field-border: rgb(255 255 255 / 0.14);
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        html, body { background: var(--color-kiosk-black); }
        body {
            background: var(--navy-bg) fixed var(--color-kiosk-black);
            font-family: var(--font-luma);
            min-height: 100vh;
            min-height: 100dvh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 24px 16px;
            color: var(--color-text-primary);
            -webkit-font-smoothing: antialiased;
        }
        .brand { display: flex; justify-content: center; margin-bottom: 32px; }
        .brand img { width: clamp(200px, 56vw, 360px); height: auto; user-select: none; }
        .card {
            width: 100%;
            max-width: 360px;
        }
        .field { margin-bottom: 18px; }
        .field label {
            display: block;
            font-size: 13px;
            font-weight: 500;
            color: var(--color-text-secondary);
            margin: 0 0 6px 2px;
        }
        .field input {
            width: 100%;
            height: 50px;
            padding: 0 16px;
            background: var(--field-bg);
            border: 1px solid var(--field-border);
            border-radius: 12px;
            font: inherit;
            font-size: 16px;
            color: var(--color-text-primary);
            transition: border-color 0.2s;
        }
        .field input:focus { outline: none; border-color: var(--color-accent); }
        .remember {
            display: flex;
            align-items: center;
            gap: 10px;
            margin: 4px 0 8px 2px;
            font-size: 14px;
            color: var(--color-text-secondary);
            cursor: pointer;
        }
        .remember input { width: 18px; height: 18px; accent-color: var(--color-accent); }
        button[type="submit"] {
            width: 100%;
            height: 50px;
            background: var(--color-accent);
            color: var(--color-text-primary);
            border: none;
            border-radius: 999px;
            font: inherit;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
        }
        button[type="submit"]:hover { background: var(--color-accent-hover); }
        /* Plain red line between Remember me and Sign in. Its height is
           reserved, so showing an error never moves the form. */
        .error {
            color: #ef4444;
            font-size: 13px;
            line-height: 18px;
            min-height: 18px;
            margin: 0 0 16px 2px;
        }
    </style>
</head>
<body>
    <div class="brand">
        <img src="{{ url_for('static', filename='images/luma_text.webp') }}" alt="" draggable="false">
    </div>
    <main class="card">
        <form method="POST" action="{{ url_for('auth.login', next=next_url) }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <div class="field">
                <label for="username">Email or username</label>
                <input type="text" id="username" name="username" value="{{ username }}" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" required {% if not username %}autofocus{% endif %}>
            </div>
            <div class="field">
                <label for="password">Password</label>
                <input type="password" id="password" name="password" autocomplete="current-password" required {% if username %}autofocus{% endif %}>
            </div>
            <label class="remember">
                <input type="checkbox" id="remember" name="remember" checked>
                <span>Remember me</span>
            </label>
            <div class="error{% if rate_limited %} rate-limited{% endif %}" role="alert" aria-live="polite">{{ error }}</div>
            <button type="submit">Sign in</button>
        </form>
    </main>
</body>
</html>'''

from app.theme import NAVY_BG as _NAVY_BG  # noqa: E402
LOGIN_PAGE_HTML = LOGIN_PAGE_HTML.replace('__NAVY_BG__', _NAVY_BG)


def _no_store(response):
    """Auth responses must never be cached by the browser or the service worker."""
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    return response


# Session key for the name typed before a failed attempt (one read).
_LAST_USERNAME = 'login_username'


def _login_page(error='', rate_limited=False, next_url='/', username='', status=200):
    html = render_template_string(LOGIN_PAGE_HTML,
                                  error=error,
                                  rate_limited=rate_limited,
                                  next_url=next_url,
                                  username=username,
                                  csrf_token=_generate_csrf_token())
    return _no_store(make_response(html, status))


@bp.after_request
def _auth_no_store(response):
    return _no_store(response)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page and authentication endpoint."""
    from app.settings_manager import SettingsManager
    from app import login_limiter
    settings = SettingsManager()
    
    next_param = request.args.get('next', '/')
    safe_next = _safe_redirect_url(next_param)

    from app import minimal as _minimal
    if _minimal.enabled():
        # One sign-in screen: the SPA's one-page form (it reads ?next=).
        # A stale form post (session expired) lands back on it, which then
        # says so (?expired=1).
        if request.method in ('GET', 'HEAD'):
            from app.main import _serve_spa
            return _no_store(make_response(_serve_spa()))
        return redirect(url_for('auth.login', next=safe_next, expired='1'))

    if request.method in ('GET', 'HEAD'):
        # Generate CSRF token
        csrf_token = _generate_csrf_token()
        
        # Show login form
        error = request.args.get('error', '')
        rate_limited = request.args.get('rate_limited') == '1'
        # The name typed before a failed attempt comes back filled in.
        return _login_page(error=error, rate_limited=rate_limited,
                           next_url=safe_next,
                           username=session.pop(_LAST_USERNAME, '') if error else '')
    
    # POST - process login
    # Validate CSRF token
    csrf_token = request.form.get('csrf_token', '')
    if not _validate_csrf_token(csrf_token):
        logger.warning("[auth] CSRF token validation failed")
        # Re-show the form with a fresh token; the typed name is kept.
        return _login_page(error='Your session expired. Sign in again.',
                           next_url=safe_next,
                           username=request.form.get('username', '').strip(),
                           status=400)
    
    client_ip = _get_client_ip(request, settings)
    username = request.form.get('username', '').strip()
    
    # Check rate limit using login_limiter
    wait_time = login_limiter.retry_after(client_ip, username)
    if wait_time > 0:
        mins = max(1, (wait_time + 59) // 60)
        logger.warning(f"[auth] Rate limit exceeded for {username} from {client_ip}")
        session[_LAST_USERNAME] = username
        return redirect(url_for('auth.login', 
                               error=f'Too many attempts. Try again in {mins} min.',
                               rate_limited='1',
                               next=safe_next))
    
    password = request.form.get('password', '')
    remember = request.form.get('remember') == 'on'
    
    if not username or not password:
        session[_LAST_USERNAME] = username
        return redirect(url_for('auth.login', 
                               error='Username and password required',
                               next=safe_next))
    
    # Authenticate user
    user = user_auth.authenticate(username, password)
    
    if not user:
        login_limiter.record_failure(client_ip, username)
        logger.info(f"[auth] Failed login attempt for user {username} from {client_ip}")
        session[_LAST_USERNAME] = username
        return redirect(url_for('auth.login', 
                               error='Invalid email, username, or password',
                               next=safe_next))
    
    # Login successful - clear rate limit; the form token is single-use
    login_limiter.record_success(client_ip, username)
    session.pop('csrf_token', None)
    session.pop(_LAST_USERNAME, None)
    
    # Create session
    token = user_auth.create_session(user['id'])
    
    # Set cookie and redirect to safe URL
    response = make_response(redirect(safe_next))
    
    # Calculate expiry (30 days if remember, else session cookie)
    if remember:
        max_age = 30 * 24 * 60 * 60  # 30 days in seconds
    else:
        max_age = None  # Session cookie
    
    # Secure flag per scheme (X-Forwarded-Proto only from trusted proxies)
    secure = request.is_secure
    
    # Cookie path respects URL base
    url_base = current_app.config.get('URL_BASE', '') or '/'
    
    response.set_cookie(
        AUTH_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        samesite='Lax',
        secure=secure,
        path=url_base
    )
    
    logger.info(f"[auth] Successful login for user {username} from {client_ip}")
    return response


@bp.route('/logout', methods=['POST'])
def logout():
    """Logout endpoint - POST-only, deletes session and cookie."""
    # CSRF protection: exact same-origin Origin (or Referer) required
    origin = request.headers.get('Origin')
    referer = request.headers.get('Referer')
    if origin:
        valid_request = is_same_origin(origin)
    else:
        valid_request = is_same_origin(referer)
    
    if not valid_request:
        logger.warning(f"[auth] Logout CSRF check failed: origin={origin}, referer={referer}, host={request.host}")
        return jsonify({"error": "Invalid request"}), 403
    
    token = request.cookies.get(AUTH_COOKIE)
    
    if token:
        user_auth.delete_session(token)
    
    url_base = current_app.config.get('URL_BASE', '') or '/'
    login_url = (url_base.rstrip('/') + '/login') if url_base != '/' else '/login'
    
    response = make_response(redirect(login_url))
    response.delete_cookie(AUTH_COOKIE, path=url_base)
    
    return response


def init_app(app):
    """Register auth blueprint."""
    app.register_blueprint(bp)
