"""Authentication routes for forms-based login."""

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, make_response, redirect, url_for, render_template_string
from werkzeug.security import check_password_hash
import app.user_auth as user_auth
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


def _get_client_ip() -> str:
    """Get client IP from request."""
    # Use X-Forwarded-For if available, otherwise remote_addr
    forwarded = request.headers.get('X-Forwarded-For', '').split(',')
    if forwarded and forwarded[0].strip():
        return forwarded[0].strip()
    return request.remote_addr or '0.0.0.0'


# Simple HTML login page
LOGIN_PAGE_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sign In - Luma</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .login-container {
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 40px;
            width: 100%;
            max-width: 400px;
        }
        h1 {
            font-size: 28px;
            font-weight: 600;
            color: #1a202c;
            margin-bottom: 8px;
            text-align: center;
        }
        .subtitle {
            color: #718096;
            text-align: center;
            margin-bottom: 32px;
            font-size: 14px;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            font-size: 14px;
            font-weight: 500;
            color: #4a5568;
            margin-bottom: 8px;
        }
        input[type="text"],
        input[type="password"] {
            width: 100%;
            padding: 12px 16px;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            font-size: 15px;
            transition: all 0.2s;
        }
        input:focus {
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        .remember-me {
            display: flex;
            align-items: center;
            margin-bottom: 24px;
        }
        .remember-me input[type="checkbox"] {
            width: 18px;
            height: 18px;
            margin-right: 8px;
            cursor: pointer;
        }
        .remember-me label {
            margin: 0;
            font-weight: 400;
            cursor: pointer;
        }
        button[type="submit"] {
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.1s, box-shadow 0.2s;
        }
        button[type="submit"]:hover {
            transform: translateY(-1px);
            box-shadow: 0 8px 16px rgba(102, 126, 234, 0.3);
        }
        button[type="submit"]:active {
            transform: translateY(0);
        }
        .error {
            background: #fed7d7;
            color: #c53030;
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 14px;
            border-left: 4px solid #f56565;
        }
        .rate-limit-error {
            background: #feebc8;
            color: #c05621;
            border-left-color: #ed8936;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <h1>Luma</h1>
        <p class="subtitle">Sign in to continue</p>
        
        {% if error %}
        <div class="error {% if rate_limited %}rate-limit-error{% endif %}">
            {{ error }}
        </div>
        {% endif %}
        
        <form method="POST" action="{{ url_for('auth.login', next=next_url) }}">
            <div class="form-group">
                <label for="username">Username</label>
                <input type="text" id="username" name="username" required autofocus>
            </div>
            
            <div class="form-group">
                <label for="password">Password</label>
                <input type="password" id="password" name="password" required>
            </div>
            
            <div class="remember-me">
                <input type="checkbox" id="remember" name="remember" checked>
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
    next_url = request.args.get('next', '/')
    
    if request.method == 'GET':
        # Show login form
        error = request.args.get('error', '')
        rate_limited = request.args.get('rate_limited') == '1'
        return render_template_string(LOGIN_PAGE_HTML, 
                                     error=error, 
                                     rate_limited=rate_limited,
                                     next_url=next_url)
    
    # POST - process login
    client_ip = _get_client_ip()
    
    # Check rate limit
    if _is_rate_limited(client_ip):
        logger.warning(f"[auth] Rate limit exceeded for IP {client_ip}")
        return redirect(url_for('auth.login', 
                               error='Too many failed attempts. Try again in 5 minutes.',
                               rate_limited='1',
                               next=next_url))
    
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    remember = request.form.get('remember') == 'on'
    
    if not username or not password:
        return redirect(url_for('auth.login', 
                               error='Username and password required',
                               next=next_url))
    
    # Authenticate user
    user = user_auth.authenticate(username, password)
    
    if not user:
        _record_failed_attempt(client_ip)
        logger.info(f"[auth] Failed login attempt for user {username} from {client_ip}")
        return redirect(url_for('auth.login', 
                               error='Invalid username or password',
                               next=next_url))
    
    # Create session
    token = user_auth.create_session(user['id'])
    
    # Set cookie
    response = make_response(redirect(next_url))
    
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


@bp.route('/logout', methods=['POST', 'GET'])
def logout():
    """Logout endpoint - deletes session and cookie."""
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
