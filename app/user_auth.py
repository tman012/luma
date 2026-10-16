#!/usr/bin/env python3
"""
User authentication for Luma.

Name + PIN login with server-side session tokens stored in SQLite.
Admin account is seeded on startup only when LUMA_ADMIN_NAME / LUMA_ADMIN_PIN are set.
"""

import sqlite3
import hashlib
import secrets
import os
import json
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
ADMIN_EMAIL = os.environ.get("LUMA_ADMIN_EMAIL") or os.environ.get("ADMIN_EMAIL", "")


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

        # Seed admin account (only when configured via env; never with built-in defaults)
        if not (ADMIN_NAME and ADMIN_PIN):
            logger.info("LUMA_ADMIN_NAME/LUMA_ADMIN_PIN not set; skipping admin seed")
            return
        existing = conn.execute(
            "SELECT id FROM users WHERE name = ? COLLATE NOCASE", (ADMIN_NAME,)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO users (name, pin_hash, is_admin, status, email) VALUES (?, ?, 1, 'active', ?)",
                (ADMIN_NAME, _hash_pin(ADMIN_PIN), ADMIN_EMAIL or None),
            )
            logger.info("Seeded admin user: %s", ADMIN_NAME)
        else:
            # Keep the admin active and its email current. The stored password
            # is never replaced: LUMA_ADMIN_PIN only seeds a new admin or an
            # admin row that has no password at all.
            if ADMIN_EMAIL:
                conn.execute(
                    "UPDATE users SET status = 'active', email = ? WHERE name = ? COLLATE NOCASE",
                    (ADMIN_EMAIL, ADMIN_NAME),
                )
            else:
                conn.execute(
                    "UPDATE users SET status = 'active' WHERE name = ? COLLATE NOCASE",
                    (ADMIN_NAME,),
                )
            conn.execute(
                "UPDATE users SET pin_hash = ? WHERE name = ? COLLATE NOCASE"
                " AND (pin_hash IS NULL OR pin_hash = '')",
                (_hash_pin(ADMIN_PIN), ADMIN_NAME),
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
    """Create a new user. Raises ValueError on duplicate name or email."""
    existing = get_user_by_name(name)
    if existing:
        raise ValueError(f"User '{name}' already exists")
    if email:
        existing_email = get_user_by_email(email)
        if existing_email:
            raise ValueError(f"An account with that email already exists")
    
    pin_hash = generate_password_hash(pin)
    
    with _get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO users (name, pin_hash, is_admin, status, email) VALUES (?, ?, ?, ?, ?)",
            (name.strip(), pin_hash, 1 if is_admin else 0, status, email.strip().lower() if email else None),
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


def update_pin(user_id: int, new_pin: str) -> None:
    """Update a user's PIN."""
    with _get_db() as conn:
        conn.execute(
            "UPDATE users SET pin_hash = ? WHERE id = ?",
            (_hash_pin(new_pin), user_id),
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

def authenticate(name: str, pin: str, email: str = None) -> Optional[Dict]:
    """Verify credentials + PIN. Returns user dict on success, None on failure.
    Tries email lookup first (if provided), then falls back to name lookup.
    Only allows users with status='active'.
    Automatically rehashes legacy SHA-256 passwords to werkzeug format.
    """
    user = find_user(name, email)
    if not user:
        return None
    if user.get("status", "active") != "active":
        return None
    
    pin_hash = user["pin_hash"]
    
    # Check if it's a legacy SHA-256 hash
    if _is_legacy_hash(pin_hash):
        # Compare with legacy hash
        if pin_hash != _hash_pin(pin):
            return None
        # Rehash with werkzeug and update
        new_hash = generate_password_hash(pin)
        with _get_db() as conn:
            conn.execute("UPDATE users SET pin_hash = ? WHERE id = ?", (new_hash, user["id"]))
        logger.info(f"Rehashed legacy password for user {user['name']}")
        user["pin_hash"] = new_hash
        return user
    
    # Modern werkzeug hash
    if not check_password_hash(pin_hash, pin):
        return None
    
    return user


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
