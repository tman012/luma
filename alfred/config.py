"""Alfred API configuration — loaded from environment variables."""

import os

# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
PORT = int(os.getenv('ALFRED_PORT', '8990'))
API_KEY = os.getenv('ALFRED_API_KEY', '')  # optional bearer token

# ---------------------------------------------------------------------------
# Anthropic API (for fallback / complex commands)
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')

# ---------------------------------------------------------------------------
# qBittorrent
# ---------------------------------------------------------------------------
QBIT_URL = os.getenv('QBIT_URL', 'http://localhost:8080')
QBIT_USER = os.getenv('QBIT_USER', '')
QBIT_PASS = os.getenv('QBIT_PASS', '')

# ---------------------------------------------------------------------------
# Radarr
# ---------------------------------------------------------------------------
RADARR_URL = os.getenv('RADARR_URL', 'http://localhost:7878')
RADARR_API_KEY = os.getenv('RADARR_API_KEY', '')

# ---------------------------------------------------------------------------
# Sonarr
# ---------------------------------------------------------------------------
SONARR_URL = os.getenv('SONARR_URL', 'http://localhost:8989')
SONARR_API_KEY = os.getenv('SONARR_API_KEY', '')

# ---------------------------------------------------------------------------
# Plex
# ---------------------------------------------------------------------------
PLEX_URL = os.getenv('PLEX_URL', 'http://192.168.1.25:32400')
PLEX_TOKEN = os.getenv('PLEX_TOKEN', '')

# ---------------------------------------------------------------------------
# Home Assistant (direct REST API — bypasses LLM for speed)
# ---------------------------------------------------------------------------
HA_URL = os.getenv('HA_URL', 'http://192.168.1.162:8123')
HA_TOKEN = os.getenv('HA_TOKEN', '')

# ---------------------------------------------------------------------------
# Storage paths to check
# ---------------------------------------------------------------------------
STORAGE_PATHS = os.getenv('STORAGE_PATHS', '/,/storage,/mnt/media').split(',')
