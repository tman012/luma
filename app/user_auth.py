#!/usr/bin/env python3
"""
User authentication for Luma.

Name + PIN login with server-side session tokens stored in SQLite.
Admin account is seeded on startup only when LUMA_ADMIN_NAME / LUMA_ADMIN_PIN are set.
"""

import sqlite3
import hashlib
import hmac
import secrets
import os
import json
import unicodedata
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)

# Session TTL: 30 days
SESSION_TTL_DAYS = 30

# Seed admin — from env only (no defaults). If LUMA_ADMIN_NAME / LUMA_ADMIN_PIN are unset,
# no admin is seeded (the first-run Start flow creates it). Legacy ADMIN_* names still work.
ADMIN_NAME = os.environ.get("LUMA_ADMIN_NAME") or os.environ.get("ADMIN_NAME", "")
ADMIN_PIN = os.environ.get("LUMA_ADMIN_PIN") or os.environ.get("ADMIN_PIN", "")
ADMIN_EMAIL = (os.environ.get("LUMA_ADMIN_EMAIL") or os.environ.get("ADMIN_EMAIL", "") or "").strip().lower()


def _get_db_path() -> str:
    from app.paths import database_path
    return str(database_path())


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _hash_pin(pin: str) -> str:
    """Hash a PIN with SHA-256 + app-specific salt (legacy, for backward compat detection)."""
    return hashlib.sha256(f"luma_auth_v1_{pin}".encode()).hexdigest()


def _is_legacy_hash(pin_hash: str) -> bool:
    """Check if a hash is legacy SHA-256 format (64 hex chars)."""
    return len(pin_hash) == 64 and all(c in '0123456789abcdef' for c in pin_hash)


# ---------------------------------------------------------------------------
# DB initialisation
# ---------------------------------------------------------------------------

def _migrate_name_key(conn) -> None:
    """Add users.name_key (NFKC + casefold of the name), index it and fill
    it for existing rows. Not UNIQUE: older installs may hold names that only
    collide after normalization, and a failed migration must not lock anyone
    out; new names are checked against it before every write."""
    try:
        conn.execute("ALTER TABLE users ADD COLUMN name_key TEXT")
        logger.info("Migrated users table: added name_key column")
    except Exception:
        pass  # Column already exists
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_name_key ON users(name_key)")
    rows = conn.execute("SELECT id, name FROM users WHERE name_key IS NULL").fetchall()
    for row in rows:
        conn.execute("UPDATE users SET name_key = ? WHERE id = ?", (name_key(row[1] or ''), row[0]))


def init_db() -> None:
    """Create auth tables and seed the admin user."""
    with _get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    UNIQUE NOT NULL COLLATE NOCASE,
                pin_hash    TEXT    NOT NULL,
                is_admin    INTEGER NOT NULL DEFAULT 0,
                status      TEXT    NOT NULL DEFAULT 'active',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS user_sessions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token      TEXT    UNIQUE NOT NULL,
                created_at TEXT    NOT NULL DEFAULT (datetime('now')),
                last_seen  TEXT    NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_token
                ON user_sessions(token);

            CREATE TABLE IF NOT EXISTS user_settings (
                user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                settings_json TEXT NOT NULL DEFAULT '{}'
            );
        """)

        # Migrate existing DBs: add status column if missing
        try:
            conn.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
            logger.info("Migrated users table: added status column")
        except Exception:
            pass  # Column already exists

        # Migrate existing DBs: add email column if missing
        try:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
            logger.info("Migrated users table: added email column")
        except Exception:
            pass  # Column already exists

        _migrate_name_key(conn)

        # Seed admin account (only when configured via env; never with built-in defaults)
        if not (ADMIN_NAME and ADMIN_PIN):
            logger.info("LUMA_ADMIN_NAME/LUMA_ADMIN_PIN not set; skipping admin seed")
            return
        existing = conn.execute(
            "SELECT id FROM users WHERE name = ? COLLATE NOCASE", (ADMIN_NAME,)
        ).fetchone()
        # The admin email is used only if no other account already has it.
        email_owner = conn.execute(
            "SELECT id FROM users WHERE email = ? COLLATE NOCASE AND name <> ? COLLATE NOCASE",
            (ADMIN_EMAIL, ADMIN_NAME),
        ).fetchone() if ADMIN_EMAIL else None
        admin_email = None if email_owner else (ADMIN_EMAIL or None)
        if email_owner:
            logger.warning("LUMA_ADMIN_EMAIL belongs to another account; not assigned to the admin")
        if not existing:
            conn.execute(
                "INSERT INTO users (name, pin_hash, is_admin, status, email, name_key) VALUES (?, ?, 1, 'active', ?, ?)",
                (ADMIN_NAME, generate_password_hash(ADMIN_PIN), admin_email, name_key(ADMIN_NAME)),
            )
            logger.info("Seeded admin user: %s", ADMIN_NAME)
        else:
            # Keep the admin active and its email current. The stored password
            # is never replaced: LUMA_ADMIN_PIN only seeds a new admin or an
            # admin row that has no password at all.
            if admin_email:
                conn.execute(
                    "UPDATE users SET status = 'active', email = ? WHERE name = ? COLLATE NOCASE",
                    (admin_email, ADMIN_NAME),
                )
            else:
                conn.execute(
                    "UPDATE users SET status = 'active' WHERE name = ? COLLATE NOCASE",
                    (ADMIN_NAME,),
                )
            conn.execute(
                "UPDATE users SET pin_hash = ? WHERE name = ? COLLATE NOCASE"
                " AND (pin_hash IS NULL OR pin_hash = '')",
                (generate_password_hash(ADMIN_PIN), ADMIN_NAME),
            )
            logger.debug("Admin user already exists: %s", ADMIN_NAME)


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def get_user_by_name(name: str) -> Optional[Dict]:
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        return dict(row) if row else None


def _script(ch: str) -> str:
    """Script group of a letter, from its Unicode name. Han, kana and Hangul
    are grouped so the usual CJK combinations resolve (UTS #39 style)."""
    try:
        word = unicodedata.name(ch).split(' ')[0]
    except ValueError:
        return 'UNKNOWN'
    if word in ('CJK', 'IDEOGRAPHIC'):
        return 'HAN'
    if word in ('HIRAGANA', 'KATAKANA', 'KATAKANA-HIRAGANA', 'HALFWIDTH'):
        return 'KANA'
    return word


# Script sets a single name may mix (UTS #39 "highly restrictive"): Latin
# with Japanese (Han + kana), Korean (Han + Hangul) or Chinese (Han + Bopomofo).
_ALLOWED_MIXES = (
    frozenset({'LATIN', 'HAN', 'KANA'}),
    frozenset({'LATIN', 'HAN', 'HANGUL'}),
    frozenset({'LATIN', 'HAN', 'BOPOMOFO'}),
)


def normalize_name(name: str) -> str:
    """NFKC-normalized, trimmed form of a username (fullwidth -> ASCII etc.)."""
    return unicodedata.normalize('NFKC', name or '').strip()


def name_key(name: str) -> str:
    """Comparison key for uniqueness: NFKC then casefold."""
    return normalize_name(name).casefold()


def name_problem(name: str) -> Optional[str]:
    """Why a (normalized) username is unusable, or None."""
    for ch in name:
        cat = unicodedata.category(ch)
        if cat in ('Cc', 'Cf', 'Co', 'Cs', 'Cn', 'Zl', 'Zp'):
            return "Name can't contain hidden or control characters"
        if cat == 'Me' or (cat == 'Mn' and unicodedata.combining(ch) == 1):
            return "Name can't contain overlay marks"
    scripts = {_script(ch) for ch in name if ch.isalpha()}
    if len(scripts) > 1 and not any(scripts <= mix for mix in _ALLOWED_MIXES):
        return "Use letters from one alphabet"
    return None


def _name_taken(name: str, exclude_id: int = None) -> bool:
    """True when another account's name or email matches *name* after NFKC
    normalization and casefolding (indexed users.name_key lookup)."""
    key = name_key(name)
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM users WHERE name_key = ? OR lower(email) = ?", (key, key)
        ).fetchall()
        if any(r[0] != exclude_id for r in rows):
            return True
        # Rows written outside this module (e.g. set_credentials) before the
        # next start-up backfill have no key yet.
        legacy = conn.execute("SELECT id, name FROM users WHERE name_key IS NULL").fetchall()
    return any(r[0] != exclude_id and name_key(r[1] or '') == key for r in legacy)


def identity_conflict(name: str, email: str = None, exclude_id: int = None) -> Optional[str]:
    """Why this name/email can't be used, or None. Names compare after NFKC
    normalization and casefolding."""
    name = normalize_name(name)
    email = (email or '').strip()
    if not name:
        return "Name is required"
    if '@' in name:
        return "Name can't contain @"
    problem = name_problem(name)
    if problem:
        return problem

    def _taken(user):
        return user is not None and user.get('id') != exclude_id

    if _name_taken(name, exclude_id) or _taken(get_user_by_name(name)) or _taken(get_user_by_email(name)):
        return "An account with that name already exists"
    if email and (_taken(get_user_by_email(email)) or _taken(get_user_by_name(email))):
        return "An account with that email already exists"
    return None


def find_user(identifier: str, email: str = None) -> Optional[Dict]:
    """Look up a user by email or name, case-insensitive.

    An identifier containing '@' is tried as an email first; anything else is
    tried as a name first. Either way the other lookup is the fallback.
    """
    identifier = (identifier or '').strip()
    email = (email or '').strip()
    if email:
        user = get_user_by_email(email)
        if user:
            return user
    if not identifier:
        return None
    if '@' in identifier:
        return get_user_by_email(identifier) or get_user_by_name(identifier)
    return get_user_by_name(identifier) or get_user_by_email(identifier)


def get_user_by_email(email: str) -> Optional[Dict]:
    """Look up a user by email address (case-insensitive)."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[Dict]:
    with _get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def list_users() -> List[Dict]:
    """Return all users (without pin_hash)."""
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, email, is_admin, status, created_at FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def create_user(name: str, pin: str, is_admin: bool = False, status: str = 'active',
                email: str = None) -> Dict:
    """Create a new user. Raises ValueError on a bad or duplicate name or email.

    Names and emails share one namespace for sign-in, so a new name may not
    match any existing email and a new email may not match any existing name.
    """
    name = normalize_name(name)
    email = (email or '').strip().lower() or None
    problem = identity_conflict(name, email)
    if problem:
        raise ValueError(problem)
    
    pin_hash = generate_password_hash(pin)
    
    with _get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO users (name, pin_hash, is_admin, status, email, name_key) VALUES (?, ?, ?, ?, ?, ?)",
            (name.strip(), pin_hash, 1 if is_admin else 0, status, email.strip().lower() if email else None,
             name_key(name)),
        )
        user_id = cursor.lastrowid
    return {"id": user_id, "name": name.strip(), "email": email.strip().lower() if email else None,
            "is_admin": is_admin, "status": status}


def set_user_status(user_id: int, status: str) -> None:
    """Set a user's status (active/pending/denied)."""
    with _get_db() as conn:
        conn.execute(
            "UPDATE users SET status = ? WHERE id = ?",
            (status, user_id),
        )


def approve_user(user_id: int) -> bool:
    """Approve a pending user. Returns False if not found."""
    user = get_user_by_id(user_id)
    if not user:
        return False
    set_user_status(user_id, 'active')
    return True


def deny_user(user_id: int) -> bool:
    """Deny and delete a pending user. Returns False if not found."""
    user = get_user_by_id(user_id)
    if not user:
        return False
    with _get_db() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return True


PASSWORD_SPACE_ERROR = "Password can't start or end with a space"


def password_space_problem(password: str) -> str:
    """The error for a password with leading or trailing whitespace, else ''.
    Passwords are stored and checked exactly as typed, never stripped."""
    if isinstance(password, str) and password and password != password.strip():
        return PASSWORD_SPACE_ERROR
    return ''


def set_name_and_password(user_id: int, name: str, password: str) -> None:
    """Rename a user and set their password in one transaction.
    Raises ValueError when the name is unusable or taken."""
    name = normalize_name(name)
    problem = identity_conflict(name, exclude_id=user_id)
    if problem:
        raise ValueError(problem)
    pin_hash = generate_password_hash(password)
    with _get_db() as conn:
        conn.execute("UPDATE users SET name = ?, name_key = ?, pin_hash = ? WHERE id = ?",
                     (name, name_key(name), pin_hash, user_id))


def update_pin(user_id: int, new_pin: str) -> None:
    """Update a user's PIN."""
    with _get_db() as conn:
        conn.execute(
            "UPDATE users SET pin_hash = ? WHERE id = ?",
            (generate_password_hash(new_pin), user_id),
        )


def delete_user(user_id: int) -> bool:
    """Delete a user. Returns False if the user is the sole admin."""
    user = get_user_by_id(user_id)
    if not user:
        return False
    # Prevent deleting the last admin
    if user["is_admin"]:
        with _get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE is_admin = 1"
            ).fetchone()[0]
            if count <= 1:
                raise ValueError("Cannot delete the last admin account")
    with _get_db() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return True


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def authenticate(name: str, pin: str, email: str = None,
                 any_status: bool = False) -> Optional[Dict]:
    """Verify credentials. Returns the user dict on success, None on failure.

    The user is found by email or name (see find_user). The password is always
    checked before the account status, and an unknown user still costs one
    hash, so timing does not reveal which accounts exist. Only status='active'
    users pass unless any_status is set (the caller then reports the status).
    A legacy SHA-256 hash is upgraded only after it matched the typed password.
    """
    user = find_user(name, email)
    if not user or not user.get("pin_hash"):
        check_password_hash(_dummy_hash(), pin or "")
        return None

    pin_hash = user["pin_hash"]
    if _is_legacy_hash(pin_hash):
        if not hmac.compare_digest(pin_hash, _hash_pin(pin)):
            return None
        new_hash = generate_password_hash(pin)
        with _get_db() as conn:
            conn.execute("UPDATE users SET pin_hash = ? WHERE id = ?", (new_hash, user["id"]))
        logger.info(f"Rehashed legacy password for user {user['name']}")
        user["pin_hash"] = new_hash
    elif not check_password_hash(pin_hash, pin):
        return None

    if not any_status and user.get("status", "active") != "active":
        return None
    return user


_DUMMY_HASH = None


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(16))
    return _DUMMY_HASH


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def create_session(user_id: int) -> str:
    """Create a 30-day session token. Returns the token string."""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat()
    with _get_db() as conn:
        conn.execute(
            "INSERT INTO user_sessions (user_id, token, expires_at) VALUES (?, ?, ?)",
            (user_id, token, expires_at),
        )
    return token


def validate_session(token: str) -> Optional[Dict]:
    """Validate a session token. Returns user dict if valid."""
    if not token:
        return None
    with _get_db() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.name, u.is_admin, u.created_at,
                   s.token, s.expires_at
            FROM   user_sessions s
            JOIN   users u ON s.user_id = u.id
            WHERE  s.token = ?
              AND  s.expires_at > datetime('now')
            """,
            (token,),
        ).fetchone()
        if not row:
            return None
        # Touch last_seen
        conn.execute(
            "UPDATE user_sessions SET last_seen = datetime('now') WHERE token = ?",
            (token,),
        )
        return dict(row)


def delete_session(token: str) -> None:
    """Invalidate a session (logout)."""
    with _get_db() as conn:
        conn.execute("DELETE FROM user_sessions WHERE token = ?", (token,))


def delete_all_sessions(user_id: int) -> None:
    """Invalidate all sessions for a user."""
    with _get_db() as conn:
        conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))


# ---------------------------------------------------------------------------
# Per-user settings
# ---------------------------------------------------------------------------

def get_user_settings(user_id: int) -> Dict:
    """Return user-specific settings dict (plex_url, plex_token, etc.)."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT settings_json FROM user_settings WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return {}
        return json.loads(row["settings_json"] or "{}")


def save_user_settings(user_id: int, new_settings: Dict) -> None:
    """Merge and persist user-specific settings."""
    current = get_user_settings(user_id)
    current.update(new_settings)
    with _get_db() as conn:
        conn.execute(
            """
            INSERT INTO user_settings (user_id, settings_json)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET settings_json = excluded.settings_json
            """,
            (user_id, json.dumps(current)),
        )


def is_plex_approved(user: Optional[Dict]) -> bool:
    """Admins (including the LAN implicit owner and the service key), or an
    active user whose shared Plex access was approved and not denied."""
    if not user:
        return False
    if user.get("is_admin"):
        return True
    full = get_user_by_id(user["id"]) or {}
    if full.get("status", "active") != "active":
        return False
    us = get_user_settings(user["id"]) or {}
    if us.get("plex_access_status") == "denied":
        return False
    return us.get("use_shared_server") is True or us.get("plex_access_status") == "approved"
