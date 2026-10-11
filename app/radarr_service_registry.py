"""Per-user RadarrAPI instance registry with TTL-based eviction."""
import threading
import time
from app.radarr_api import RadarrAPI

_lock = threading.Lock()
_instances: dict[int, tuple[RadarrAPI, float]] = {}  # user_id -> (instance, last_accessed)
_MAX_AGE = 3600  # 1 hour idle eviction


def get_user_radarr(user_id: int, radarr_url: str, radarr_api_key: str) -> RadarrAPI:
    """Get or create a RadarrAPI for a specific user."""
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
            if svc.url.rstrip('/') == radarr_url.rstrip('/'):
                _instances[user_id] = (svc, now)
                return svc

        # Create new
        svc = RadarrAPI(radarr_url, radarr_api_key)
        _instances[user_id] = (svc, now)
        return svc


def invalidate_user(user_id: int) -> None:
    """Remove cached RadarrAPI for a user (e.g., when they change settings)."""
    with _lock:
        _instances.pop(user_id, None)
