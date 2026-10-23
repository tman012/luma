"""v251 (a): tracker queries in Sonarr clean-title style with fallbacks, and release titles like
'Grey s Anatomy' matched to Grey's Anatomy (search and feed). Every tracker, Plex and qBit call is mocked."""
import pytest

from app import tv_match, tv_release, tv_safety, tv_search
from tests.test_tv_search_v158b import _auto, allow_grabs, env, item, rows  # noqa: F401
from tests.test_tv_search_v158b import aired, owner, scanned, store, tv_env, lib, fake_tmdb, minimal_app  # noqa: F401
from tests.test_v233_tv_audit import _per_query, distinct_torrents, make_show, ts

GREYS = ["Grey's.Anatomy.S23E02.1080p.WEB.h264-ELiTE", 'Greys.Anatomy.S23E02.1080p.WEB.H264-playWEB',
         'Grey s Anatomy S23E02 1080p WEB H264-MeGusta', 'Greys Anatomy S23E02 720p HEVC x265-MeGusta',
         "Grey's Anatomy S23E02 1080p AMZN WEB-DL DDP5 1 H 264-playWEB"]


@pytest.mark.parametrize('title, want', [
    ("Special Forces: World's Toughest Test", 'Special Forces Worlds Toughest Test'),
    ("Grey's Anatomy", 'Greys Anatomy'),
    ('Law & Order: Special Victims Unit', 'Law and Order Special Victims Unit'),
    ('Marvel\u2019s Agents of S.H.I.E.L.D.', 'Marvels Agents of S H I E L D'),
    ('Pokémon:  Horizons', 'Pokemon Horizons'),
    ('Ghosts (US)', 'Ghosts US'),
])
def test_query_title_is_sonarr_clean_title_style(title, want):
    assert tv_match.query_title(title) == want


@pytest.mark.parametrize('name', GREYS)
def test_every_greys_anatomy_release_name_matches_the_show(name):
    assert tv_match.release_key(tv_release.parse(name).title) == tv_match.release_key("Grey's Anatomy") == 'greys anatomy'


def test_the_lone_s_join_does_not_merge_other_shows():
    assert tv_match.release_key('Grey s Anatomy') != tv_match.release_key('Grey Anatomy')
    assert tv_match.release_key('Station 19') == 'station 19'


def test_special_forces_query_is_clean(env, monkeypatch):
    sid, ids = make_show(env, "Special Forces: World's Toughest Test", 'standard', 5, (5,), '2026-10-21',
                         '2026-10-22T00:00:00Z', 60)
    name = 'Special.Forces.Worlds.Toughest.Test.S05E05.1080p.WEB.h264-EDITH'
    sent = _per_query(env, monkeypatch, {'Special Forces Worlds Toughest Test S05E05': [name]})
    out = tv_search.search_show(sid, list(ids.values()), origin='auto', now=ts(2026, 10, 21, 21), do_grab=False)
    assert sent == ['Special Forces Worlds Toughest Test S05E05']
    assert [p['title'] for p in out['picks']] == [name]


def test_greys_anatomy_search_finds_and_grabs_the_apostrophe_less_release(env, monkeypatch):
    allow_grabs(monkeypatch)
    distinct_torrents(env, monkeypatch)
    sid, ids = make_show(env, "Grey's Anatomy", 'standard', 23, (2,), '2026-10-23', '2026-10-24T01:00:00Z', 60)
    sent = _per_query(env, monkeypatch, {'Greys Anatomy S23E02': ['Grey s Anatomy S23E02 1080p WEB H264-MeGusta']})
    out = tv_search.search_show(sid, list(ids.values()), origin='auto', now=ts(2026, 10, 23, 22))
    assert sent == ['Greys Anatomy S23E02']
    assert [p['title'] for p in out['picks'] if p['grabbed']] == ['Grey s Anatomy S23E02 1080p WEB H264-MeGusta']


def test_fallback_to_the_stripped_title_then_aliases_only_when_nothing_is_found(env, monkeypatch):
    sid, ids = make_show(env, 'Ghosts (US)', 'standard', 5, (3,), '2026-10-21', '2026-10-22T00:00:00Z', 30)
    from app import tv_store
    with tv_store.write() as conn:
        conn.execute('UPDATE series SET aliases = ? WHERE id = ?', ('["Ghosts US", "Ghosts: Américain"]', sid))
    sent = _per_query(env, monkeypatch, {'Ghosts Americain S05E03': ['Ghosts.S05E03.1080p.WEB.h264-EDITH']})
    out = tv_search.search_show(sid, list(ids.values()), origin='auto', now=ts(2026, 10, 21, 21), do_grab=False)
    # clean title, then the stripped title, then the aliases (the duplicate 'Ghosts US' is skipped)
    assert sent == ['Ghosts US S05E03', 'Ghosts S05E03', 'Ghosts Americain S05E03']
    assert out['searched'] == 3
    sent.clear()
    sent2 = _per_query(env, monkeypatch, {'Ghosts US S05E03': ['Ghosts.US.S05E03.1080p.WEB.h264-EDITH']})
    tv_search.search_show(sid, list(ids.values()), origin='auto', now=ts(2026, 10, 21, 21, 15), do_grab=False)
    assert sent2 == ['Ghosts US S05E03']                                    # found: no fallback


def test_no_fallback_when_no_tracker_answered(env, monkeypatch):
    sid, ids = make_show(env, 'Ghosts (US)', 'standard', 5, (3,), '2026-10-21', '2026-10-22T00:00:00Z', 30)
    sent = []
    monkeypatch.setattr(tv_search, 'query_trackers', lambda q, p, s, i, now, cfgs=None: (
        sent.append(q) or [], [{'id': 7, 'name': 'MyTracker', 'status': 'error'}]))
    tv_search.search_show(sid, list(ids.values()), origin='auto', now=ts(2026, 10, 21, 21), do_grab=False)
    assert sent == ['Ghosts US S05E03']


def test_feed_maps_greys_release_names_to_greys_anatomy(env, monkeypatch):
    allow_grabs(monkeypatch)
    distinct_torrents(env, monkeypatch)
    _auto(feed=True)
    sid, ids = make_show(env, "Grey's Anatomy", 'standard', 23, (2,), '2026-10-23', '2026-10-24T01:00:00Z', 60)
    env.results = [item('Grey s Anatomy S23E02 1080p WEB H264-MeGusta')]
    t0 = tv_safety.aired_start({'airstamp': '2026-10-24T01:00:00Z'}) + 3600
    assert tv_search.feed(t0) == {'trackers': 1, 'grabbed': 1}
    assert [r['release_title'] for r in rows('SELECT release_title FROM grabs WHERE series_id = ?', sid)] == \
        ['Grey s Anatomy S23E02 1080p WEB H264-MeGusta']
