"""QA (Luma Tester) for cursor/tv-phase1 f0db05d: Movies filing across the
TV paths -> Library paths migration (B1 demos and :5002-like configs), and
what still moves on legacy data where one folder was both. Strict xfails mark
open items (drop the marker with the fix)."""
from datetime import date

import pytest

from app import file_management as fm, tv_settings
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_tv_libraries import _legacy, owner, tv_env, _add, _put  # noqa: F401

TODAY = date(2026, 10, 17)
RULE = {'id': 'r_5002a001', 'enabled': True, 'path_id': 'p_c48f1d47', 'released_within': {'n': 1, 'unit': 'years'},
        'resolution': ['1080p', '720p', 'sd'], 'source': []}
SAMPLES = [{'resolution': '1080p', 'source': 'web', 'release_date': date(2026, 6, 1)},
           {'resolution': 'sd', 'source': None, 'release_date': date(2025, 11, 1)},
           {'resolution': '2160p', 'source': 'remux', 'release_date': date(2026, 9, 1)},
           {'resolution': '1080p', 'source': 'bluray', 'release_date': date(2019, 5, 5)},
           {'resolution': '1080p', 'source': 'web'}]


def _where(taken=None):
    data = fm.load()
    return [(fm.pick_destination(data, i, today=TODAY, taken=taken) or {}).get('path') for i in SAMPLES]


def _setup(env, case):
    p = {n: str(env / n) for n in ('TV', 'Movies', 'Anime', 'Kids TV')}
    if case == 'tv_only':
        _legacy(('t_0badf00d', 'TV', p['TV']))
    elif case == 'one_movies_no_fallback':
        fm.save({**fm.load(), 'paths': [{'id': 'p_11111111', 'name': 'Movies', 'path': p['Movies']}]})
        _legacy(('t_0badf00d', 'TV', p['TV']))
    elif case == 'one_movies_explicit_fallback':
        fm.save({**fm.load(), 'paths': [{'id': 'p_11111111', 'name': 'Movies', 'path': p['Movies']}], 'fallback_path_id': 'p_11111111'})
        _legacy(('t_0badf00d', 'TV', p['TV']))
    elif case in ('5002', '5002_legacy_tv', '5002_legacy_tv_and_new'):
        fm.save({**fm.load(), 'paths': [{'id': 'p_48f684d2', 'name': 'TV', 'path': p['TV']},
                                        {'id': 'p_81f2cd3e', 'name': 'Movies', 'path': p['Movies']},
                                        {'id': 'p_c48f1d47', 'name': 'Recently Released', 'path': p['Kids TV']}],
                 'rules': [RULE], 'fallback_path_id': 'p_81f2cd3e'})
        if case == '5002_legacy_tv':
            _legacy(('t_0badf00d', 'TV', p['TV']))
        elif case == '5002_legacy_tv_and_new':
            _legacy(('t_0badf00d', 'TV', p['TV']), ('t_0badf00e', 'Anime', p['Anime']))


@pytest.mark.parametrize('case', ['tv_only', 'one_movies_no_fallback', 'one_movies_explicit_fallback',
                                  '5002', '5002_legacy_tv', '5002_legacy_tv_and_new'])
def test_movie_destination_unchanged_by_migration_and_a_second_boot(tv_env, case):  # noqa: F811
    _setup(tv_env, case)
    before = _where(taken=set())                         # b0d63f8: no TV selection existed
    tv_settings.migrate()
    after = _where()
    assert after == before
    raw = fm.store_path().read_bytes() if fm.store_path().exists() else None
    assert tv_settings.migrate() == {}
    assert (fm.store_path().read_bytes() if fm.store_path().exists() else None) == raw
    assert _where() == before
    tv = str(tv_env / 'TV')
    assert all(d != tv for d in after)                   # never into the TV folder


def test_5002_selecting_tv_keeps_filing_and_refuses_movies_paths(owner, tv_env):  # noqa: F811
    _setup(tv_env, '5002')
    before = _where(taken=set())
    assert _put(owner, ['p_48f684d2']).status_code == 200
    assert _where() == before
    for pid in ('p_81f2cd3e', 'p_c48f1d47'):
        r = _put(owner, ['p_48f684d2', pid])
        assert r.status_code == 409 and r.get_json()['errors']['selected'] == 'This path is used by Movies'
    assert tv_settings.selected_ids() == ['p_48f684d2'] and _where() == before


@pytest.mark.xfail(strict=True, reason='QA f0db05d M30: legacy data where a TV path is the Movies explicit fallback: '
                                       'after migration Movies silently file into ANOTHER Library path (the implicit '
                                       'single remaining one) that the owner never chose')
def test_a_tv_taken_explicit_fallback_never_redirects_movies_elsewhere(tv_env):  # noqa: F811
    fm.save({**fm.load(), 'paths': [{'id': 'p_33333333', 'name': 'Movies', 'path': str(tv_env / 'Movies')},
                                    {'id': 'p_44444444', 'name': 'Anime', 'path': str(tv_env / 'Anime')}],
             'fallback_path_id': 'p_33333333'})
    _legacy(('t_0badf00d', 'Films', tv_env / 'Movies'))
    before = _where(taken=set())
    tv_settings.migrate()
    after = _where()
    assert all(a is None or a == b for a, b in zip(after, before)), after
