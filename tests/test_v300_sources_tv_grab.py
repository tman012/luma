"""v300: Sources TV grabs route through tv_grab (luma-test-tv), never movie add / library_mover."""
import json
import types

import pytest

import app.main
from app import library_mover as lm, tv_store
from tests.test_downloads_add_library_mover import H, MAGNET, ORIGIN, TMDB
from tests.test_file_management import minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import lib, rows  # noqa: F401
from tests.test_tv_search_v158b import eps  # noqa: F401
from tests.test_tv_v158c import BODY, known_token, popup, qenv  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401


TV_TMDB = {'tmdb_id': 95396, 'media_type': 'tv', 'title': 'Severance', 'year': 2022}


def test_downloads_add_tv_routes_through_tv_grab_not_library_mover(popup, qenv, owner, monkeypatch):  # noqa: F811
    """POST /api/downloads/add with media_type=tv → tv_grab / luma-test-tv + tv.db grab; no mover."""
    tracked = []
    monkeypatch.setattr(lm, 'track', lambda *a, **k: tracked.append(a))
    body = {
        'torrent_url': BODY['download_url'],
        'torrent_name': BODY['title'],
        'tmdb_data': TV_TMDB,
        'season': 2,
        'episode': 3,
        'size': 123,
        'indexer': 'IPT',
        'quality': '1080p',
    }
    r = owner.post('/api/downloads/add', json=body, environ_base=REMOTE, headers=ORIGIN)
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out['success'] is True and out.get('tv') is True
    assert tracked == []
    assert popup.torrents[0]['category'] == popup.torrents[0]['tags'] == 'luma-test-tv'
    g = rows('SELECT * FROM grabs')[0]
    assert (g['series_id'], g['origin'], g['season_number']) == (qenv, 'manual', 2)
    assert json.loads(g['episode_ids']) == [eps(qenv, 2)[3]]


def test_downloads_add_tv_season_pack_registers_tv_grab(popup, qenv, owner, monkeypatch):  # noqa: F811
    tracked = []
    monkeypatch.setattr(lm, 'track', lambda *a, **k: tracked.append(a))
    with tv_store.write() as conn:
        conn.execute(
            'DELETE FROM episode_file_links WHERE episode_id IN '
            '(SELECT id FROM episodes WHERE series_id = ? AND season_number = 2)',
            (qenv,),
        )
    body = {
        'torrent_url': BODY['download_url'],
        'torrent_name': 'Severance.S02.1080p.WEB-DL-PACK',
        'tmdb_data': TV_TMDB,
        'season': 2,
        'episode': None,
    }
    r = owner.post('/api/downloads/add', json=body, environ_base=REMOTE, headers=ORIGIN)
    assert r.status_code == 200 and r.get_json()['success'] is True, r.get_json()
    assert tracked == []
    assert popup.torrents[0]['category'] == 'luma-test-tv'
    g = rows('SELECT * FROM grabs')[0]
    assert sorted(json.loads(g['episode_ids'])) == sorted(eps(qenv, 2).values())


def test_downloads_add_tv_without_season_refuses(owner, qenv, monkeypatch):  # noqa: F811
    tracked = []
    monkeypatch.setattr(lm, 'track', lambda *a, **k: tracked.append(a))
    r = owner.post(
        '/api/downloads/add',
        json={'torrent_url': MAGNET, 'torrent_name': 'x', 'tmdb_data': TV_TMDB},
        environ_base=REMOTE,
        headers=ORIGIN,
    )
    assert r.status_code == 400
    assert 'season' in r.get_json()['error'].lower()
    assert tracked == []


def test_downloads_add_movie_still_uses_movie_tag_and_library_mover(env_movie):
    r = _client(env_movie.app).post(
        '/api/downloads/add',
        json={'torrent_url': MAGNET, 'torrent_name': 'Heat.1995.1080p.BluRay', 'tmdb_data': TMDB},
        environ_base=REMOTE,
        headers=ORIGIN,
    )
    assert r.status_code == 200, r.get_json()
    assert env_movie.added[0]['tags'] == 'luma-test' and env_movie.added[0]['category'] == 'luma-test'
    assert env_movie.tracked == [(H, TMDB, 'Heat.1995.1080p.BluRay')]


@pytest.fixture
def env_movie(test_app, monkeypatch, tmp_path):
    """Same setup as test_downloads_add_library_mover.env (renamed to avoid fixture clash)."""
    import time as _time
    from tests.test_downloads_add_library_mover import QBIT, Resp

    added, torrents, tracked, renamed = [], [], [], []

    class Session:
        def post(self, url, data=None, files=None, timeout=None, **kw):
            if url.endswith('/api/v2/torrents/add'):
                added.append(dict(data or {}))
                torrents.append({'hash': H, 'added_on': int(_time.time())})
            return Resp()

        def get(self, url, params=None, timeout=None, **kw):
            return Resp(data=list(torrents))

    import requests
    monkeypatch.setattr(requests, 'Session', Session)
    real_get = app.main.settings.get
    monkeypatch.setattr(app.main.settings, 'get', lambda k, *a, **kw: QBIT[k] if k in QBIT else real_get(k, *a, **kw))
    monkeypatch.setattr(_time, 'sleep', lambda s: None)
    monkeypatch.setattr(app.main, '_record_origin', lambda *a, **k: None)
    monkeypatch.setattr(lm, 'track', lambda h, d, n=None: tracked.append((h, d, n)))
    fake = types.ModuleType('app.auto_rename')
    fake.auto_renamer = types.SimpleNamespace(track_download=lambda h, d: renamed.append((h, d)))
    monkeypatch.setitem(__import__('sys').modules, 'app.auto_rename', fake)
    (tmp_path / 'index.html').write_text('<!doctype html><div id="root"></div>')
    monkeypatch.setitem(test_app.config, 'SPA_DIR', str(tmp_path))
    from app import start_flow
    monkeypatch.setattr(start_flow, '_load_state', lambda: {})
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    monkeypatch.delenv('LUMA_TEST_QBIT_TAG', raising=False)
    monkeypatch.delenv('LUMA_INSTANCE', raising=False)
    return types.SimpleNamespace(added=added, torrents=torrents, tracked=tracked, renamed=renamed, app=test_app)


def test_track_grab_refuses_tv_media_type(monkeypatch):
    tracked = []
    monkeypatch.setattr(lm, 'track', lambda *a, **k: tracked.append(a))
    monkeypatch.setattr(app.main, '_minimal', types.SimpleNamespace(enabled=lambda: True))
    app.main._track_grab('a' * 40, {'media_type': 'tv', 'tmdb_id': 1, 'title': 'Bluey'})
    assert tracked == []
