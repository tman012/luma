"""v169 (Tester, 3e0df8a): a valid release token whose target Luma refuses (an internal IP, the metadata
IP 169.254.169.254, a file: URL, another site) is a 4xx with no fallback, and costs nothing: no request,
no budget charge. Fake trackers only."""
import time

import pytest

from app import tracker_sources, tv_grab
from app.tracker_guard import guard_key
from tests.test_tracker_search import env, torznab  # noqa: F401
from tests.test_tv_v158c import BODY, _post, known_token, qenv  # noqa: F401
from tests.test_file_management import minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import lib  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

REFUSED = [
    'http://127.0.0.1:7928/api/v2/torrents/delete?hashes=all&deleteFiles=true',
    'http://169.254.169.254/latest/meta-data/',
    'file:///etc/passwd',
    'http://10.0.0.9/download/1.torrent',
    'http://[::1]/x.torrent',
    'gopher://t.example/x',
    'magnet:?xt=urn:btih:' + 'a' * 40 + '\nhttp://127.0.0.1/',
]


@pytest.mark.parametrize('target', REFUSED)
def test_a_refused_target_is_a_400_with_nothing_fetched_or_charged(env, target):  # noqa: F811
    rec = torznab(env['store'])
    tok = env['engine'].tokens.issue(rec['id'], target)
    with pytest.raises(tracker_sources.GrabError) as e:
        tracker_sources.fetch_release(tok)
    assert e.value.status == 400
    assert env['mock'].calls == []                                      # nothing fetched
    cfg = env['store'].config(env['store'].get(rec['id']))
    assert env['guard'].counts(guard_key(cfg), time.time()) == (0, 0, 0)   # nothing charged


def _route_with(monkeypatch, err):
    real = tv_grab.grab

    def fake(it, sid, season, ids, origin, now=None, **k):
        def fetch(u):
            raise err
        return real(it, sid, season, ids, origin, now, fetch=fetch)
    monkeypatch.setattr(tv_grab, 'grab', fake)


@pytest.mark.parametrize('msg', ['This download link points to another site and wasn’t fetched.',
                                 'This download link isn’t valid.', 'This download link isn’t allowed.'])
def test_the_route_answers_400_with_no_fallback(qenv, owner, monkeypatch, known_token, msg):  # noqa: F811
    _route_with(monkeypatch, tracker_sources.GrabError(msg, 400))
    r = _post(owner, BODY)
    assert r.status_code == 400
    out = r.get_json()
    assert out == {'ok': False, 'error': msg, 'fallback': False}


def test_a_tracker_failure_still_offers_the_fallback(qenv, owner, monkeypatch, known_token):  # noqa: F811
    _route_with(monkeypatch, tracker_sources.GrabError('Couldn’t get the torrent from the tracker. Search again.', 502))
    r = _post(owner, BODY)
    assert r.status_code == 200 and r.get_json()['fallback'] is True
