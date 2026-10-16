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
    <meta name="theme-color" content="#111318">
    <meta name="color-scheme" content="dark">
    <title>Sign in - Luma</title>
    <link rel="icon" href="{{ url_for('static', filename='images/icon-192.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='images/apple-touch-icon.png') }}">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Google+Sans+Flex:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --surface: #111318;
            --surface-container: #1d2024;
            --surface-container-high: #282a2f;
            --on-surface: #e2e2e9;
            --on-surface-variant: #c4c6d0;
            --outline: #8e9099;
            --outline-variant: #44474f;
            --primary: #adc7ff;
            --on-primary: #002e68;
            --error: #ffb4ab;
            --error-container: #93000a;
            --on-error-container: #ffdad6;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        html, body { background: var(--surface); }
        body {
            font-family: 'Google Sans Flex', 'Google Sans', Roboto, -apple-system, BlinkMacSystemFont, sans-serif;
            min-height: 100vh;
            min-height: 100dvh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
            color: var(--on-surface);
            -webkit-font-smoothing: antialiased;
        }
        .card {
            background: var(--surface-container-high);
            border-radius: 28px;
            padding: 40px 32px 32px;
            width: 100%;
            max-width: 400px;
        }
        .brand {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 16px;
            margin-bottom: 32px;
        }
        .brand img { width: 72px; height: 72px; border-radius: 20px; }
        .brand h1 { font-size: 28px; font-weight: 500; letter-spacing: 0; }
        .field { margin-bottom: 20px; }
        .field label {
            display: block;
            font-size: 12px;
            font-weight: 500;
            letter-spacing: 0.4px;
            color: var(--on-surface-variant);
            margin: 0 0 6px 4px;
        }
        .field input {
            width: 100%;
            height: 56px;
            padding: 0 16px;
            background: var(--surface-container);
            border: 1px solid var(--outline);
            border-radius: 12px;
            font: inherit;
            font-size: 16px;
            color: var(--on-surface);
            transition: border-color 0.2s;
        }
        .field input:focus { outline: none; border: 2px solid var(--primary); padding: 0 15px; }
        .remember {
            display: flex;
            align-items: center;
            gap: 12px;
            margin: 4px 0 28px 4px;
            font-size: 14px;
            color: var(--on-surface-variant);
            cursor: pointer;
        }
        .remember input { width: 18px; height: 18px; accent-color: var(--primary); }
        button[type="submit"] {
            width: 100%;
            height: 48px;
            background: var(--primary);
            color: var(--on-primary);
            border: none;
            border-radius: 24px;
            font: inherit;
            font-size: 15px;
            font-weight: 500;
            cursor: pointer;
        }
        button[type="submit"]:hover { filter: brightness(1.06); }
        button[type="submit"]:active { filter: brightness(0.94); }
        .error {
            background: var(--error-container);
            color: var(--on-error-container);
            padding: 12px 16px;
            border-radius: 12px;
            margin-bottom: 20px;
            font-size: 14px;
        }
    </style>
</head>
<body>
    <main class="card">
        <div class="brand">
            <img src="{{ url_for('static', filename='images/icon-192.png') }}" alt="">
            <h1>Luma</h1>
        </div>
        {% if error %}
        <div class="error{% if rate_limited %} rate-limited{% endif %}" role="alert">{{ error }}</div>
        {% endif %}
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
            <button type="submit">Sign in</button>
        </form>
    </main>
</body>
</html>'''


def _no_store(response):
    """Auth responses must never be cached by the browser or the service worker."""
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    return response


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
    
    if request.method == 'GET':
        # Generate CSRF token
        csrf_token = _generate_csrf_token()
        
        # Show login form
        error = request.args.get('error', '')
        rate_limited = request.args.get('rate_limited') == '1'
        return _login_page(error=error, rate_limited=rate_limited,
                           next_url=safe_next)
    
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
        return redirect(url_for('auth.login', 
                               error=f'Too many attempts. Try again in {mins} min.',
                               rate_limited='1',
                               next=safe_next))
    
    password = request.form.get('password', '')
    remember = request.form.get('remember') == 'on'
    
    if not username or not password:
        return redirect(url_for('auth.login', 
                               error='Username and password required',
                               next=safe_next))
    
    # Authenticate user
    user = user_auth.authenticate(username, password)
    
    if not user:
        login_limiter.record_failure(client_ip, username)
        logger.info(f"[auth] Failed login attempt for user {username} from {client_ip}")
        return redirect(url_for('auth.login', 
                               error='Wrong email, username or password.',
                               next=safe_next))
    
    # Login successful - clear rate limit; the form token is single-use
    login_limiter.record_success(client_ip, username)
    session.pop('csrf_token', None)
    
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
