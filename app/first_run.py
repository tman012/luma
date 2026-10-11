"""First-run bootstrap: install secrets kept in the data dir, no env needed.

``data/secrets.json`` (mode 600) holds a generated Flask secret key, the
service API key and the Plex client identifier for this install. Values set
in the environment always win, so existing installs keep their keys.

``export_settings_to_env()`` copies a few values saved in settings.json into
the process environment when the environment does not set them, so modules
that read their configuration from the environment at import time (TMDB key,
Plex URL/token) also work on an install configured only through the UI.
"""

import json
import os
import secrets
import uuid
from pathlib import Path
from typing import Dict


def data_dir() -> Path:
    override = os.environ.get('LUMA_STATE_DIR', '')
    if override:
        return Path(override)
    d = Path('/app/data')
    if d.is_dir():
        return d
    db = os.environ.get('DATABASE_PATH', '')
    if db:
        return Path(db).parent
    return Path(__file__).resolve().parent.parent / 'data'


def _secrets_path() -> Path:
    return data_dir() / 'secrets.json'


def load_or_create_secrets() -> Dict[str, str]:
    """Return the install secrets, generating any that are missing."""
    path = _secrets_path()
    data: Dict[str, str] = {}
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        data = {}
    changed = False
    for key, make in (
        ('flask_secret_key', lambda: secrets.token_hex(32)),
        ('service_api_key', lambda: secrets.token_urlsafe(32)),
        ('plex_client_id', lambda: f'luma-{uuid.uuid4()}'),
    ):
        if not data.get(key):
            data[key] = make()
            changed = True
    if changed:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix('.tmp')
            tmp.write_text(json.dumps(data, indent=2))
            os.chmod(tmp, 0o600)
            tmp.replace(path)
        except OSError:
            pass  # read-only data dir: keys live for this process only
    return data


_SETTINGS_ENV = {
    'TMDB_API_KEY': ('apis', 'tmdb', 'api_key'),
    'OMDB_API_KEY': ('apis', 'omdb', 'api_key'),
    'PLEX_URL': ('plex', 'url'),
    'PLEX_TOKEN': ('plex', 'token'),
    'PLEX_MACHINE_ID': ('plex', 'machine_id'),
}


def export_settings_to_env() -> None:
    """Fill unset env vars from settings.json (never overrides the env)."""
    try:
        data = json.loads((data_dir() / 'settings.json').read_text())
    except (FileNotFoundError, ValueError):
        return
    for env, path in _SETTINGS_ENV.items():
        if os.environ.get(env):
            continue
        cur = data
        for k in path:
            cur = cur.get(k) if isinstance(cur, dict) else None
        if isinstance(cur, str) and cur.strip():
            os.environ[env] = cur.strip()
