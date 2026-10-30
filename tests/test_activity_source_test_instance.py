"""luma-test Activity source badges: test-native grab rows (tv.db grabs.origin, movies.db movie_grabs +
automation.db ttv_log / antic_log) give the real origin, never Radarr/Sonarr; unknown rows stay Other."""
import sqlite3
from contextlib import contextmanager

import pytest

from app import grab_origin, instance, movie_grabs, tv_store
from app.automation import gr_store

H = {k: (c * 40) for k, c in (('man', 'a'), ('auto', 'b'), ('ttv', 'c'), ('antic', 'd'), ('gr', 'e'),
                               ('mov', 'f'), ('old', '1'), ('mman', '2'))}
T = 1_000_000.0


def _db(path, sql):
    c = sqlite3.connect(path)
    c.executescript(sql)
    c.commit()
    c.close()

    @contextmanager
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    return connect


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(grab_origin, '_data_dir', lambda: tmp_path)
    monkeypatch.setattr(instance, 'is_test', lambda: True)
    monkeypatch.setattr(tv_store, 'connect', _db(str(tmp_path / 'tv.db'), f"""
        CREATE TABLE grabs (hash TEXT PRIMARY KEY, series_id INTEGER, origin TEXT, grabbed_at REAL);
        INSERT INTO grabs VALUES ('{H['man']}', 1, 'manual', {T}), ('{H['auto']}', 2, 'auto', {T}),
                                 ('{H['ttv']}', 3, 'auto', {T}), ('{H['antic']}', 4, 'auto', {T});"""))
    monkeypatch.setattr(gr_store, 'connect', _db(str(tmp_path / 'automation.db'), f"""
        CREATE TABLE ttv_log (series_id INTEGER, tmdb_id INTEGER, added_at REAL);
        CREATE TABLE antic_log (series_id INTEGER, tmdb_id INTEGER, added_at REAL, kind TEXT);
        INSERT INTO ttv_log VALUES (3, 30, {T - 3600});
        INSERT INTO antic_log VALUES (4, 40, {T - 7200}, 'shows'), (NULL, 77, {T - 600}, 'movies');"""))
    rows = {H['mov']: {'tmdb_id': 77, 'grabbed_at': T}, H['old']: {'tmdb_id': 88, 'grabbed_at': T},
            H['gr']: {'tmdb_id': 99, 'grabbed_at': T}}
    monkeypatch.setattr(movie_grabs, 'by_hash', lambda h: rows.get(h))

    def no_arr(*a, **k):
        raise AssertionError('Radarr/Sonarr must never be asked on luma-test')
    monkeypatch.setattr(grab_origin, '_arr_context', no_arr)
    monkeypatch.setattr(grab_origin, '_arr_get', no_arr)
    grab_origin._cache.clear()
    yield
    grab_origin._cache.clear()


class _S:
    def get(self, k, d=None):
        return 'http://radarr:7878' if k.endswith('.url') else ('key' if k.endswith('api_key') else d)


def test_mixed_sources_on_test(env):
    grab_origin.record('genre_roulette', info_hash=H['gr'], tmdb_id=99, media_type='movie')
    grab_origin.record('manual', info_hash=H['mman'], tmdb_id=55, media_type='movie', hash_only=True)
    items = [{'torrent_hash': h, 'completed_timestamp': T + 900, 'tmdb_id': None} for h in H.values()]
    got = {k: o['origin_label'] for k, o in zip(H, grab_origin.resolve(items, _S()))}
    assert got == {'man': 'Manual', 'auto': 'TV Auto', 'ttv': 'Trending TV', 'antic': 'Anticipated',
                   'gr': 'Genre Roulette', 'mov': 'Anticipated', 'old': 'Other', 'mman': 'Manual'}


def test_labels_short_american():
    assert grab_origin.LABELS['tv_auto'] == 'TV Auto'
    assert all(len(v) <= 20 for v in grab_origin.LABELS.values())


def test_manual_movie_grab_records_origin():
    import inspect
    from app import movie_scanner
    src = inspect.getsource(movie_scanner.grab)
    assert "grab_origin.record('manual', info_hash=h" in src and "if origin == 'manual'" in src
