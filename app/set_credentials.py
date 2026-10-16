#!/usr/bin/env python3
"""
CLI tool to set owner credentials for remote access.

Usage:
    python -m app.set_credentials <username>
    (prompts for password securely)
    
Or via stdin:
    echo "secretpass" | python -m app.set_credentials <username>
    
Or via environment (hashed password):
    LUMA_ADMIN_USER=admin LUMA_ADMIN_PASSWORD_HASH='pbkdf2:sha256:...' python -m app.set_credentials
"""

import sys
import os
import getpass
from werkzeug.security import generate_password_hash, check_password_hash
import app.user_auth as user_auth


def set_credentials(username: str, password_or_hash: str, is_hash: bool = False):
    """Set or update owner credentials."""
    # Initialize database
    user_auth.init_db()
    
    # If not already hashed, hash it
    if is_hash:
        password_hash = password_or_hash
        # Verify it's a valid hash
        try:
            check_password_hash(password_hash, 'test')
        except Exception:
            pass  # Hash format is valid even if check fails
    else:
        password_hash = generate_password_hash(password_or_hash)
    
    # Check if user exists
    existing = user_auth.get_user_by_name(username)
    
    if existing:
        # Update existing user by directly setting hash
        conn = user_auth._get_db()
        conn.execute('UPDATE users SET pin_hash = ? WHERE id = ?', (password_hash, existing['id']))
        conn.commit()
        print(f"Updated credentials for user: {username}")
    else:
        # Create new admin user with hash
        conn = user_auth._get_db()
        conn.execute(
            'INSERT INTO users (name, pin_hash, is_admin, status) VALUES (?, ?, 1, ?)',
            (username, password_hash, 'active')
        )
        conn.commit()
        print(f"Created admin user: {username}")
    
    print("\nCredentials set successfully!")
    print("You can now enable remote access in Settings > Security")


def main():
    # Try environment variables first
    username = os.environ.get('LUMA_ADMIN_USER', '').strip()
    password_hash = os.environ.get('LUMA_ADMIN_PASSWORD_HASH', '').strip()
    
    # DEPRECATED: Support plaintext env var with warning
    plaintext_password = os.environ.get('LUMA_ADMIN_PASSWORD', '').strip()
    if plaintext_password and not password_hash:
        print("WARNING: LUMA_ADMIN_PASSWORD is deprecated. Use LUMA_ADMIN_PASSWORD_HASH instead.", file=sys.stderr)
        print("         Hashing password at startup...", file=sys.stderr)
        password_hash = generate_password_hash(plaintext_password)
    
    # Try command line args if env vars not set (username only, never password)
    if not username and len(sys.argv) >= 2:
        username = sys.argv[1]
    
    # Interactive prompts if still missing
    if not username:
        username = input("Username: ").strip()
    
    if not password_hash:
        # Try reading from stdin (non-interactive)
        if not sys.stdin.isatty():
            password = sys.stdin.read().strip()
        else:
            # Interactive secure prompt
            password = getpass.getpass("Password: ").strip()
            password_confirm = getpass.getpass("Confirm password: ").strip()
            
            if password != password_confirm:
                print("ERROR: Passwords do not match", file=sys.stderr)
                sys.exit(1)
        
        if not password:
            print("ERROR: Password required", file=sys.stderr)
            sys.exit(1)
        
        password_hash = generate_password_hash(password)
    
    if not username:
        print("ERROR: Username required", file=sys.stderr)
        sys.exit(1)
    
    set_credentials(username, password_hash, is_hash=True)


if __name__ == '__main__':
    main()
