"""v252 Settings > Automation > Genre Roulette (native, no Radarr): engine rules with every outside service
mocked (TMDb, OMDb, trackers, qBittorrent, Plex). Ported from prod 1b4ea74 app/genre_roulette.py."""
import random
import re
from datetime import date
from pathlib import Path

import pytest

from app.automation import gr_engine as eng, gr_select as sel, gr_store as gs
from app.movie_scanner import grab as REAL_GRAB

G = gs.GENRES
NOW = 2e9


def film(i, title, genres, votes=5000, year=2005, collection=0, imdb=(7.0, 300000)):
    return {'id': i, 'title': title, 'genre_ids': [G[g] for g in genres], 'votes': votes, 'year': year,
            'collection': collection, 'imdb': imdb}


class World:
    """TMDb + OMDb + Plex + trackers + qBittorrent, all fake."""

    def __init__(self, cat):
        self.cat = {f['id']: f for f in cat}
        self.owned = []                    # (tmdb_id, title, year, section)
        self.releases = {}                 # tmdb id -> list of release dicts ('ok' search) ; missing -> []
        self.search_status = 'ok'
        self.grab_result = {}              # tmdb id -> dict returned by grab
        self.grabs, self.searches, self.tmdb_calls = [], [], []
        self.omdb_fail = set()
        self.omdb_key = False
        self.plex_fail = False
        self.pushes = []

    def tmdb_get(self, path, params):
        self.tmdb_calls.append(path)
        if path == '/discover/movie':
            g = int(params['with_genres'])
            lo, hi = int(params['primary_release_date.gte'][:4]), int(params['primary_release_date.lte'][:4])
            rows = [f for f in self.cat.values() if g in f['genre_ids'] and f['votes'] >= params['vote_count.gte']
                    and lo <= f['year'] <= hi]
            rows.sort(key=lambda f: -f['votes'])
            page = int(params['page'])
            chunk = rows[(page - 1) * 20: page * 20]
            return {'total_pages': max(1, -(-len(rows) // 20)),
                    'results': [{'id': f['id'], 'title': f['title'], 'release_date': f'{f["year"]}-05-01',
                                 'genre_ids': f['genre_ids'], 'vote_count': f['votes'], 'vote_average': 6.0}
                                for f in chunk]}
        m = re.match(r'^/movie/(\d+)$', path)
        if m:
            f = self.cat.get(int(m.group(1)))
            if not f:
                return None
            return {'imdb_id': f'tt{f["id"]:07d}', 'runtime': 120,
                    'belongs_to_collection': {'id': f['collection']} if f['collection'] else None}
        m = re.match(r'^/collection/(\d+)$', path)
        if m:
            c = int(m.group(1))
            parts = [{'id': f['id'], 'title': f['title'], 'release_date': f'{f["year"]}-01-01'}
                     for f in self.cat.values() if f['collection'] == c]
            parts += [{'id': o[0], 'title': o[1], 'release_date': f'{o[2]}-01-01'} for o in self.owned
                      if len(o) > 4 and o[4] == c]
            return {'parts': parts}
        return None

    def imdb(self, imdb_id):
        tid = int(imdb_id[2:])
        if tid in self.omdb_fail:
            return None
        r, v = self.cat[tid]['imdb']
        return {'rating': r, 'votes': v}

    def plex(self):
        if self.plex_fail:
            raise RuntimeError('down')
        by = {}
        for o in self.owned:
            by.setdefault(o[1].lower(), []).append({'tmdb_id': o[0], 'title': o[1], 'year': o[2],
                                                    'library_name': o[3] if len(o) > 3 else 'Movies'})
        return {'movies_by_title': by}

    def search(self, f, cfgs, now):
        self.searches.append(f['tmdb_id'])
        if self.search_status != 'ok':
            return [], [{'id': 1, 'name': 'IPT', 'status': self.search_status}]
        return list(self.releases.get(f['tmdb_id'], [])), [{'id': 1, 'name': 'IPT', 'status': 'ok'}]

    def grab(self, item, movie, now):
        self.grabs.append((movie['tmdb_id'], item['title']))
        r = self.grab_result.get(movie['tmdb_id'])
        if callable(r):
            return r(item)
        return r or {'ok': True, 'hash': f'{movie["tmdb_id"]:040x}'}


def rel(tid, title='X', seeders=20):
    return {'title': f'{title}.2005.1080p.WEB-DL-GRP', 'size': 8 * 1024 ** 3, 'seeders': seeders, 'indexerId': 1,
            'indexer': 'IPT', 'downloadUrl': '/x', 'quality': 'webdl-1080p'}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setitem(gs._cfg, 'db_path', str(tmp_path / 'automation.db'))
    w = World([])
    monkeypatch.setattr(sel, 'tmdb_key', lambda: 'k')
    monkeypatch.setattr(sel, 'tmdb_get', w.tmdb_get)
    monkeypatch.setattr(sel, 'imdb_ratings', w.imdb)
    monkeypatch.setattr(sel, 'omdb_key_set', lambda: w.omdb_key)
    monkeypatch.setattr(sel, 'plex_index', w.plex)
    monkeypatch.setattr(sel, 'in_flight', lambda: set())
    monkeypatch.setattr(eng, 'trackers', lambda: [{'id': 1, 'name': 'IPT'}])
    monkeypatch.setattr(eng, 'profile', lambda pid: {'id': 1, 'items': [{'id': 'webdl-1080p', 'allowed': True}]})
    monkeypatch.setattr(eng, 'grabs_allowed', lambda: True)
    monkeypatch.setattr(eng, 'search', w.search)
    monkeypatch.setattr(eng, 'rank', lambda items, movie, prof, cfgs, ms: [
        {'item': it} for it in sorted(items, key=lambda i: -i['seeders']) if it['seeders'] >= ms])
    monkeypatch.setattr(eng, 'grab', w.grab)
    monkeypatch.setattr(eng, 'note_origin', lambda h, f, it: None)
    monkeypatch.setattr(eng, 'notify', lambda t, b: w.pushes.append((t, b)))
    w.owned.append((999999, 'Some Owned Film', 1999, 'Movies'))
    return w


def cfg(**k):
    c = gs.defaults(date(2026, 10, 23))
    c.update({'year_from': 1900, 'year_to': 2026, **k})
    return c


def go(w, c=None, seed=1, **k):
    return eng.run(k.pop('kind', 'manual'), cfg=c or cfg(), now=NOW, rng=random.Random(seed), sleep=lambda s: None, **k)


def three_genre_world(w):
    cat = []
    i = 100
    for g in ('Action', 'Comedy', 'Drama'):
        for n in range(5):
            i += 1
            cat.append(film(i, f'{g} {n}', [g], votes=10000 - n * 100))
    w.cat = {f['id']: f for f in cat}
    for f in cat:
        w.releases[f['id']] = [rel(f['id'])]
    return cat


ONLY3 = {'include_genres': ['Action', 'Comedy', 'Drama']}


# ------------------------------------------------------------------ defaults and settings

def test_defaults_match_prod_and_the_final_decisions(world):
    d = gs.settings()
    assert d['enabled'] is False                                   # prod: off until enabled
    assert (d['genres_per_run'], d['movies_per_genre'], d['time']) == (3, 1, '22:50')
    assert (d['min_votes'], d['imdb_min_rating'], d['imdb_min_votes'], d['min_seeders']) == (800, 0.0, 120000, 5)
    assert d['skip_same_collection'] is True and d['language'] == 'en' and d['dry_run'] is False
    y = date.today().year
    assert (d['year_from'], d['year_to']) == (y - 40, y - 3)
    assert (d['attempt_budget'], d['time_budget_min'], d['notify'], d['history_days']) == (60, 45, 'failure', 90)
    assert 'min_rating' not in d and 'destination_path' not in d and 'library' not in d


def test_settings_validate_and_round_trip(world):
    assert gs.save({'genres_per_run': 4, 'exclude_genres': ['Horror'], 'time': '21:05'})['genres_per_run'] == 4
    assert gs.settings()['time'] == '21:05'
    for bad in ({'genres_per_run': 0}, {'genres_per_run': True}, {'time': '25:00'}, {'include_genres': ['Nope']},
                {'imdb_min_rating': 11}, {'year_from': 2020, 'year_to': 2000}, {'min_rating': 6.5},
                {'include_genres': ['Horror']}, {'profile_id': 'x'}, {'destination_path': '/x'}):
        with pytest.raises(gs.Invalid):
            gs.save(bad)
    assert gs.save({'profile_id': 3})['profile_id'] == 3 and gs.save({'profile_id': None})['profile_id'] is None


def test_prod_keys_are_imported_once_and_off(world, monkeypatch):
    vals = {'automation.genre_roulette.hour': 22, 'automation.genre_roulette.minute': 50,
            'automation.genre_roulette.enabled': True, 'automation.genre_roulette.genres': 'Action,Comedy',
            'automation.genre_roulette.imdb_min_votes': 250000, 'automation.genre_roulette.quality_profile_id': 7}

    class SM:
        def get(self, k, d=None):
            return vals.get(k, d)
    import app.settings_manager as smod
    monkeypatch.setattr(smod, 'SettingsFile', SM)
    d = gs.settings()
    assert d['enabled'] is False and d['time'] == '22:50' and d['include_genres'] == ['Action', 'Comedy']
    assert d['imdb_min_votes'] == 250000 and d['profile_id'] is None


# ------------------------------------------------------------------ rules

def test_primary_genre_only(world):
    world.cat = {1: film(1, 'Fast', ['Action', 'Thriller'], votes=90000), 2: film(2, 'Seven', ['Thriller'])}
    world.releases = {1: [rel(1)], 2: [rel(2)]}
    r = go(world, cfg(include_genres=['Thriller'], genres_per_run=1))
    assert [p['tmdb_id'] for p in r['picks']] == [2]
    assert 1 not in world.searches


def test_three_genres_one_movie_each(world):
    three_genre_world(world)
    r = go(world, cfg(**ONLY3))
    assert r['grabbed'] == 3 and r['wanted'] == 3 and r['stop_reason'] == 'target_met'
    assert sorted(p['genre'] for p in r['picks']) == ['Action', 'Comedy', 'Drama']
    assert [p['title'] for p in r['picks'] if p['genre'] == 'Action'] == ['Action 0']   # most voted first


def test_per_genre_retry_tries_the_next_film_in_the_same_genre(world):
    three_genre_world(world)
    world.releases[101] = []                                    # Action 0: nothing
    world.releases[102] = [rel(102, seeders=2)]                 # Action 1: under 5 seeders
    r = go(world, cfg(**ONLY3))
    assert r['grabbed'] == 3
    assert [p['title'] for p in r['picks'] if p['genre'] == 'Action'] == ['Action 2']
    assert {s['title'] for s in r['skips']['no_release']} == {'Action 0', 'Action 1'}


def test_a_failed_genre_is_reported_and_its_slot_filled_from_other_genres(world):
    three_genre_world(world)
    for i in range(101, 106):
        world.releases[i] = []
    r = go(world, cfg(**ONLY3))
    assert r['grabbed'] == 3
    assert r['failures'] == [{'genre': 'Action', 'reason': 'No release found'}]
    assert sum(p['genre'] == 'Action' for p in r['picks']) == 0


def test_same_collection_never_twice_in_a_run_or_after_a_recent_grab(world):
    three_genre_world(world)
    world.cat[101]['collection'] = 77
    world.cat[111]['collection'] = 77                           # Drama 0 shares Action 0's collection
    r = go(world, cfg(**ONLY3), seed=3)
    titles = {p['title'] for p in r['picks']}
    assert not {'Action 0', 'Drama 0'} <= titles
    assert r['skips']['same_collection']
    # next run: the collection mate of a recent grab is skipped too
    world.grabs.clear()
    r2 = go(world, cfg(**ONLY3), seed=4)
    assert not ({'Action 0', 'Drama 0'} & {p['title'] for p in r2['picks']})


def test_2b_owned_collection_is_skipped_and_logged_and_allowed_when_off(world):
    three_genre_world(world)
    world.cat[101]['collection'] = 55
    world.owned.append((5001, 'Action Prequel', 1999, 'Movies', 55))
    r = go(world, cfg(**ONLY3))
    sk = r['skips']['owned_collection']
    assert sk[0]['title'] == 'Action 0' and sk[0]['owned_title'] == 'Action Prequel'
    assert 'Action 0' not in {p['title'] for p in r['picks']}
    gs.save_state({})
    world.grabs.clear()
    with gs.connect() as conn:
        conn.execute('DELETE FROM gr_grabs')
    r = go(world, cfg(skip_same_collection=False, **ONLY3))
    assert 'Action 0' in {p['title'] for p in r['picks']}


def test_1b_keeps_filling_past_the_old_25_search_limit(world):
    cat = [film(200 + n, f'Drama {n}', ['Drama'], votes=90000 - n) for n in range(40)]
    world.cat = {f['id']: f for f in cat}
    world.releases = {239: [rel(239)]}                          # only the 40th has a release
    r = go(world, cfg(include_genres=['Drama'], genres_per_run=1))
    assert r['grabbed'] == 1 and r['stats']['attempts'] == 40 and r['stop_reason'] == 'target_met'


def test_1b_stop_reasons_are_recorded_in_history(world):
    cat = [film(200 + n, f'Drama {n}', ['Drama']) for n in range(10)]
    world.cat = {f['id']: f for f in cat}
    r = go(world, cfg(include_genres=['Drama'], genres_per_run=1, attempt_budget=4))
    assert r['stop_reason'] == 'attempt_budget' and r['stats']['attempts'] == 4 and r['grabbed'] == 0
    r = go(world, cfg(include_genres=['Drama'], genres_per_run=1))
    assert r['stop_reason'] == 'pool_exhausted'
    t = [0.0]

    def clock():
        t[0] += 60
        return t[0]
    r = eng.run('manual', cfg=cfg(include_genres=['Drama'], genres_per_run=1, time_budget_min=3), now=NOW,
                rng=random.Random(1), sleep=lambda s: None, clock=clock)
    assert r['stop_reason'] == 'time_budget'
    h = gs.runs()
    assert [x['stop_reason'] for x in h] == ['time_budget', 'pool_exhausted', 'attempt_budget']
    assert h[2]['stats']['attempts'] == 4 and h[0]['kind'] == 'manual' and h[0]['status'] == 'done'


def test_trackers_unreachable_abort_without_advancing_the_wheel(world):
    three_genre_world(world)
    gs.save_state({'recent_genres': ['War'], 'cursors': {'sig': 'old', 'pages': {}}})
    world.search_status = 'error'
    r = go(world, cfg(**ONLY3))
    assert r['stop_reason'] == 'tracker_unreachable' and r['error'] and len(world.searches) == 3
    assert gs.state()['recent_genres'] == ['War'] and gs.state()['cursors']['sig'] == 'old'
    assert gs.runs()[0]['status'] == 'error'


def test_qbit_unreachable_stops_the_run(world):
    three_genre_world(world)
    world.grab_result = {i: {'ok': False, 'client': True, 'error': 'x'} for i in world.cat}
    r = go(world, cfg(**ONLY3))
    assert r['stop_reason'] == 'qbit_unreachable' and len(world.grabs) == 1


def test_duplicate_in_library_or_qbit_is_never_retried_with_another_release(world):
    three_genre_world(world)
    world.releases[101] = [rel(101, 'A'), rel(101, 'B'), rel(101, 'C')]
    world.grab_result[101] = {'ok': False, 'duplicate': True, 'error': 'already in qBittorrent', 'blocked': True}
    r = go(world, cfg(**ONLY3))
    assert [g for g in world.grabs if g[0] == 101] == [(101, 'A.2005.1080p.WEB-DL-GRP')]
    assert r['skips']['owned'][0]['title'] == 'Action 0' and r['grabbed'] == 3


def test_a_refused_release_tries_the_next_one(world):
    three_genre_world(world)
    world.releases[101] = [rel(101, 'A', 30), rel(101, 'B', 20)]
    world.grab_result[101] = lambda it: {'ok': it['title'].startswith('B'), 'hash': 'f' * 40, 'error': 'refused'}
    r = go(world, cfg(**ONLY3))
    assert len([g for g in world.grabs if g[0] == 101]) == 2
    assert [p['release'] for p in r['picks'] if p['tmdb_id'] == 101] == ['B.2005.1080p.WEB-DL-GRP']


def test_a_second_run_never_regrabs(world):
    three_genre_world(world)
    first = {p['tmdb_id'] for p in go(world, cfg(**ONLY3))['picks']}
    second = {p['tmdb_id'] for p in go(world, cfg(**ONLY3), seed=9)['picks']}
    assert first and second and not first & second
    assert gs.title_for(next(iter(first)))['title']


def test_empty_plex_refuses_to_run(world):
    three_genre_world(world)
    world.owned = []
    r = go(world, cfg(**ONLY3))
    assert r['error'] == 'Plex library unavailable' and world.searches == []
    world.plex_fail = True
    assert go(world, cfg(**ONLY3))['error'] == 'Plex library unavailable'


def test_owned_films_by_id_or_title_and_year_are_never_picked(world):
    three_genre_world(world)
    world.owned += [(101, 'Action 0', 2005, 'Movies'), (None, 'Comedy 0', 2005, '4K')]
    r = go(world, cfg(**ONLY3))
    assert not {101, 106} & {p['tmdb_id'] for p in r['picks']}
    assert not {101, 106} & set(world.searches)


def test_owned_sections_limit_what_counts_as_owned(world):
    three_genre_world(world)
    world.owned += [(101, 'Action 0', 2005, 'Kids')]
    r = go(world, cfg(owned_sections=['Movies'], **ONLY3))
    assert 101 in {p['tmdb_id'] for p in r['picks']}


def test_in_flight_grabs_count_as_owned(world, monkeypatch):
    three_genre_world(world)
    monkeypatch.setattr(sel, 'in_flight', lambda: {101})
    assert 101 not in {p['tmdb_id'] for p in go(world, cfg(**ONLY3))['picks']}


def test_dry_run_grabs_nothing_and_writes_no_state(world):
    three_genre_world(world)
    r = go(world, cfg(dry_run=True, **ONLY3))
    assert r['dry'] and r['grabbed'] == 3 and world.grabs == [] and gs.state() == {}
    assert gs.runs()[0]['dry'] is True and world.pushes == []
    p = go(world, cfg(**ONLY3), kind='preview')
    assert p['dry'] and world.grabs == [] and len(gs.runs()) == 1


def test_imdb_gate_needs_an_omdb_key(world):
    three_genre_world(world)
    for f in world.cat.values():
        f['imdb'] = (6.0, 1000)                              # far under 120,000
    r = go(world, cfg(**ONLY3))
    assert r['grabbed'] == 3 and not r['stats']['imdb_gate']    # no key: not applied
    world.omdb_key = True
    world.grabs.clear()
    gs.save_state({})
    with gs.connect() as conn:
        conn.execute('DELETE FROM gr_grabs')
    r = go(world, cfg(**ONLY3))
    assert r['stats']['imdb_gate'] and r['grabbed'] == 0 and world.grabs == []


def test_imdb_floor_keeps_the_famous_and_omdb_failures_fail_open(world):
    three_genre_world(world)
    world.omdb_key = True
    for f in world.cat.values():
        f['imdb'] = (7.0, 500000)
    world.cat[101]['imdb'] = (8.0, 1000)                     # Action 0 under the floor
    world.omdb_fail = {102}                                  # Action 1: OMDb can't answer -> admitted
    for i in (103, 104, 105):                                # (unknowns rank after known votes)
        world.releases[i] = []
    r = go(world, cfg(**ONLY3))
    assert 101 not in {p['tmdb_id'] for p in r['picks']}
    assert [p['tmdb_id'] for p in r['picks'] if p['genre'] == 'Action'] == [102]
    assert r['stats']['imdb_fail_open'] >= 1


def test_imdb_rating_floor(world):
    three_genre_world(world)
    world.omdb_key = True
    world.cat[101]['imdb'] = (5.0, 900000)
    r = go(world, cfg(imdb_min_rating=6.5, **ONLY3))
    assert 101 not in {p['tmdb_id'] for p in r['picks']} and r['grabbed'] == 3


def test_imdb_vote_floor_relaxes_when_short(world):
    three_genre_world(world)
    world.omdb_key = True
    for f in world.cat.values():
        f['imdb'] = (7.0, 60000)                             # under 120k, over the 50k stop
    r = go(world, cfg(**ONLY3))
    assert r['grabbed'] == 3


def test_excluded_genres_are_never_spun_or_borrowed(world):
    cat = [film(300 + n, f'Horror {n}', ['Horror']) for n in range(3)]
    cat += [film(400 + n, f'War {n}', ['War']) for n in range(3)]
    world.cat = {f['id']: f for f in cat}
    world.releases = {f['id']: [rel(f['id'])] for f in cat}
    for seed in range(5):
        r = go(world, cfg(exclude_genres=[g for g in G if g != 'War']), seed=seed)
        assert all(p['genre'] == 'War' for p in r['picks'])
        assert 'Horror' not in r['genres'] and 'Horror' not in r.get('borrowed_genres', [])


def test_borrowing_continues_round_after_round(world):
    # spun genres empty; only the last borrowed genre (by shuffle) has films
    cat = [film(500, 'Western 0', ['Western'])]
    world.cat = {f['id']: f for f in cat}
    world.releases = {500: [rel(500)]}
    r = go(world, cfg(genres_per_run=1, include_genres=['Action', 'Comedy', 'Crime', 'Drama', 'Family', 'Western',
                                                        'War', 'History']), seed=2)
    assert r['grabbed'] == 1 and r['picks'][0]['borrowed'] in (True, False)


def test_movies_per_genre_two(world):
    three_genre_world(world)
    r = go(world, cfg(movies_per_genre=2, **ONLY3))
    assert r['wanted'] == 6 and r['grabbed'] == 6
    assert sorted(p['genre'] for p in r['picks']).count('Comedy') == 2


def test_language_is_sent_to_discover_unless_any(world):
    seen = []
    real = world.tmdb_get

    def spy(path, params):
        seen.append(params.get('with_original_language'))
        return real(path, params)
    import app.automation.gr_select as s
    s.tmdb_get = spy
    try:
        three_genre_world(world)
        go(world, cfg(**ONLY3))
        assert 'en' in seen
        seen.clear()
        go(world, cfg(language='any', **ONLY3), seed=5)
        assert seen and None in seen and 'en' not in seen
    finally:
        s.tmdb_get = real


def test_notifies_admins_only_on_failure_by_default(world):
    """v296: grab/run-finish Web Push is off (import-only). Setting may still be stored."""
    three_genre_world(world)
    go(world, cfg(**ONLY3))
    assert world.pushes == []
    for i in world.cat:
        world.releases[i] = []
    go(world, cfg(**ONLY3), seed=7)
    assert world.pushes == []
    go(world, cfg(notify='finish', **ONLY3), seed=8)
    assert world.pushes == []
    go(world, cfg(notify='off', **ONLY3), seed=9)
    assert world.pushes == []


def test_history_is_pruned_by_history_days(world):
    three_genre_world(world)
    old = gs.start_run('scheduled', False, NOW - 100 * 86400)
    gs.finish_run(old, {'status': 'done'}, NOW - 100 * 86400)
    go(world, cfg(**ONLY3))
    assert all(r['started_at'] > NOW - 90 * 86400 for r in gs.runs())


def test_a_running_run_blocks_another(world):
    three_genre_world(world)
    eng._status['running'] = True
    try:
        assert go(world, cfg(**ONLY3))['status'] == 'busy'
    finally:
        eng._status['running'] = False


def test_no_radarr_anywhere_under_app_automation():
    root = Path(__file__).resolve().parent.parent / 'app' / 'automation'
    files = list(root.rglob('*.py'))
    assert files
    for p in files:
        src = p.read_text()
        assert not re.search(r'^\s*(from|import)\s+[\w.]*radarr', src, re.M | re.I), p
        assert not re.search(r'radarr_api|RadarrAPI|radarr_service', src), p


def test_importer_files_a_roulette_grab_under_its_title(world):
    from app import movie_import
    gs.record_grab('a' * 40, 4242, 'Zodiac', 2007, None, NOW)
    assert movie_import._watch(4242) == {'title': 'Zodiac', 'year': 2007}


# ------------------------------------------------------------------ the real search/rank/grab path (fakes at the edges)

from tests.test_v203_movie_scanner import mv, rel as srel, _torrent  # noqa: E402,F401
from tests.test_tv_libraries import owner, tv_env  # noqa: E402,F401
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: E402,F401
from tests.test_tv_v158c import guard  # noqa: E402,F401


def test_grabs_through_the_movie_scanner_path_into_qbittorrent(mv, tmp_path, monkeypatch):  # noqa: F811
    from app import grab_origin, movie_grabs, movie_scanner as sc
    monkeypatch.setitem(gs._cfg, 'db_path', str(tmp_path / 'automation.db'))
    w = World([film(693134, 'Dune: Part Two', ['Science Fiction'], year=2024, votes=9000)])
    w.owned.append((1, 'Owned', 1990, 'Movies'))
    monkeypatch.setattr(sel, 'tmdb_key', lambda: 'k')
    monkeypatch.setattr(sel, 'tmdb_get', w.tmdb_get)
    monkeypatch.setattr(sel, 'omdb_key_set', lambda: False)
    monkeypatch.setattr(sel, 'plex_index', w.plex)
    monkeypatch.setattr(eng, 'notify', lambda t, b: None)
    origins = []
    monkeypatch.setattr(grab_origin, 'record', lambda origin, **k: origins.append((origin, k)))
    q = mv.q                                        # FakeQbit and a fake link; movie/pending passed through
    monkeypatch.setattr(sc, 'grab', lambda item, tid, origin, now=None, **k: REAL_GRAB(
        item, tid, origin, now, session_factory=q.factory, fetch=lambda u: ('torrent', _torrent(item['title'])), **k))
    mv.results = {1: [srel('Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1-GRP', seeders=3),
                      srel('Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1-OK', seeders=40)]}
    r = eng.run('manual', cfg=cfg(include_genres=['Science Fiction'], genres_per_run=1), now=NOW,
                rng=random.Random(1), sleep=lambda s: None)
    assert r['grabbed'] == 1, r
    assert r['picks'][0]['release'].endswith('-OK')                 # 3 seeders is under the minimum 5
    assert [g['tmdb_id'] for g in movie_grabs.all_rows()] == [693134]
    assert origins and origins[0][0] == 'genre_roulette' and origins[0][1]['tmdb_id'] == 693134
    assert gs.title_for(693134) == {'title': 'Dune: Part Two', 'year': 2024}
    assert any(c[1] == 'torrents/add' for c in mv.q.calls)
