"""v183: the monitored bookmarks (show, season, episode) use the same routes as the v182 switches. They are the
signed-in owner's (tv.db only, never a search)."""
from flask import jsonify

from app import tv_settings
from tests.test_file_management import minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import _client
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.test_tv_search_v158b import env, item  # noqa: F401
from tests.test_v177_tv_safety import adds
from tests.test_v182_series_detail import _req, spies  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401


def _routes(env):
    sid, e = env.sid, env.e2
    return ((f'/api/settings/tv/shows/{sid}/monitored', {'monitored': False}),
            (f'/api/settings/tv/shows/{sid}/seasons/2/monitored', {'monitored': False}),
            (f'/api/settings/tv/episodes/{e[3]}/monitored', {'monitored': False}))


def test_the_bookmarks_are_the_signed_in_owners(minimal_app, owner, env, spies, monkeypatch):  # noqa: F811
    before = rows('SELECT id, monitored FROM episodes ORDER BY id')
    anon = minimal_app.test_client()
    others = (_client(minimal_app, name='second'), _client(minimal_app, admin=False, name='plain'))
    for u, b in _routes(env):
        assert _req(anon, 'put', u, b).status_code == 401, u
        for c in others:
            assert _req(c, 'put', u, b).status_code == 403, u
    monkeypatch.setitem(tv_settings._cfg, 'signed_in_owner', lambda: (None, (jsonify({'error': 'Sign in', 'sign_in': True}), 401)))
    for u, b in _routes(env):
        assert _req(owner, 'put', u, b).status_code == 401, u
    assert rows('SELECT id, monitored FROM episodes ORDER BY id') == before
    assert rows('SELECT monitored FROM series WHERE id = ?', env.sid)[0]['monitored'] == 1
    assert spies == [] and env.sent == [] and adds(env) == []


def test_the_owner_flips_them_and_nothing_searches(owner, env, spies):  # noqa: F811
    for u, b in _routes(env):
        r = _req(owner, 'put', u, b)
        assert r.status_code == 200, u
    assert rows('SELECT monitored FROM series WHERE id = ?', env.sid)[0]['monitored'] == 0
    assert rows('SELECT monitored FROM episodes WHERE id = ?', env.e2[3])[0]['monitored'] == 0
    assert spies == [] and env.sent == [] and adds(env) == []
