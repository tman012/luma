"""tests.portal_threads: init_portal registers the routes but starts none of
the portal's warm-up threads, which would outlive the test."""
import threading
from unittest.mock import Mock

from flask import Flask

from tests.portal_threads import WARM_THREADS, init_portal


def test_init_portal_starts_no_warm_threads():
    before = {t.ident for t in threading.enumerate() if t.name in WARM_THREADS}
    app = Flask('portal-threads')
    settings = Mock()
    settings.get.return_value = {}
    init_portal(app, settings)
    after = {t.ident for t in threading.enumerate() if t.name in WARM_THREADS}
    assert after <= before
    assert any(r.rule.startswith('/api/request') for r in app.url_map.iter_rules())


def test_other_threads_still_start():
    from app import request_portal
    from tests.portal_threads import no_portal_warm_threads
    hit = threading.Event()
    with no_portal_warm_threads():
        t = request_portal.threading.Thread(target=hit.set, name='other')
        t.start()
        t.join(2)
    assert hit.is_set()
