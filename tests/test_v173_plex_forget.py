"""v173 (Tester FF-5, 607f36a): after Plex is disconnected or removed, the TV Calendar's 'in Plex'
index is dropped at once (no restart), and a test's FakePlex never outlives its test."""
import json
import threading

import pytest

import app.main
import app.user_auth
from app import tv_plex
from tests.test_tv_downloaded_v157 import SHOWS, FakePlex, _ep


class Switch(FakePlex):
    def __init__(self, *a, url='http://plex-a.fake:32400', **k):
        super().__init__(*a, **k)
        self.on, self.plex_url = True, url

    @property
    def is_configured(self):
        return self.on


@pytest.fixture
def plex():
    p = Switch(SHOWS, [_ep(10, 2, 3)])
    tv_plex.init(get_plex=lambda: p)
    assert tv_plex.rebuild(force=True) > 0 and tv_plex.has(95396, None, 2, 3)
    return p


def test_disconnected_plex_stops_answering_and_the_index_is_dropped(plex):
    plex.on = False
    assert not tv_plex.has(95396, None, 2, 3)
    assert not tv_plex._have and not tv_plex._shows and tv_plex._state['built_at'] == 0.0
    plex.on = True                                        # reconnected: nothing until a fresh build
    assert not tv_plex.has(95396, None, 2, 3)
    assert tv_plex.rebuild() > 0 and tv_plex.has(95396, None, 2, 3)


def test_removed_plex_drops_the_index(plex):
    tv_plex.init(get_plex=lambda: None)
    assert not tv_plex.has(95396, None, 2, 3) and not tv_plex._have
    assert tv_plex.rebuild(force=True) == -1 and tv_plex.recent() == 0


def test_a_new_server_does_not_inherit_the_old_index(plex):
    plex.plex_url = 'http://plex-b.fake:32400'
    assert not tv_plex.has(95396, None, 2, 3) and not tv_plex._have
    plex.plex_url = 'http://plex-a.fake:32400/'            # same server, trailing slash: no change
    tv_plex.rebuild(force=True)
    plex.plex_url = 'HTTP://PLEX-A.fake:32400'
    assert tv_plex.has(95396, None, 2, 3)


def test_a_rebuild_that_ends_after_a_disconnect_is_not_stored(plex):
    tv_plex.forget()
    gate, go = threading.Event(), threading.Event()
    real = plex._get

    def slow(path, params=None, timeout=15):
        if path != '/library/sections':
            gate.set()
            go.wait(5)
        return real(path, params, timeout)
    plex._get = slow
    t = threading.Thread(target=tv_plex.rebuild, kwargs={'force': True})
    t.start()
    gate.wait(5)
    plex.on = False
    go.set()
    t.join(5)
    plex.on = True
    assert not tv_plex._have and not tv_plex.has(95396, None, 2, 3)


def test_the_calendar_rows_stop_reading_downloaded_after_a_disconnect(plex):
    from app import tv_calendar  # noqa: F401 - has() is what the calendar asks (tv_calendar line ~51)
    assert tv_plex.has(95396, None, 2, 3)
    plex.on = False
    assert tv_plex.has(95396, None, 2, 3) is False


@pytest.fixture
def admin(test_app):
    o = app.user_auth.create_user('owner', 'pin-1234567', is_admin=True)
    c = test_app.test_client()
    c.environ_base.update({'REMOTE_ADDR': '192.168.1.20', 'HTTP_ORIGIN': 'http://localhost', 'HTTP_HOST': 'localhost'})
    c.set_cookie(app.main.AUTH_COOKIE, app.user_auth.create_session(o['id']))
    return c


def test_clearing_plex_in_settings_reconfigures_the_shared_client_now(admin, monkeypatch):
    ps, s = app.main.plex_service, app.main.settings
    saved = (s.get('plex.url', ''), s.get('plex.token', ''), ps.plex_url, ps.plex_token)
    try:
        s.update({'plex': {'url': 'http://plex-a.fake:32400', 'token': 'tok-a-1234567890'}})
        ps.update_config('http://plex-a.fake:32400', 'tok-a-1234567890')
        tv_plex.init(get_plex=lambda: ps)
        tv_plex._have.add(('tmdb', 95396, 2, 3))           # as a rebuild against plex-a would leave it
        tv_plex._state.update(built_at=1.0, server='http://plex-a.fake:32400')
        assert tv_plex.has(95396, None, 2, 3)
        r = admin.put('/api/settings', data=json.dumps({'plex': {'url': '', 'token': ''}}),
                      content_type='application/json')
        assert r.status_code == 200, r.get_json()
        assert not ps.is_configured                         # the shared client follows the change
        assert not tv_plex._have and not tv_plex.has(95396, None, 2, 3)
    finally:
        s.update({'plex': {'url': saved[0], 'token': saved[1]}})
        ps.update_config(saved[2], saved[3])


_LEAK = {}


def test_hygiene_1_a_test_registers_a_fake_plex():
    fake = FakePlex(SHOWS, [_ep(10, 2, 3)])
    _LEAK['fn'] = lambda: fake
    tv_plex.init(get_plex=_LEAK['fn'])


def test_hygiene_2_the_next_test_does_not_see_it():
    assert tv_plex._cfg.get('get_plex') is not _LEAK.get('fn')
    assert not tv_plex._have and tv_plex._state['built_at'] == 0.0
