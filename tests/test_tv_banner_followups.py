"""Banner follow-ups: the two TheTVDB resets in connectors._forget_air_times are independent; tv_tvdb.banner
raises on any non-200, auth failure or malformed 200 (an outage never reads as 'no banner'); tv_banners backs
off on any exception type with one log line and no traceback, and never stamps banner_checked_at then."""
import logging

import pytest

from app import connectors, tv_airtimes, tv_banners, tv_tvdb
from tests.test_tv_banner_backfill import NOW, _series, bf  # noqa: F401
from tests.test_tv_calendar import _post, aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.test_file_management import store, minimal as minimal_app  # noqa: F401


# ------------------------------------------------------------------ (1) separate resets

def test_banner_reset_runs_when_air_times_reset_fails(monkeypatch):
    seen = []
    monkeypatch.setattr(tv_airtimes, 'forget', lambda: (_ for _ in ()).throw(RuntimeError('no tv.db')))
    monkeypatch.setattr(tv_banners, 'forget', lambda: seen.append('banners'))
    connectors._forget_air_times()
    assert seen == ['banners']


def test_air_times_reset_runs_when_banner_reset_fails(monkeypatch):
    seen = []
    monkeypatch.setattr(tv_airtimes, 'forget', lambda: seen.append('air'))
    monkeypatch.setattr(tv_banners, 'forget', lambda: (_ for _ in ()).throw(RuntimeError('x')))
    connectors._forget_air_times()          # never raises
    assert seen == ['air']


# ------------------------------------------------------------------ (2) banner() raises on an outage

@pytest.fixture
def fake_tvdb():
    st = {'login': (200, {'data': {'token': 't'}}), 'get': (200, {'data': {'artworks': []}})}
    tv_tvdb.configure(post=lambda path, body: st['login'], get=lambda path, params, tok: st['get'])
    yield st
    tv_tvdb.configure()


@pytest.mark.parametrize('status', [500, 502, 503, 429, 403, 301])
def test_banner_raises_on_non_200(fake_tvdb, status):
    fake_tvdb['get'] = (status, {})
    with pytest.raises(tv_tvdb.TvdbError):
        tv_tvdb.banner(1, ('k', ''))


def test_banner_raises_on_repeated_401(fake_tvdb):
    fake_tvdb['get'] = (401, {})
    with pytest.raises(tv_tvdb.TvdbError):
        tv_tvdb.banner(1, ('k', ''))


@pytest.mark.parametrize('login', [(401, {}), (403, {}), (429, {}), (500, {}), (200, {'data': {}})])
def test_banner_raises_when_login_fails(fake_tvdb, login):
    fake_tvdb['login'] = login
    with pytest.raises(tv_tvdb.TvdbError):
        tv_tvdb.banner(1, ('k', ''))


@pytest.mark.parametrize('body', [{}, [], {'data': 'x'}, {'data': {'artworks': 'x'}}, 'oops'])
def test_banner_raises_on_a_malformed_200(fake_tvdb, body):
    fake_tvdb['get'] = (200, body)
    with pytest.raises(tv_tvdb.TvdbError):
        tv_tvdb.banner(1, ('k', ''))


def test_banner_none_only_when_tvdb_answers_none(fake_tvdb):
    fake_tvdb['get'] = (200, {'data': {'artworks': []}})
    assert tv_tvdb.banner(1, ('k', '')) is None
    fake_tvdb['get'] = (200, {'data': {'artworks': None}})
    assert tv_tvdb.banner(1, ('k', '')) is None
    fake_tvdb['get'] = (404, {})
    assert tv_tvdb.banner(1, ('k', '')) is None
    fake_tvdb['get'] = (200, {'data': {'artworks': [{'type': 1, 'image': '/banners/a.jpg', 'score': 3}]}})
    assert tv_tvdb.banner(1, ('k', '')) == 'https://artworks.thetvdb.com/banners/a.jpg'


def test_an_outage_never_stamps_none_found(bf, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tv_tvdb, 'banner', lambda tvdb_id, creds: (_ for _ in ()).throw(tv_tvdb.TvdbError('HTTP 503')))
    out = tv_banners.run_pass(now=NOW)
    assert out == {'asked': 0, 'found': 0, 'none': 0, 'skipped': 0}
    assert all(r['banner_checked_at'] is None for r in _series().values())


# ------------------------------------------------------------------ (3) any exception backs off, one line

@pytest.mark.parametrize('exc', [KeyError('artworks'), TypeError('x'), AttributeError('x'), OSError('x')])
def test_unexpected_errors_back_off_with_one_line(bf, caplog, exc):  # noqa: F811
    ids = sorted(_series(), key=lambda k: _series()[k]['id'])
    bf['arts'][ids[0]] = exc
    with caplog.at_level(logging.INFO, logger='app.tv_banners'):
        out = tv_banners.run_pass(now=NOW)
    assert out['asked'] == 0 and tv_banners._fail['until'] == NOW + tv_banners.BACKOFF_S
    recs = [r for r in caplog.records if r.name == 'app.tv_banners']
    assert len(recs) == 1 and recs[0].exc_info is None
    assert type(exc).__name__ in recs[0].getMessage() and 'next try in 1 min' in recs[0].getMessage()
    assert _series()[ids[0]]['banner_checked_at'] is None
    bf['arts'].pop(ids[0])
    assert tv_banners.run_pass(now=NOW + tv_banners.BACKOFF_S)['found'] == len(ids)
