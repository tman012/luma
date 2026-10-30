"""Ops (CAKES re-grab): a hash whose grab row is failed or lost is sent to qBittorrent again and the row
revived; an active or imported row still dedupes. Manual search says grabbed: false (with a reason) when
nothing was sent."""
import pytest

from app import movie_grabs, tv_grab, tv_search, tv_store
from tests.test_tv_v158c import FakeQbit, T, _grab, qenv  # noqa: F401
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.test_tv_search_v158b import allow_grabs, env, eps, item, sev  # noqa: F401


def _row(h):
    with tv_store.connect() as conn:
        r = conn.execute('SELECT state, error, origin FROM grabs WHERE hash = ?', (h,)).fetchone()
    return dict(r) if r else None


def _set(h, state, error=None):
    with tv_store.write() as conn:
        conn.execute('UPDATE grabs SET state = ?, error = ?, finished_at = 1 WHERE hash = ?', (state, error, h))


@pytest.mark.parametrize('state,error', [('failed', 'qBittorrent refused the torrent'),
                                         ('failed', 'lost: not in qBittorrent 5 min after the grab'),
                                         ('skipped', None)])
def test_failed_or_lost_row_is_sent_again_and_revived(qenv, state, error):  # noqa: F811
    q = FakeQbit()
    first = _grab(q, qenv)
    h = first['hash']
    assert first['ok'] and len(q.torrents) == 1
    _set(h, state, error)
    again = _grab(q, qenv, now=9000.0)
    assert again['ok'] and not again.get('existing') and again['hash'] == h
    assert len(q.torrents) == 2                                   # really sent to qBit again
    assert _row(h)['state'] == 'downloading' and _row(h)['error'] is None


@pytest.mark.parametrize('state', ['downloading', 'importing'])
def test_active_row_still_dedupes(qenv, state, monkeypatch):  # noqa: F811
    q = FakeQbit()
    h = _grab(q, qenv)['hash']
    _set(h, state)
    e3 = eps(qenv, 2)[3]
    out = tv_grab.grab(item('Severance.S02E03.1080p.WEB-DL-B'), qenv, 2, [e3], 'manual', 9000.0,
                       session_factory=q.factory, fetch=lambda u: ('torrent', T))
    assert out.get('existing') and out.get('grabbed') is False and out.get('reason') == 'existing'
    assert len(q.torrents) == 1


def test_interactive_grab_of_an_existing_hash_says_not_grabbed(qenv, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tv_grab, 'grab', lambda *a, **k: {'ok': True, 'hash': 'a' * 40, 'existing': True})
    out = tv_search.grab_release(qenv, item('Severance.S02E03.1080p.WEB-DL-B'))
    assert out['grabbed'] is False and out['reason'] == 'existing'
    monkeypatch.setattr(tv_grab, 'grab', lambda *a, **k: {'ok': False, 'error': 'qBittorrent returned HTTP 500'})
    out = tv_search.grab_release(qenv, item('Severance.S02E03.1080p.WEB-DL-B'))
    assert out['grabbed'] is False and out['reason'] == 'send_failed'
    monkeypatch.setattr(tv_grab, 'grab', lambda *a, **k: {'ok': True, 'hash': 'a' * 40})
    assert tv_search.grab_release(qenv, item('Severance.S02E03.1080p.WEB-DL-B'))['grabbed'] is True


def test_movie_failed_grab_of_the_same_hash_is_revived(tmp_path, monkeypatch):
    from app import movie_settings
    monkeypatch.setitem(movie_settings._cfg, 'db_path', str(tmp_path / 'movies.db'))
    monkeypatch.setitem(movie_settings._cfg, 'seed_path', str(movie_settings.SEED_FILE))
    h = 'e' * 40
    movie_grabs.record(1, 'X', h, now=1)
    movie_grabs.set_state(h, 'failed', error='lost: gone', now=2)
    movie_grabs.record(1, 'X', h, now=3)                          # what grab() does after a re-send
    movie_grabs.set_state(h, 'grabbed', now=3)
    assert movie_grabs.by_hash(h)['state'] == 'grabbed' and movie_grabs.by_hash(h)['error'] is None
