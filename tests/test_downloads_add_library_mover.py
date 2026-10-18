"""/api/downloads/add hands minimal builds' grabs to the library mover with
the test install's qBittorrent tag, and production's grabs to AutoRenamer
exactly as before (no tag)."""

import sys
import time
import types

import pytest

import app.main
from tests.test_home_rails import REMOTE, _client

ORIGIN = {'Origin': 'http://localhost', 'Host': 'localhost'}
H = 'd' * 40
MAGNET = f'magnet:?xt=urn:btih:{H}&dn=Heat.1995.1080p.BluRay'
TMDB = {'tmdb_id': 949, 'media_type': 'movie', 'title': 'Heat', 'year': 1995}
QBIT = {'qbittorrent.url': 'http://qbit.invalid:8080', 'qbittorrent.username': 'u',
        'qbittorrent.password': 'p', 'qbittorrent.app_category': 'luma-test'}


class Resp:
    def __init__(self, data=None):
        self.status_code, self.text, self._data = 200, 'Ok.', data
        self.headers, self.content, self.encoding = {}, b'Ok.', 'utf-8'

    def json(self):
        return self._data


@pytest.fixture
def env(test_app, monkeypatch, tmp_path):
    added, torrents, tracked, renamed = [], [], [], []

    class Session:
        def post(self, url, data=None, files=None, timeout=None, **kw):
            if url.endswith('/api/v2/torrents/add'):
                added.append(dict(data or {}))
                torrents.append({'hash': H, 'added_on': int(time.time())})
            return Resp()

        def get(self, url, params=None, timeout=None, **kw):
            return Resp(data=list(torrents))
    import requests
    monkeypatch.setattr(requests, 'Session', Session)
    real_get = app.main.settings.get
    monkeypatch.setattr(app.main.settings, 'get', lambda k, *a, **kw: QBIT[k] if k in QBIT else real_get(k, *a, **kw))
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    monkeypatch.setattr(app.main, '_record_origin', lambda *a, **k: None)
    from app import library_mover
    monkeypatch.setattr(library_mover, 'track', lambda h, d, n=None: tracked.append((h, d, n)))
    fake = types.ModuleType('app.auto_rename')
    fake.auto_renamer = types.SimpleNamespace(track_download=lambda h, d: renamed.append((h, d)))
    monkeypatch.setitem(sys.modules, 'app.auto_rename', fake)
    (tmp_path / 'index.html').write_text('<!doctype html><div id="root"></div>')
    monkeypatch.setitem(test_app.config, 'SPA_DIR', str(tmp_path))
    from app import start_flow
    monkeypatch.setattr(start_flow, '_load_state', lambda: {})
    return types.SimpleNamespace(added=added, torrents=torrents, tracked=tracked, renamed=renamed, app=test_app)


def _grab(c):
    return c.post('/api/downloads/add', json={'torrent_url': MAGNET, 'torrent_name': 'Heat.1995.1080p.BluRay',
                                              'tmdb_data': TMDB}, environ_base=REMOTE, headers=ORIGIN)


def test_minimal_grab_is_tagged_and_goes_to_the_library_mover(env, monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    monkeypatch.delenv('LUMA_TEST_QBIT_TAG', raising=False)
    r = _grab(_client(env.app))
    assert r.status_code == 200, r.get_json()
    assert env.added[0]['tags'] == 'luma-test' and env.added[0]['category'] == 'luma-test'
    assert env.tracked == [(H, TMDB, 'Heat.1995.1080p.BluRay')]
    assert env.renamed == []


def test_minimal_tag_follows_the_environment(env, monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    monkeypatch.setenv('LUMA_TEST_QBIT_TAG', 'qa-box')
    # v171+ (Reviewer): a LUMA_TEST_* variable makes this the test instance (fail closed), where a
    # movie add is the movie_grab job (off by default) and is scoped to the test tag, category too.
    assert _grab(_client(env.app)).status_code == 403
    assert env.added == []
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'movie_grab')
    # v176 (PM): opt-ins count only when LUMA_INSTANCE is exactly 'test'; an assumed test instance ignores them
    assert _grab(_client(env.app)).status_code == 403
    assert env.added == []
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    assert _grab(_client(env.app)).status_code == 200
    assert env.added[0]['tags'] == 'qa-box' and env.added[0]['category'] == 'qa-box'


def test_minimal_regrab_of_an_existing_torrent_is_tracked_not_retagged(env, monkeypatch):
    """Already in the shared client (perhaps production's): tracked here, but
    the mover only copies it if it already carries this install's tag."""
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    env.torrents.append({'hash': H, 'added_on': 1})
    r = _grab(_client(env.app))
    assert r.status_code == 200 and r.get_json()['message'] == 'Already downloading'
    assert env.added == [] and env.tracked and env.renamed == []


def test_production_grab_is_unchanged(env, monkeypatch):
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    r = _grab(_client(env.app))
    assert r.status_code == 200, r.get_json()
    assert 'tags' not in env.added[0]
    assert env.renamed == [(H, TMDB)] and env.tracked == []
