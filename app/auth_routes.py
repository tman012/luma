"""Authentication routes for forms-based login."""

import logging
import time
import secrets
from collections import defaultdict
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, make_response, redirect, url_for, render_template_string, session
from werkzeug.security import check_password_hash
import app.user_auth as user_auth
import app.access as access
from app.server_config import get_url_base

logger = logging.getLogger(__name__)

bp = Blueprint('auth', __name__)

# Rate limiting: track failed attempts per IP
_failed_attempts = defaultdict(list)  # IP -> [timestamp, ...]
_RATE_LIMIT_WINDOW = 300  # 5 minutes
_RATE_LIMIT_MAX = 5  # Max attempts in window

# Session cookie name
AUTH_COOKIE = 'luma_auth_session'


def _is_rate_limited(ip: str) -> bool:
    """Check if IP is rate limited."""
    now = time.time()
    cutoff = now - _RATE_LIMIT_WINDOW
    
    # Clean old attempts
    _failed_attempts[ip] = [t for t in _failed_attempts[ip] if t > cutoff]
    
    return len(_failed_attempts[ip]) >= _RATE_LIMIT_MAX


def _record_failed_attempt(ip: str):
    """Record a failed login attempt."""
    _failed_attempts[ip].append(time.time())


def _get_client_ip(request, settings) -> str:
    """Get client IP using trusted-proxy-aware access.client_ip()."""
    ip = access.client_ip(request, settings)
    return ip if ip else request.remote_addr or '0.0.0.0'


def _is_safe_redirect(url: str, url_base: str) -> bool:
    """Check if a redirect URL is safe (no open redirect).
    
    Only allows paths that:
    - Start with exactly one '/' (not '//' or '/\')
    - Don't contain a scheme (http:, https:, etc.)
    - Will be prefixed with url_base
    """
    if not url:
        return False
    
    # Reject anything with a scheme
    if '://' in url or url.startswith('//') or url.startswith('/\\'):
        return False
    
    # Must start with single /
    if not url.startswith('/'):
        return False
    
    # Check for scheme-like patterns
    if ':' in url.split('/')[0]:
        return False
    
    return True


def _safe_redirect_url(next_param: str, default: str = '/') -> str:
    """Return a safe redirect URL, applying URL base prefix.
    
    If next_param is unsafe, returns default with URL base.
    """
    url_base = get_url_base() or ''
    
    if not _is_safe_redirect(next_param, url_base):
        return url_base + default if url_base else default
    
    # Apply URL base prefix if not already there
    if url_base and not next_param.startswith(url_base):
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


# Simple HTML login page
LOGIN_PAGE_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sign In - Luma</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Google+Sans+Flex:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Google Sans Flex', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #111318;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            color: #e8eaed;
        }
        .login-container {
            background: #282a2f;
            border-radius: 16px;
            padding: 40px;
            width: 100%;
            max-width: 400px;
        }
        h1 {
            font-size: 32px;
            font-weight: 500;
            color: #e8eaed;
            margin-bottom: 32px;
            text-align: center;
            letter-spacing: -0.5px;
        }
        .form-group {
            margin-bottom: 24px;
        }
        label {
            display: block;
            font-size: 14px;
            font-weight: 500;
            color: #bdc1c6;
            margin-bottom: 8px;
        }
        input[type="text"],
        input[type="password"] {
            width: 100%;
            padding: 14px 16px;
            background: #111318;
            border: 1px solid #3c4043;
            border-radius: 8px;
            font-size: 16px;
            color: #e8eaed;
            font-family: 'Google Sans Flex', sans-serif;
            transition: border-color 0.2s, background 0.2s;
        }
        input:focus {
            outline: none;
            border-color: #adc7ff;
            background: #1a1d23;
        }
        input::placeholder {
            color: #5f6368;
        }
        .remember-me {
            display: flex;
            align-items: center;
            margin-bottom: 32px;
        }
        .remember-me input[type="checkbox"] {
            width: 20px;
            height: 20px;
            margin-right: 12px;
            cursor: pointer;
            accent-color: #adc7ff;
        }
        .remember-me label {
            margin: 0;
            font-weight: 400;
            font-size: 15px;
            color: #bdc1c6;
            cursor: pointer;
        }
        button[type="submit"] {
            width: 100%;
            padding: 14px;
            background: #adc7ff;
            color: #111318;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 500;
            cursor: pointer;
            font-family: 'Google Sans Flex', sans-serif;
            transition: background 0.2s, transform 0.1s;
        }
        button[type="submit"]:hover {
            background: #c4d7ff;
        }
        button[type="submit"]:active {
            transform: scale(0.98);
        }
        .error {
            background: #5c2626;
            color: #f28b82;
            padding: 14px 16px;
            border-radius: 8px;
            margin-bottom: 24px;
            font-size: 14px;
            border-left: 4px solid #f28b82;
        }
        .rate-limit-error {
            background: #5c4526;
            color: #fdd663;
            border-left-color: #fdd663;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <h1>Luma</h1>
        
        {% if error %}
        <div class="error {% if rate_limited %}rate-limit-error{% endif %}">
            {{ error }}
        </div>
        {% endif %}
        
        <form method="POST" action="{{ url_for('auth.login', next=next_url) }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            
            <div class="form-group">
                <label for="username">Username</label>
                <input type="text" id="username" name="username" required autofocus>
            </div>
            
            <div class="form-group">
                <label for="password">Password</label>
                <input type="password" id="password" name="password" required>
            </div>
            
            <div class="remember-me">
                <input type="checkbox" id="remember" name="remember">
                <label for="remember">Remember me for 30 days</label>
            </div>
            
            <button type="submit">Sign In</button>
        </form>
    </div>
</body>
</html>'''


@bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page and authentication endpoint."""
    from app.settings_manager import SettingsManager
    settings = SettingsManager()
    
    next_param = request.args.get('next', '/')
    safe_next = _safe_redirect_url(next_param)
    
    if request.method == 'GET':
        # Generate CSRF token
        csrf_token = _generate_csrf_token()
        
        # Show login form
        error = request.args.get('error', '')
        rate_limited = request.args.get('rate_limited') == '1'
        return render_template_string(LOGIN_PAGE_HTML, 
                                     error=error, 
                                     rate_limited=rate_limited,
                                     next_url=safe_next,
                                     csrf_token=csrf_token)
    
    # POST - process login
    # Validate CSRF token
    csrf_token = request.form.get('csrf_token', '')
    if not _validate_csrf_token(csrf_token):
        logger.warning("[auth] CSRF token validation failed")
        return redirect(url_for('auth.login', 
                               error='Invalid request. Please try again.',
                               next=safe_next))  # Use safe_next, not raw next_param
    
    client_ip = _get_client_ip(request, settings)
    
    # Check rate limit
    if _is_rate_limited(client_ip):
        logger.warning(f"[auth] Rate limit exceeded for IP {client_ip}")
        return redirect(url_for('auth.login', 
                               error='Too many failed attempts. Try again in 5 minutes.',
                               rate_limited='1',
                               next=safe_next))  # Use safe_next
    
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    remember = request.form.get('remember') == 'on'
    
    if not username or not password:
        return redirect(url_for('auth.login', 
                               error='Username and password required',
                               next=safe_next))  # Use safe_next
    
    # Authenticate user
    user = user_auth.authenticate(username, password)
    
    if not user:
        _record_failed_attempt(client_ip)
        logger.info(f"[auth] Failed login attempt for user {username} from {client_ip}")
        return redirect(url_for('auth.login', 
                               error='Invalid username or password',
                               next=safe_next))  # Use safe_next
    
    # Create session
    token = user_auth.create_session(user['id'])
    
    # Set cookie and redirect to safe URL
    response = make_response(redirect(safe_next))
    
    # Calculate expiry (30 days if remember, else session cookie)
    if remember:
        max_age = 30 * 24 * 60 * 60  # 30 days in seconds
    else:
        max_age = None  # Session cookie
    
    # Secure flag if HTTPS
    secure = request.is_secure
    
    # Cookie path respects URL base
    url_base = get_url_base() or '/'
    
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
    # Check if request came from same origin (CSRF protection)
    origin = request.headers.get('Origin')
    referer = request.headers.get('Referer')
    host = request.headers.get('Host')
    
    # Build expected origin
    scheme = 'https' if request.is_secure else 'http'
    expected_origin = f"{scheme}://{host}"
    
    # Validate origin or referer matches
    valid_request = False
    if origin and origin.startswith(expected_origin):
        valid_request = True
    elif referer and referer.startswith(expected_origin):
        valid_request = True
    
    if not valid_request:
        logger.warning(f"[auth] Logout CSRF check failed: origin={origin}, referer={referer}, host={host}")
        return jsonify({"error": "Invalid request"}), 403
    
    token = request.cookies.get(AUTH_COOKIE)
    
    if token:
        user_auth.delete_session(token)
    
    url_base = get_url_base() or '/'
    login_url = (url_base.rstrip('/') + '/login') if url_base != '/' else '/login'
    
    response = make_response(redirect(login_url))
    response.delete_cookie(AUTH_COOKIE, path=url_base)
    
    return response


def init_app(app):
    """Register auth blueprint."""
    app.register_blueprint(bp)
