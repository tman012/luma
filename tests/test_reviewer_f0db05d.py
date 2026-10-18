"""Reviewer F1-F4 on cursor/tv-phase1 f0db05d: Movies' effective fallback is
pinned before anything moves it (a Library path added, a TV selection or
release), the implicit fallback counts as "used by Movies" (Tester M31), and
the migration leaves the only Movies path to Movies. Taylor's :5002 config
(explicit fallback) stays byte-identical."""
import json
from datetime import date

from app import file_management as fm, tv_settings
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_libraries import _add, _legacy, _put, owner, tv_env  # noqa: F401
from tests.test_taylor_5002_config import CONFIG, MOVIES_ID, TV_ID
from tests.test_home_rails import REMOTE

FM = '/api/settings/library-paths'
RULES = '/api/settings/library-rules'


def _movies_rule(c, pid):
    """Movies depend on the Library paths (a rule): only then is an implicit fallback "used by Movies" (PM, Oct 18)."""
    r = {'enabled': True, 'path_id': pid, 'released_within': {'n': 1, 'unit': 'years'}, 'resolution': ['1080p'], 'source': []}
    fm.save({**fm.load(), 'rules': [{**r, 'id': 'r_00000001'}]})
ITEM = {'resolution': '1080p', 'source': 'web'}


def _dest():
    p = fm.pick_destination(fm.load(), ITEM, today=date(2026, 10, 17))
    return (p or {}).get('id')


def test_f1_adding_a_path_pins_the_single_movies_path(owner, tv_env):  # noqa: F811
    movies = _add(owner, 'Movies', tv_env / 'Movies')
    _movies_rule(owner, movies)
    assert fm.load()['fallback_path_id'] is None and _dest() == movies      # implicit
    _add(owner, 'TV', tv_env / 'TV')
    assert fm.load()['fallback_path_id'] == movies and _dest() == movies    # still Movies, now explicit


def test_f1_the_first_path_pins_nothing(owner, tv_env):  # noqa: F811
    _add(owner, 'Movies', tv_env / 'Movies')
    assert fm.load()['fallback_path_id'] is None


def test_f1_an_explicit_fallback_is_left_alone_on_add(owner, tv_env):  # noqa: F811
    a = _add(owner, 'Movies', tv_env / 'Movies')
    b = _add(owner, 'Anime', tv_env / 'Anime')
    owner.put('/api/settings/library-rules', json={'rules': [], 'fallback_path_id': b}, environ_base=REMOTE, headers=HDR)
    _add(owner, 'TV', tv_env / 'TV')
    assert fm.load()['fallback_path_id'] == b and a != b


def test_f1_tv_select_pins_the_implicit_fallback(owner, tv_env):  # noqa: F811
    # Movies + TV (TV selected), Movies implicit: selecting another path keeps Movies' fallback
    fm.save({**fm.load(), 'paths': [{'id': 'p_00000a01', 'name': 'Movies', 'path': str(tv_env / 'Movies')},
                                    {'id': 'p_00000a02', 'name': 'TV', 'path': str(tv_env / 'TV')},
                                    {'id': 'p_00000a03', 'name': 'Anime', 'path': str(tv_env / 'Anime')}]})
    tv_settings.save({**tv_settings.load(), 'libraries': ['p_00000a02', 'p_00000a03']})
    _movies_rule(owner, 'p_00000a01')
    assert fm.load()['fallback_path_id'] is None and _dest() == 'p_00000a01'
    assert _put(owner, ['p_00000a02']).status_code == 200                  # release Anime
    assert fm.load()['fallback_path_id'] == 'p_00000a01' and _dest() == 'p_00000a01'


def test_f1_tv_deselect_pins_the_implicit_fallback(owner, tv_env):  # noqa: F811
    fm.save({**fm.load(), 'paths': [{'id': 'p_00000a01', 'name': 'Movies', 'path': str(tv_env / 'Movies')},
                                    {'id': 'p_00000a02', 'name': 'TV', 'path': str(tv_env / 'TV')}]})
    tv_settings.save({**tv_settings.load(), 'libraries': ['p_00000a02']})
    _movies_rule(owner, 'p_00000a01')
    assert _dest() == 'p_00000a01'
    assert _put(owner, []).status_code == 200
    # without the pin Movies would see two paths and no fallback: refused "no destination"
    assert fm.load()['fallback_path_id'] == 'p_00000a01' and _dest() == 'p_00000a01'


def test_f1_a_refused_selection_writes_nothing(owner, tv_env):  # noqa: F811
    only = _add(owner, 'Movies', tv_env / 'Movies')
    _movies_rule(owner, only)
    before = fm.store_path().read_bytes()
    assert _put(owner, [only]).status_code == 409
    assert fm.store_path().read_bytes() == before


def test_f2_the_implicit_fallback_is_used_by_movies(owner, tv_env):  # noqa: F811
    fm.save({**fm.load(), 'paths': [{'id': 'p_00000a01', 'name': 'Movies', 'path': str(tv_env / 'Movies')},
                                    {'id': 'p_00000a02', 'name': 'TV', 'path': str(tv_env / 'TV')}]})
    tv_settings.save({**tv_settings.load(), 'libraries': ['p_00000a02']})
    assert fm.movies_uses(fm.load()) == set()                 # no Movies rules: nothing depends on it
    _movies_rule(owner, 'p_00000a01')
    assert fm.movies_uses(fm.load()) == {'p_00000a01'}
    r = _put(owner, ['p_00000a02', 'p_00000a01'])
    assert r.status_code == 409 and r.get_json()['errors']['selected'] == 'This path is used by Movies'
    assert tv_settings.selected_ids() == ['p_00000a02']


def test_f2_without_a_fallback_two_free_paths_are_selectable(owner, tv_env):  # noqa: F811
    fm.save({**fm.load(), 'paths': [{'id': 'p_00000a01', 'name': 'TV', 'path': str(tv_env / 'TV')},
                                    {'id': 'p_00000a02', 'name': 'Anime', 'path': str(tv_env / 'Anime')}]})
    assert fm.movies_uses(fm.load()) == set()
    assert _put(owner, ['p_00000a01']).status_code == 200


def test_f4_a_legacy_tv_folder_equal_to_the_only_movies_path_stays_with_movies(tv_env):  # noqa: F811
    fm.save({**fm.load(), 'paths': [{'id': 'p_00000a01', 'name': 'Movies', 'path': str(tv_env / 'Movies')}]})
    _legacy(('t_0badf00d', 'Everything', tv_env / 'Movies'))
    assert _dest() == 'p_00000a01'
    tv_settings.migrate()
    assert tv_settings.selected_ids() == [] and _dest() == 'p_00000a01'


def test_f4_with_two_movies_paths_the_legacy_folder_is_still_selected(tv_env):  # noqa: F811
    fm.save({**fm.load(), 'paths': [{'id': 'p_00000a01', 'name': 'Movies', 'path': str(tv_env / 'Movies')},
                                    {'id': 'p_00000a02', 'name': 'TV', 'path': str(tv_env / 'TV')}]})
    _legacy(('t_0badf00d', 'TV', tv_env / 'TV'))
    tv_settings.migrate()
    assert tv_settings.selected_ids() == ['p_00000a02']


def test_f4_a_crash_rerun_still_selects_what_the_migration_added(tv_env, monkeypatch):  # noqa: F811
    _legacy(('t_0badf00d', 'TV', tv_env / 'TV'))
    real = tv_settings._move_rows
    monkeypatch.setattr(tv_settings, '_move_rows', lambda o, n: (_ for _ in ()).throw(OSError('disk')))
    try:
        tv_settings.migrate()
    except OSError:
        pass
    assert json.loads(tv_settings.store_path().read_text())['migration_added']
    monkeypatch.setattr(tv_settings, '_move_rows', real)
    tv_settings.migrate()
    assert tv_settings.selected_ids() == [fm.load()['paths'][0]['id']]
    assert 'migration_added' not in json.loads(tv_settings.store_path().read_text())


def test_taylor_config_stays_byte_identical_through_add_select_deselect(owner, tv_env):  # noqa: F811
    fm.save(json.loads(json.dumps(CONFIG)))
    raw = fm.store_path().read_bytes()
    tv_settings.migrate()
    assert fm.store_path().read_bytes() == raw
    assert _put(owner, [TV_ID]).status_code == 200 and fm.store_path().read_bytes() == raw
    assert _put(owner, []).status_code == 200 and fm.store_path().read_bytes() == raw
    r = _put(owner, [TV_ID, MOVIES_ID])
    assert r.status_code == 409 and fm.store_path().read_bytes() == raw


def test_f2_tv_only_the_lone_path_is_selectable_for_tv(owner, tv_env):  # noqa: F811
    """PM, Oct 18 (M31): no Movies rules and no Movies fallback, so the only Library path can be TV's."""
    only = _add(owner, 'TV', tv_env / 'TV')
    assert _put(owner, [only]).status_code == 200
    assert tv_settings.selected_ids() == [only] and fm.load()['fallback_path_id'] is None


def test_f2_tv_only_a_second_path_doesnt_pin_the_fallback(owner, tv_env):  # noqa: F811
    tv = _add(owner, 'TV', tv_env / 'TV')
    _add(owner, 'Anime', tv_env / 'Anime')
    assert fm.load()['fallback_path_id'] is None                 # never pinned to a TV path
    assert _put(owner, [tv]).status_code == 200 and fm.load()['fallback_path_id'] is None
