"""request_portal.init_app without its warm-up threads, for tests.

init_app starts two daemon threads (request-index-warm, request-recent-warm)
that sleep, then refresh TMDB discover lists in a loop for the rest of the
session. Started from a test with portal settings that read as enabled, they
call requests.get from the background while later tests have patched it,
and those tests record calls that aren't theirs (test_minimal_search,
test_quickfix_navy_cache_img, depending on order). Tests that only need the
routes registered use this instead.
"""
from contextlib import contextmanager
from unittest.mock import patch

WARM_THREADS = frozenset({'request-index-warm', 'request-recent-warm'})


@contextmanager
def no_portal_warm_threads():
    from app import request_portal
    real = request_portal.threading

    class _Thread(real.Thread):
        def start(self):
            if self.name in WARM_THREADS:
                return
            super().start()

    class _Threading:
        Thread = _Thread

        def __getattr__(self, name):
            return getattr(real, name)

    with patch.object(request_portal, 'threading', _Threading()):
        yield


def init_portal(app, settings, **kw):
    """request_portal.init_app(app, settings) with no warm-up threads."""
    from app import request_portal
    with no_portal_warm_threads():
        request_portal.init_app(app, settings, **kw)
