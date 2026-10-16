#!/usr/bin/env python3
"""
CLI tool to set owner credentials for remote access.

Usage:
    python -m app.set_credentials <username> <password>
    
Or via environment:
    LUMA_ADMIN_USER=admin LUMA_ADMIN_PASSWORD=secretpass python -m app.set_credentials
"""

import sys
import os
import getpass
from werkzeug.security import generate_password_hash
import app.user_auth as user_auth


def set_credentials(username: str, password: str):
    """Set or update owner credentials."""
    # Initialize database
    user_auth.init_db()
    
    # Check if user exists
    existing = user_auth.get_user_by_name(username)
    
    if existing:
        # Update existing user
        user_auth.update_pin(existing['id'], password)
        print(f"Updated credentials for user: {username}")
    else:
        # Create new admin user
        user_auth.create_user(username, password, is_admin=True)
        print(f"Created admin user: {username}")
    
    print("\nCredentials set successfully!")
    print("You can now enable remote access in Settings > Security")


def main():
    # Try environment variables first
    username = os.environ.get('LUMA_ADMIN_USER', '').strip()
    password = os.environ.get('LUMA_ADMIN_PASSWORD', '').strip()
    
    # Try command line args if env vars not set
    if not username and len(sys.argv) >= 2:
        username = sys.argv[1]
    if not password and len(sys.argv) >= 3:
        password = sys.argv[2]
    
    # Interactive prompts if still missing
    if not username:
        username = input("Username: ").strip()
    
    if not password:
        password = getpass.getpass("Password: ").strip()
        password_confirm = getpass.getpass("Confirm password: ").strip()
        
        if password != password_confirm:
            print("ERROR: Passwords do not match", file=sys.stderr)
            sys.exit(1)
    
    if not username or not password:
        print("ERROR: Username and password required", file=sys.stderr)
        sys.exit(1)
    
    set_credentials(username, password)


if __name__ == '__main__':
    main()
