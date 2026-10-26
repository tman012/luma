"""v283: movie_import fires library_notify on import-complete (prod AutoRenamer parity).

Watchlist / Genre Roulette movies file via movie_import (not library_mover).
Notify is Web Push + bell only; never on grab; never Telegram/email.
"""
from __future__ import annotations

from pathlib import Path

import pytest

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


@pytest.fixture
def captured(monkeypatch):
    pushes, bells = [], []

    def push(**kw):
        pushes.append(kw)
        return True

    monkeypatch.setattr('app.push_service.send_push_notification', push)
    monkeypatch.setattr(library_notify, '_backdrop', lambda *a, **k: '')
    library_notify.set_bell_hook(lambda r: bells.append(r))
    return pushes, bells


def test_import_one_fires_library_notify(env, captured):
    pushes, bells = captured
    dl, lib = env
    movie_grabs.record(693134, REL, H)
    out = mi.tick(now=100, fetch=lambda: [torrent(dl)], path_map=lambda p: p)
    assert out == {'checked': 1, 'imported': 1}
    assert len(pushes) == 1
    assert pushes[0]['title'] == 'Dune: Part Two (2024) added!'
    assert pushes[0]['body'] == 'Tap to view details'
    assert pushes[0]['url'] == '/?modal=movie&tmdb=693134'
    assert pushes[0]['tag'] == 'movie-Dune: Part Two'
    assert len(bells) == 1
    assert bells[0]['source'] == 'movie_import'
    assert bells[0]['tmdb_id'] == 693134


def test_grab_does_not_notify(env, captured):
    pushes, _ = captured
    movie_grabs.record(693134, REL, H, now=10)
    assert movie_grabs.by_hash(H)['state'] == 'grabbed'
    assert pushes == []


def test_failed_import_does_not_notify(env, captured, monkeypatch):
    pushes, _ = captured
    dl, lib = env
    movie_grabs.record(693134, REL, H)
    d = lib / 'Dune - Part Two (2024)'
    d.mkdir()
    (d / 'Dune- Part Two (2024).mkv').write_bytes(b'mine')
    mi.tick(now=100, fetch=lambda: [torrent(dl)], path_map=lambda p: p)
    assert movie_grabs.by_hash(H)['state'] == 'failed'
    assert pushes == []


def test_no_telegram_or_email_from_movie_import(env, captured, monkeypatch):
    pushes, _ = captured
    called = {'tg': 0}

    def boom_tg(*a, **k):
        called['tg'] += 1
        raise AssertionError('check_and_notify must not run')

    monkeypatch.setattr('app.pending_requests.check_and_notify', boom_tg, raising=False)
    dl, _ = env
    movie_grabs.record(693134, REL, H)
    mi.tick(now=100, fetch=lambda: [torrent(dl)], path_map=lambda p: p)
    assert len(pushes) == 1
    assert called['tg'] == 0
    assert not hasattr(library_notify, '_telegram_pending')


def test_notify_failure_does_not_fail_import(env, monkeypatch):
    dl, lib = env
    movie_grabs.record(693134, REL, H)

    def boom(**kw):
        raise RuntimeError('push down')

    monkeypatch.setattr(library_notify, 'notify_library_added', boom)
    out = mi.tick(now=100, fetch=lambda: [torrent(dl)], path_map=lambda p: p)
    assert out == {'checked': 1, 'imported': 1}
    assert movie_grabs.by_hash(H)['state'] == 'imported'
    with movie_settings.connect() as conn:
        assert conn.execute('SELECT 1 FROM watchlist WHERE tmdb_id = 693134').fetchone() is None


def test_sw_is_v283():
    import re
    sw = (ROOT / 'static/sw.js').read_text()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 283
    assert "CACHE_VERSION = 'media-kiosk-v283'" in sw
