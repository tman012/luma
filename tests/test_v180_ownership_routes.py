"""v180: prod's library ownership on test (LUMA_MINIMAL). The owned Play/Watch
and Home's owned orbs read only these routes; none is a gated write, the
instance guard stays at tv_grab only and the 21 gated jobs are unchanged."""
import inspect
import re

import pytest
import requests

import app.main
from app import instance, minimal
from tests.test_home_rails import REMOTE, _client

GATED_21 = {
    'auto_rename', 'daily_missing', 'downloads_guard', 'franchise_completer', 'roulette', 'movie_night',
    'anticipated_watchlist', 'now_available_watchlist', 'trending_watchlist', 'trending_tv_watchlist',
    'latest_auto_grab', 'upgrade_4k', 'upgrade_scheduler', 'movie_collections', 'watchlist_digital_dates',
    'arr', 'qbit', 'storage_cleanup', 'tv_delete_files', 'tv_grab', 'movie_grab',
}
# What a gated write looks like in a view: the instance gate, a grab/add or a torrent client call.
WRITE_SIGNS = re.compile(r'writes_allowed\(|instance\.require\(|_instance\.require\(|\btv_grab\b|\bmovie_grab\b|'
                         r'add_torrent|torrents_add|qbit|radarr_add|sonarr_add|grab_release|settings\.set\(|'
                         r'_save_settings|\.delete\(')


def _sample(rule: str) -> str:
    return re.sub(r'<int:[a-z_]+>', '7', rule)


def _rule(path: str):
    for r in app.main.app.url_map.iter_rules():
        if r.rule == path:
            return r
    raise AssertionError(f'no route {path}')


def test_inventory_is_the_six_reads():
    assert [m for m, _ in minimal.OWNERSHIP_READ] == ['GET', 'POST', 'GET', 'GET', 'GET', 'GET']
    assert len({p for _, p in minimal.OWNERSHIP_READ}) == 6


@pytest.mark.parametrize('method,path', minimal.OWNERSHIP_READ)
def test_each_route_is_on_in_minimal_for_its_method_only(method, path):
    sample = _sample(path)
    assert minimal.path_allowed(sample, method), path
    methods = set(_rule(path).methods) - {'HEAD', 'OPTIONS'}
    assert methods == {method}, (path, methods)
    if method == 'GET':
        for m in ('POST', 'PUT', 'PATCH', 'DELETE'):
            assert not minimal.path_allowed(sample, m), (path, m)
    assert sample not in minimal.SIGNED_OUT_PATHS


@pytest.mark.parametrize('method,path', minimal.OWNERSHIP_READ)
def test_no_route_is_a_gated_write(method, path):
    view = app.main.app.view_functions[_rule(path).endpoint]
    src = inspect.getsource(inspect.unwrap(view))
    hit = WRITE_SIGNS.search(src)
    assert hit is None, (path, hit.group(0) if hit else None)


def test_guard_stays_tv_grab_only_with_21_gated_jobs(monkeypatch):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'tv_grab')
    monkeypatch.setattr(instance, 'prod_conflict', lambda: [])
    assert set(instance.GATED) == GATED_21 and len(instance.GATED) == 21
    assert instance.allowed() & set(instance.GATED) == {'tv_grab'}
    assert set(instance.blocked_jobs()) == GATED_21 - {'tv_grab'}


def test_no_write_route_was_opened():
    for p in ('/api/check-ownership', '/api/radarr/add', '/api/sonarr/series', '/api/plex/play',
              '/api/plex/stream/7', '/api/watchlist', '/api/request/x'):
        for m in ('GET', 'POST'):
            assert not minimal.path_allowed(p, m), (p, m)
    for m in ('POST', 'PUT', 'PATCH', 'DELETE'):      # TV detail reads under /api/tv/ stay GET only
        assert not minimal.path_allowed('/api/tv/grab', m)
        assert not minimal.path_allowed('/api/tv/ownership/7/cached', m)


class _Plex:
    ENTRY = {'rating_key': '42', 'title': 'Inception', 'year': 2010, 'library_name': 'Movies'}

    def build_ownership_index(self):
        e = self.ENTRY
        return {'movies': {'inception|2010': e}, 'movies_by_title': {'inception': [e]},
                'movies_by_tmdb': {27205: e}, 'tv': {}, 'tv_by_title': {}, 'tv_by_tmdb': {}, 'tv_by_tvdb': {}}


@pytest.fixture
def test_minimal(test_app, monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'tv_grab')
    monkeypatch.setattr(app.main, 'get_plex_for_request', lambda *a, **k: _Plex())
    ps = app.main.plex_service
    monkeypatch.setattr(ps, 'find_movie_copies', lambda *a, **k: [], raising=False)
    monkeypatch.setattr(ps, 'get_sections_lean', lambda *a, **k: [{'id': '1', 'type': 'movie', 'title': 'Movies'}],
                        raising=False)
    monkeypatch.setattr(ps, 'search_library', lambda *a, **k: [{'title': 'Inception', 'year': 2010, 'ratingKey': 42}],
                        raising=False)
    gate_calls, sent = [], []
    real_wa = instance.writes_allowed
    monkeypatch.setattr(instance, 'writes_allowed', lambda j: gate_calls.append(j) or real_wa(j))
    monkeypatch.setattr(instance, 'require', lambda j: gate_calls.append(j))
    real_req = requests.Session.request

    def spy(self, method, url, *a, **k):
        sent.append((method.upper(), url))
        return real_req(self, method, url, *a, **k)
    monkeypatch.setattr(requests.Session, 'request', spy)
    test_app.gate_calls, test_app.sent = gate_calls, sent
    return test_app


def test_ownership_reads_answer_on_test_without_touching_the_gate(test_minimal):
    c = _client(test_minimal)
    h = {'Origin': 'http://localhost', 'Host': 'localhost'}
    r = c.get('/api/plex/lookup?title=Inception&year=2010', environ_base=REMOTE, headers=h)
    assert r.status_code == 200 and r.get_json()['owned'] is True
    r = c.post('/api/check-ownership-batch', json={'movies': [{'title': 'Inception', 'year': 2010, 'tmdb_id': 27205},
                                                            {'title': 'Nope', 'year': 2001, 'tmdb_id': 1}]},
               environ_base=REMOTE, headers=h)
    assert r.status_code == 200
    res = r.get_json()['results']
    assert res['movie:inception_2010']['owned'] is True and res['movie:nope_2001']['owned'] is False
    assert test_minimal.gate_calls == []
    assert all(m == 'GET' for m, _ in test_minimal.sent), test_minimal.sent


def test_ownership_reads_need_sign_in(test_minimal):
    c = test_minimal.test_client()
    assert c.get('/api/plex/lookup?title=Inception', environ_base=REMOTE).status_code in (401, 302)
    assert c.post('/api/check-ownership-batch', json={'movies': []}, environ_base=REMOTE).status_code in (401, 302)
