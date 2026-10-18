"""Reviewer blocker on dcb9cc1: Library paths TV selects are never Movie
destinations. Movies file exactly as before TV selected Library paths, for
every combination of explicit fallback, Movies paths and TV-selected paths."""
import itertools
import logging
import os

import pytest
from unittest import mock

from app import file_management as fm, library_selection, tv_settings
from tests.test_tv_libraries import _legacy, tv_env  # noqa: F401
from tests.test_file_management import store  # noqa: F401

INFO = {'resolution': '1080p', 'source': 'web'}
MOVIES = [{'id': 'p_0000a001', 'name': 'Movies', 'path': '/media/Movies'},
          {'id': 'p_0000a002', 'name': '4K Movies', 'path': '/media/4K'}]
TV = [{'id': 'p_0000b001', 'name': 'TV', 'path': '/media/TV'},
      {'id': 'p_0000b002', 'name': 'Anime', 'path': '/media/Anime'}]


def _before(n_movies, explicit):
    """What Movies picked before TV selection existed: only Movies paths in the store."""
    paths = MOVIES[:n_movies]
    data = {'paths': paths, 'rules': [], 'fallback_path_id': paths[-1]['id'] if explicit and paths else None}
    return fm.pick_destination(data, INFO, taken=set())


MATRIX = [(e, m, t, order) for e, m, t, order in itertools.product((False, True), (0, 1, 2), (0, 1, 2), ('tv_first', 'tv_last'))
          if not (e and m == 0)]


@pytest.mark.parametrize('explicit,n_movies,n_tv,order', MATRIX)
def test_movie_filing_matrix(tv_env, explicit, n_movies, n_tv, order):
    movies, tv = MOVIES[:n_movies], TV[:n_tv]
    paths = tv + movies if order == 'tv_first' else movies + tv
    fallback = movies[-1]['id'] if explicit else None
    # a TV path in a matching rule is never used either
    rules = [{'id': 'r_00000001', 'enabled': True, 'path_id': p['id'], 'released_within': None,
              'resolution': [], 'source': []} for p in tv]
    fm.save({**fm.load(), 'paths': paths, 'rules': rules, 'fallback_path_id': fallback})
    tv_settings.save({'libraries': [p['id'] for p in tv]})
    data = fm.load()
    got = fm.pick_destination(data, INFO)
    expect = _before(n_movies, explicit)
    assert (got and got['id']) == (expect and expect['id'])
    assert got is None or got['id'] not in {p['id'] for p in tv}
    assert {
        (False, 0): None, (False, 1): 'p_0000a001', (False, 2): None, (True, 1): 'p_0000a001', (True, 2): 'p_0000a002',
    }[(explicit, n_movies)] == (got and got['id'])
    fb = fm.effective_fallback(data)
    assert fb is None or fb['id'] not in {p['id'] for p in tv}


def test_a_tv_selected_explicit_fallback_is_not_used(tv_env):
    fm.save({**fm.load(), 'paths': MOVIES + TV[:1], 'fallback_path_id': TV[0]['id']})
    tv_settings.save({'libraries': [TV[0]['id']]})
    assert fm.pick_destination(fm.load(), INFO) is None          # 2 Movies paths, no Movies fallback
    tv_settings.save({'libraries': []})
    assert fm.pick_destination(fm.load(), INFO)['id'] == TV[0]['id']   # deselected: a Movies path again


def test_a_movies_rule_still_wins(tv_env):
    rule = {'id': 'r_00000001', 'enabled': True, 'path_id': MOVIES[1]['id'], 'released_within': None,
            'resolution': ['1080p'], 'source': []}
    fm.save({**fm.load(), 'paths': MOVIES + TV, 'rules': [rule], 'fallback_path_id': MOVIES[0]['id']})
    tv_settings.save({'libraries': [TV[0]['id'], TV[1]['id']]})
    assert fm.pick_destination(fm.load(), INFO)['id'] == MOVIES[1]['id']


def test_release_date_rules_on_tv_paths_dont_hold_movies(tv_env):
    rule = {'id': 'r_00000001', 'enabled': True, 'path_id': TV[0]['id'], 'released_within': {'n': 1, 'unit': 'years'},
            'resolution': [], 'source': []}
    fm.save({**fm.load(), 'paths': MOVIES[:1] + TV[:1], 'rules': [rule]})
    assert fm.needs_release_date(fm.load())
    tv_settings.save({'libraries': [TV[0]['id']]})
    assert not fm.needs_release_date(fm.load())


def test_claims_registry():
    assert library_selection.claimed_ids() is not None
    library_selection.claim('test-broken', lambda: (_ for _ in ()).throw(OSError('x')))
    try:
        assert 'test-broken' not in str(library_selection.claimed())
    finally:
        library_selection._claims.pop('test-broken')


def test_view_lists_who_uses_a_path(tv_env):
    fm.save({**fm.load(), 'paths': MOVIES[:1] + TV[:1]})
    tv_settings.save({'libraries': [TV[0]['id']]})
    assert [(p['id'], p['used_by']) for p in fm.view()['paths']] == [(MOVIES[0]['id'], []), (TV[0]['id'], ['tv'])]


# ---------------------------------------------------------------- the two repros, through migration

def test_repro1_no_movies_paths_and_one_tv_path(tv_env):
    _legacy(('t_0000000a', 'TV', tv_env / 'TV'))
    tv_settings.migrate()
    data = fm.load()
    assert [p['path'] for p in data['paths']] == [str(tv_env / 'TV')]
    assert fm.pick_destination(data, INFO) is None              # as before: no Movies path, no destination
    assert fm.effective_fallback(data) is None and data['fallback_path_id'] is None


def test_repro2_one_implicit_movies_path_and_one_tv_path(tv_env):
    fm.save({**fm.load(), 'paths': [{'id': 'p_0000a001', 'name': 'Movies', 'path': str(tv_env / 'Movies')}]})
    assert fm.pick_destination(fm.load(), INFO)['id'] == 'p_0000a001'        # implicit fallback
    _legacy(('t_0000000a', 'TV', tv_env / 'TV'))
    tv_settings.migrate()
    data = fm.load()
    assert len(data['paths']) == 2
    assert data['fallback_path_id'] == 'p_0000a001'                          # pinned
    assert fm.pick_destination(data, INFO)['id'] == 'p_0000a001'
    assert fm.effective_fallback(data)['id'] == 'p_0000a001'
    tv_settings.migrate()
    assert fm.load()['fallback_path_id'] == 'p_0000a001'


def test_migration_keeps_an_explicit_fallback_and_never_pins_with_two(tv_env):
    two = [{'id': 'p_0000a001', 'name': 'Movies', 'path': str(tv_env / 'Movies')},
           {'id': 'p_0000a002', 'name': 'Anime', 'path': str(tv_env / 'Anime')}]
    fm.save({**fm.load(), 'paths': two})
    _legacy(('t_0000000a', 'TV', tv_env / 'TV'))
    tv_settings.migrate()
    assert fm.load()['fallback_path_id'] is None
    fm.save({**fm.load(), 'paths': two[:1], 'fallback_path_id': None})
    tv_settings.save({'libraries': []})
    fm.save({**fm.load(), 'paths': two, 'fallback_path_id': 'p_0000a002'})
    _legacy(('t_0000000b', 'Kids', tv_env / 'Kids TV'))
    tv_settings.migrate()
    assert fm.load()['fallback_path_id'] == 'p_0000a002'


# ---------------------------------------------------------------- migration runs the checks, never blocks or writes

def test_migration_checks_without_writing_and_logs(tv_env, caplog):
    os.chmod(tv_env / 'Kids TV', 0o555)
    try:
        before = sorted(os.listdir(tv_env / 'Kids TV')), os.stat(tv_env / 'Kids TV').st_mtime_ns
        calls = []
        real_writable = fm.writable
        fm.writable = lambda p: calls.append(p) or real_writable(p)
        try:
            _legacy(('t_0000000a', 'Kids', tv_env / 'Kids TV'), ('t_0000000b', 'Etc', '/etc'))
            with caplog.at_level(logging.INFO, logger='app.tv_settings'):
                tv_settings.migrate()
        finally:
            fm.writable = real_writable
        assert calls == []                                              # no probe
        assert (sorted(os.listdir(tv_env / 'Kids TV')), os.stat(tv_env / 'Kids TV').st_mtime_ns) == before
        assert [p['path'] for p in fm.load()['paths']] == [str(tv_env / 'Kids TV'), '/etc']   # still added
        text = caplog.text
        assert "'Etc' added to Library paths (Not allowed)" in text
        if os.geteuid() != 0:
            assert "'Kids' added to Library paths (Can't write to this folder)" in text
    finally:
        os.chmod(tv_env / 'Kids TV', 0o755)


def test_check_library_path_without_probe_never_writes(tv_env, monkeypatch):
    monkeypatch.setattr(fm, 'writable', lambda p: pytest.fail('probe'))
    assert fm.check_library_path(str(tv_env / 'TV'), [], probe=False) == (str(tv_env / 'TV'), None)


def test_migration_pins_the_previous_fallback_before_adding_any_path(tv_env):
    """Tester B1: the pin is in place before any path is added."""
    movies = {'id': 'p_0000a001', 'name': 'Movies', 'path': str(tv_env / 'Movies')}
    fm.save({**fm.load(), 'paths': [movies]})
    seen = []
    real = fm.check_library_path
    def spy(raw, others, probe=True):
        seen.append(fm_pin())          # the in-memory store at the first add
        return real(raw, others, probe=probe)
    lib_ref = {}
    real_load = fm.load
    def load():
        lib_ref['lib'] = real_load()
        return lib_ref['lib']
    fm_pin = lambda: lib_ref['lib']['fallback_path_id']
    _legacy(('t_0000000a', 'TV', tv_env / 'TV'), ('t_0000000b', 'Kids', tv_env / 'Kids TV'))
    with mock.patch.object(fm, 'check_library_path', spy), mock.patch.object(fm, 'load', load):
        tv_settings.migrate()
    assert seen == ['p_0000a001', 'p_0000a001']
    data = fm.load()
    assert data['fallback_path_id'] == 'p_0000a001' and len(data['paths']) == 3
    assert fm.pick_destination(data, INFO)['id'] == 'p_0000a001'


def test_migration_never_pins_a_path_tv_takes(tv_env):
    movies = {'id': 'p_0000a001', 'name': 'Movies', 'path': str(tv_env / 'Movies')}
    fm.save({**fm.load(), 'paths': [movies]})
    _legacy(('t_0000000a', 'Shared', tv_env / 'Movies'), ('t_0000000b', 'TV', tv_env / 'TV'))
    tv_settings.migrate()
    data = fm.load()
    assert data['fallback_path_id'] is None and len(data['paths']) == 2
    assert 'p_0000a001' in tv_settings.selected_ids()


def test_migration_without_adds_does_not_pin_or_write(tv_env):
    movies = {'id': 'p_0000a001', 'name': 'Movies', 'path': str(tv_env / 'Movies')}
    fm.save({**fm.load(), 'paths': [movies]})
    before = fm.store_path().read_bytes()
    _legacy(('t_0000000a', 'Movies too', tv_env / 'Movies'))
    tv_settings.migrate()
    assert fm.load()['fallback_path_id'] is None
    assert fm.store_path().read_bytes() == before
