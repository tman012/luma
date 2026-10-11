"""Per-user SonarrAPI instance registry with TTL-based eviction."""
import threading
import time
from app.sonarr_api import SonarrAPI

_lock = threading.Lock()
_instances: dict[int, tuple[SonarrAPI, float]] = {}  # user_id -> (instance, last_accessed)
_MAX_AGE = 3600  # 1 hour idle eviction


def get_user_sonarr(user_id: int, sonarr_url: str, sonarr_api_key: str) -> SonarrAPI:
    """Get or create a SonarrAPI for a specific user."""
    now = time.time()
    with _lock:
        # Evict stale entries
        stale = [uid for uid, (_, ts) in _instances.items() if now - ts > _MAX_AGE]
        for uid in stale:
            del _instances[uid]

        # Check cache
        if user_id in _instances:
            svc, _ = _instances[user_id]
            # Verify URL hasn't changed
            if svc.url.rstrip('/') == sonarr_url.rstrip('/'):
                _instances[user_id] = (svc, now)
                return svc

        # Create new
        svc = SonarrAPI(sonarr_url, sonarr_api_key)
        _instances[user_id] = (svc, now)
        return svc


def invalidate_user(user_id: int) -> None:
    """Remove cached SonarrAPI for a user (e.g., when they change settings)."""
    with _lock:
        _instances.pop(user_id, None)
