"""v296: Genre Roulette Web Push shows movie name (import-only, prod AutoRenamer copy).

Standing rule: notify on library import only — not grab/run-finish.
Grab-time gr_engine admin push is silent; movie_import → library_notify carries
"{Title} ({Year}) added!" / "Tap to view details" for GR-sourced movies.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.automation import gr_engine as eng
from app import library_notify, movie_grabs, movie_import as mi, movie_settings
from tests.test_v204_movie_import import H, REL, env, torrent  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_notify():
    library_notify.clear_local()
    library_notify.set_bell_hook(None)
    yield
    library_notify.clear_local()
    library_notify.set_bell_hook(None)


def test_push_body_lists_grabbed_movie_names():
    body = eng.push_body({
        'wanted': 3,
        'grabbed': 2,
        'picks': [
            {'title': 'Pleasantville', 'year': 1998, 'grabbed': True},
            {'title': 'Heat', 'year': 1995, 'grabbed': True},
            {'title': 'Skipped', 'year': 2000, 'grabbed': False},
        ],
    })
    assert body == 'Pleasantville (1998), Heat (1995) · 2 of 3'


def test_push_body_single_full_target_no_count_suffix():
    body = eng.push_body({
        'wanted': 1,
        'grabbed': 1,
        'picks': [{'title': 'Dune: Part Two', 'year': 2024, 'grabbed': True}],
    })
    assert body == 'Dune: Part Two (2024)'


def test_push_body_error_and_empty():
    assert eng.push_body({'error': 'No movie trackers are on'}) == 'No movie trackers are on'
    assert 'Nothing added' in eng.push_body({'wanted': 3, 'grabbed': 0, 'picks': [], 'stop_reason': 'tracker_unreachable'})


def test_summary_stays_counts_for_logs():
    assert eng.summary({'wanted': 3, 'grabbed': 1, 'picks': [
        {'title': 'Heat', 'year': 1995, 'grabbed': True}]}) == 'Added 1 of 3'


def test_run_finish_does_not_web_push(monkeypatch, tmp_path):
    """Grab/finish must stay silent — movie name arrives on import via library_notify."""
    from app.automation import gr_store as gs
    monkeypatch.setitem(gs._cfg, 'db_path', str(tmp_path / 'automation.db'))
    pushes = []
    monkeypatch.setattr(eng, 'notify', lambda t, b: pushes.append((t, b)))
    monkeypatch.setattr(eng, 'grabs_allowed', lambda: False)
    out = eng.run('manual', dry=False, cfg={**gs.defaults(), 'notify': 'finish'},
                  record=True, sleep=lambda s: None)
    assert out.get('error')
    assert pushes == []
    out2 = eng.run('manual', dry=False, cfg={**gs.defaults(), 'notify': 'failure'},
                   record=True, sleep=lambda s: None)
    assert out2.get('error')
    assert pushes == []


def test_gr_movie_import_push_has_movie_name(env, monkeypatch):
    """GR films file via movie_import; prod copy includes the title."""
    pushes, bells = [], []
    monkeypatch.setattr('app.push_service.send_push_notification',
                        lambda **kw: pushes.append(kw) or True)
    monkeypatch.setattr(library_notify, '_backdrop', lambda *a, **k: '')
    library_notify.set_bell_hook(lambda r: bells.append(r))

    from app.automation import gr_store as gs
    # title comes from gr_grabs (roulette films are not on the watchlist)
    monkeypatch.setitem(gs._cfg, 'db_path', str(env[0].parent / 'automation.db'))
    gs.record_grab(H, 693134, 'Dune: Part Two', 2024, None, 50.0)
    # movie_grabs hash must match torrent; gr_store title_for uses tmdb_id
    movie_grabs.record(693134, REL, H)
    # wipe watchlist row if record created none — ensure fallback path
    with movie_settings.connect() as conn:
        conn.execute('DELETE FROM watchlist WHERE tmdb_id = 693134')

    dl, _lib = env
    out = mi.tick(now=100, fetch=lambda: [torrent(dl)], path_map=lambda p: p)
    assert out == {'checked': 1, 'imported': 1}
    assert len(pushes) == 1
    assert pushes[0]['title'] == 'Dune: Part Two (2024) added!'
    assert pushes[0]['body'] == 'Tap to view details'
    assert 'Dune' in pushes[0]['title']
    assert bells and bells[0]['source'] == 'movie_import'


def test_sw_is_v296_or_newer():
    sw = (ROOT / 'static/sw.js').read_text()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 296
    stamp = (ROOT / 'static/sw-stamp.json').read_text()
    assert re.search(r'media-kiosk-v\d+', stamp)
