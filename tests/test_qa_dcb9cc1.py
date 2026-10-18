"""QA (Luma Tester) for cursor/tv-phase1 dcb9cc1: the TV paths -> Library paths
migration and the selector. Locks what works; strict xfails mark what QA found
(drop the marker with the fix)."""
import json
import os
import stat

import pytest

from app import file_management as fm, tv_settings, tv_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_tv_libraries import tv_env, owner, _add, _put, _get  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401


def _old_tv_paths(*recs):
    os.makedirs(os.path.dirname(tv_settings.store_path()), exist_ok=True)
    with open(tv_settings.store_path(), 'w') as f:
        json.dump({'version': 1, 'paths': [dict(id=i, name=n, path=str(p)) for i, n, p in recs],
                   'rules': [], 'fallback_path_id': None}, f)


def _seed_rows(pid):
    with tv_store.write() as conn:
        conn.execute("INSERT INTO series (id, tmdb_id, title, path_id, folder, added_at) VALUES (7, 3600, 'Fargo', ?, 'Fargo', 1.0)", (pid,))
        conn.execute("INSERT INTO unmatched (id, path_id, folder, ts) VALUES (3, ?, 'Zzqx', 1.0)", (pid,))
        conn.execute("INSERT INTO history (id, ts, series_id, event, detail) VALUES (11, 1.0, 7, 'rematched', '{}')")


def _rows():
    with tv_store.connect() as conn:
        return ([tuple(r) for r in conn.execute('SELECT id, tmdb_id, path_id, folder FROM series ORDER BY id')],
                [tuple(r) for r in conn.execute('SELECT id, path_id, folder FROM unmatched ORDER BY id')],
                [tuple(r) for r in conn.execute('SELECT id, series_id, event FROM history ORDER BY id')])


def test_migration_keeps_ids_and_history_and_is_idempotent(owner, tv_env):  # noqa: F811
    _old_tv_paths(('t_0000aaaa', 'TV', tv_env / 'TV'))
    _seed_rows('t_0000aaaa')
    m = tv_settings.migrate()
    new = m['t_0000aaaa']
    assert new.startswith('p_') and tv_settings.selected_ids() == [new]
    s, u, h = _rows()
    assert s == [(7, 3600, new, 'Fargo')] and u == [(3, new, 'Zzqx')] and h == [(11, 7, 'rematched')]
    raw_fm, raw_tv = open(fm.store_path()).read(), open(tv_settings.store_path()).read()
    assert tv_settings.migrate() == {}                                        # second boot: nothing
    assert open(fm.store_path()).read() == raw_fm and open(tv_settings.store_path()).read() == raw_tv
    assert _rows() == (s, u, h)


def test_migration_matches_a_library_path_through_a_symlink(owner, tv_env, tmp_path):  # noqa: F811
    link = tmp_path / 'tvlink'
    link.symlink_to(tv_env / 'TV')
    lib = _add(owner, 'Shows', link)
    _old_tv_paths(('t_0000aaaa', 'TV', tv_env / 'TV'))
    assert tv_settings.migrate() == {'t_0000aaaa': lib}
    assert [p['id'] for p in fm.load()['paths']] == [lib]                       # nothing added


@pytest.mark.parametrize('bad', [['../../etc'], ['/etc'], ['t_0000aaaa'], ['p_deadbeef'], 'p_x', [1], None])
def test_selection_only_takes_current_library_path_ids(owner, tv_env, bad):  # noqa: F811
    _add(owner, 'TV', tv_env / 'TV')
    r = _put(owner, bad)
    assert r.status_code == 400 and tv_settings.selected_ids() == []


def test_migration_does_not_make_the_tv_folder_the_movie_destination(owner, tv_env):  # noqa: F811
    _old_tv_paths(('t_0000aaaa', 'TV', tv_env / 'TV'))
    assert fm.effective_fallback(fm.load()) is None
    tv_settings.migrate()
    dest = fm.pick_destination(fm.load(), fm.classify('Heat.1995.1080p.BluRay.x264-QA'))
    assert dest is None or os.path.realpath(dest['path']) != os.path.realpath(str(tv_env / 'TV'))


def test_migration_keeps_a_single_movies_path_as_the_fallback(owner, tv_env):  # noqa: F811
    movies = _add(owner, 'Movies', tv_env / 'Movies')
    assert fm.effective_fallback(fm.load())['id'] == movies
    _old_tv_paths(('t_0000aaaa', 'TV', tv_env / 'TV'))
    tv_settings.migrate()
    fb = fm.effective_fallback(fm.load())
    assert fb is not None and fb['id'] == movies


@pytest.mark.skipif(os.geteuid() == 0, reason='root ignores the read-only bit')
def test_a_read_only_tv_folder_can_be_offered_to_tv(owner, tv_env):  # noqa: F811
    ro = tv_env / 'Kids TV'
    os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
    try:
        r = owner.post('/api/settings/library-paths', json={'name': 'Kids TV', 'path': str(ro)},
                       environ_base=REMOTE, headers=HDR)
        assert r.status_code == 201, r.get_json()
    finally:
        os.chmod(ro, stat.S_IRWXU)
