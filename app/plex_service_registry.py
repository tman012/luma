"""Per-user PlexService instance registry with TTL-based eviction."""
import threading
import time
from app.plex_service import PlexService

_lock = threading.Lock()
_instances: dict[int, tuple[PlexService, float]] = {}  # user_id -> (instance, last_accessed)
_MAX_AGE = 3600  # 1 hour idle eviction


def get_user_plex(user_id: int, plex_url: str, plex_token: str) -> PlexService:
    """Get or create a PlexService for a specific user."""
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
            if svc.plex_url.rstrip('/') == plex_url.rstrip('/'):
                _instances[user_id] = (svc, now)
                return svc

        # Create new
        svc = PlexService(plex_url, plex_token)
        _instances[user_id] = (svc, now)
        return svc


def invalidate_user(user_id: int) -> None:
    """Remove cached PlexService for a user (e.g., when they change settings)."""
    with _lock:
        _instances.pop(user_id, None)
