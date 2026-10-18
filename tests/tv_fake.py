"""A fake TMDB for TV scan tests (patches app.tv_tmdb's calls)."""
import pytest

from app import tv_tmdb

SHOWS = {
    95396: {'name': 'Severance', 'first_air_date': '2022-02-17', 'tvdb': 371980, 'imdb': 'tt11280740',
            'seasons': {1: 9, 2: 10}},
    2316: {'name': 'The Office', 'first_air_date': '2005-03-24', 'tvdb': 73244, 'imdb': 'tt0386676',
           'seasons': {1: 6, 2: 22}},
    70523: {'name': 'Dark', 'first_air_date': '2017-12-01', 'tvdb': 334824, 'imdb': 'tt5753856', 'seasons': {1: 10}},
    63770: {'name': 'Taskmaster', 'first_air_date': '2015-07-28', 'tvdb': 302001, 'imdb': 'tt4934214',
            'seasons': {1: 6}},
    1001: {'name': 'Mystery Show', 'first_air_date': '2015-01-01', 'seasons': {1: 3}},
    1002: {'name': 'Mystery Show', 'first_air_date': '2019-05-05', 'seasons': {1: 3}},
    4000: {'name': 'The Daily Show', 'first_air_date': '1996-07-22', 'seasons': {2024: 0}, 'daily': ['2024-10-03']},
    5000: {'name': 'Bluey', 'first_air_date': '2018-10-01', 'seasons': {1: 52}},
    5001: {'name': 'Bluey', 'first_air_date': '2007-01-01', 'seasons': {1: 2}},
}


def brief(tid):
    s = SHOWS[tid]
    return {'tmdb_id': tid, 'title': s['name'], 'original_title': s['name'], 'year': int(s['first_air_date'][:4]),
            'poster_path': f'/p{tid}.jpg', 'overview': ''}


class FakeTmdb:
    def __init__(self):
        self.calls = []
        self.fail = None

    def _chk(self, *call):
        self.calls.append(call)
        if self.fail:
            raise tv_tmdb.TmdbError(self.fail)

    def search(self, title, year=None):
        self._chk('search', title, year)
        from app.tv_match import normalize
        q = normalize(title)
        out = [brief(t) for t, s in SHOWS.items() if q and q in normalize(s['name'])]
        if year:
            out = [b for b in out if b['year'] == year]
        return out

    def find(self, ext, source):
        self._chk('find', ext, source)
        key = 'tvdb' if source == 'tvdb_id' else 'imdb'
        return [brief(t) for t, s in SHOWS.items() if str(s.get(key)) == str(ext)]

    def show(self, tid, seasons=None):
        self._chk('show', tid)
        s = SHOWS.get(int(tid))
        if not s:
            return None
        return {'id': int(tid), 'name': s['name'], 'original_name': s['name'], 'first_air_date': s['first_air_date'],
                'poster_path': f'/p{tid}.jpg', 'status': 'Returning Series', 'networks': [{'name': 'Net'}],
                'external_ids': {'tvdb_id': s.get('tvdb'), 'imdb_id': s.get('imdb')},
                'alternative_titles': {'results': []},
                'number_of_seasons': len([n for n in s['seasons'] if n > 0]),
                'seasons': [{'season_number': n, 'episode_count': c} for n, c in s['seasons'].items()]}

    def season(self, tid, n):
        self._chk('season', tid, n)
        s = SHOWS[int(tid)]
        if s.get('daily'):
            return {'name': f'Season {n}', 'episodes': [{'episode_number': i + 1, 'id': i + 1, 'name': f'D{i}',
                                                         'air_date': d} for i, d in enumerate(s['daily'])]}
        titles = s.get('titles', {}).get(n, {})
        return {'name': f'Season {n}', 'episodes': [{'episode_number': e, 'id': tid * 1000 + n * 100 + e,
                                                     'name': titles.get(e, f'Episode {e}'),
                                                     'air_date': s.get('air', {}).get((n, e), '2022-01-01')}
                                                    for e in range(1, s['seasons'][n] + 1)]}


    def seasons(self, tid, numbers):
        return {n: self.season(tid, n) for n in numbers}

    def episode_groups(self, tid):
        self._chk('episode_groups', tid)
        return [{'id': g, 'name': d['name'], 'type': d['type']} for g, d in SHOWS.get(int(tid), {}).get('groups', {}).items()]

    def episode_group(self, gid):
        self._chk('episode_group', gid)
        for s in SHOWS.values():
            if gid in s.get('groups', {}):
                d = s['groups'][gid]
                return {'id': gid, 'name': d['name'], 'type': d['type'],
                        'groups': [{'name': f'Season {n}', 'order': n,
                                    'episodes': [{'id': i, 'order': k} for k, i in enumerate(ids)]}
                                   for n, ids in d['seasons'].items()]}
        return None


@pytest.fixture
def fake_tmdb(monkeypatch):
    f = FakeTmdb()
    for name in ('search', 'find', 'show', 'season', 'seasons', 'episode_groups', 'episode_group'):
        monkeypatch.setattr(tv_tmdb, name, getattr(f, name))
    return f
