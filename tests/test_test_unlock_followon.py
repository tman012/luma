"""RC-BEAVIS-DOUBLEGRAB: one feed run never grabs the same episode from a second tracker; an import that
added nothing new (every file already there) sends no push."""
import pytest

from app import tv_downloads, tv_grab, tv_safety, tv_search
from tests.test_tv_search_v158b import _auto, allow_grabs, env, item, rows  # noqa: F401
from tests.test_tv_search_v158b import aired, fake_tmdb, lib, owner, scanned, store, tv_env  # noqa: F401 (fixtures)


def _two_trackers(monkeypatch):
    real = tv_search.trackers()
    assert real
    two = [dict(real[0]), {**real[0], 'id': 'second-tracker'}]
    monkeypatch.setattr(tv_search, 'trackers', lambda: two)


@pytest.mark.parametrize('reply', [{'ok': False, 'error': 'qBittorrent refused the torrent'}, {'ok': True, 'hash': 'x'}])
def test_feed_second_tracker_cannot_regrab_episode_in_same_run(env, monkeypatch, reply):  # noqa: F811
    allow_grabs(monkeypatch)
    _auto(feed=True)
    _two_trackers(monkeypatch)
    env.results = [item('Severance.S02E04.1080p.WEB-DL-B')]
    calls = []
    monkeypatch.setattr(tv_grab, 'grab', lambda it, sid, season, covers, origin, now: (calls.append(list(covers)), reply)[1])
    e4 = rows('SELECT air_date, airstamp, runtime FROM episodes WHERE id = ?', env.e2[4])[0]
    out = tv_search.feed(tv_safety.aired_start(e4) + 3600)
    assert out['trackers'] == 2
    assert calls == [[env.e2[4]]]


def test_feed_blocked_or_no_client_does_not_claim(env, monkeypatch):  # noqa: F811
    allow_grabs(monkeypatch)
    _auto(feed=True)
    _two_trackers(monkeypatch)
    env.results = [item('Severance.S02E04.1080p.WEB-DL-B')]
    calls = []
    monkeypatch.setattr(tv_grab, 'grab', lambda it, sid, season, covers, origin, now: (calls.append(1), {'ok': False, 'client': True})[1])
    e4 = rows('SELECT air_date, airstamp, runtime FROM episodes WHERE id = ?', env.e2[4])[0]
    tv_search.feed(tv_safety.aired_start(e4) + 3600)
    assert len(calls) == 2                                   # never reached qBittorrent: the next tracker may try


def test_import_with_every_file_existing_sends_no_push():
    src = open(tv_downloads.__file__).read()
    i = src.index("_notify_imported(res)\n                    except")
    block = src[i - 400:i]
    assert "all((f or {}).get('how') == 'exists' for f in files)" in block
