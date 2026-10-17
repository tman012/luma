"""Fixture from Taylor's real :5002 config (read-only, from PM): three Library
paths (TV, Movies, Recently Released), one rule (released within a year,
1080p/720p/SD -> Recently Released), explicit fallback Movies; no legacy TV
paths, no tv_settings.json, empty tv.db.

Migration must be a no-op; after TV selects the TV path, Movies filing is
identical to before and the TV path is never a Movie destination."""
import json
from datetime import date

import pytest

from app import file_management as fm, tv_settings, tv_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_tv_libraries import tv_env  # noqa: F401

TV_ID, MOVIES_ID, RECENT_ID = 'p_48f684d2', 'p_81f2cd3e', 'p_c48f1d47'
CONFIG = {
    'version': 1,
    'paths': [{'id': TV_ID, 'name': 'TV', 'path': '/media/Video/TV'},
              {'id': MOVIES_ID, 'name': 'Movies', 'path': '/media/Video/Movies'},
              {'id': RECENT_ID, 'name': 'Recently Released', 'path': '/media/Video/Recently Released'}],
    'rules': [{'id': 'r_5002a001', 'enabled': True, 'path_id': RECENT_ID,
               'released_within': {'n': 1, 'unit': 'years'}, 'resolution': ['1080p', '720p', 'sd'], 'source': []}],
    'fallback_path_id': MOVIES_ID,
    'path_map': [], 'transfer': 'copy', 'jobs': {},
}
TODAY = date(2026, 10, 17)
SAMPLES = [
    ('new 1080p web', {'resolution': '1080p', 'source': 'web', 'release_date': date(2026, 6, 1)}),
    ('new 720p hdtv', {'resolution': '720p', 'source': 'hdtv', 'release_date': date(2026, 1, 2)}),
    ('new sd', {'resolution': 'sd', 'source': None, 'release_date': date(2025, 11, 1)}),
    ('new 2160p remux', {'resolution': '2160p', 'source': 'remux', 'release_date': date(2026, 9, 1)}),
    ('old 1080p bluray', {'resolution': '1080p', 'source': 'bluray', 'release_date': date(2019, 5, 5)}),
    ('just over a year', {'resolution': '1080p', 'source': 'web', 'release_date': date(2025, 10, 16)}),
    ('no release date', {'resolution': '1080p', 'source': 'web'}),
    ('unknown quality', {'resolution': None, 'source': None, 'release_date': date(2026, 3, 3)}),
]
EXPECT = {'new 1080p web': RECENT_ID, 'new 720p hdtv': RECENT_ID, 'new sd': RECENT_ID,
          'new 2160p remux': MOVIES_ID, 'old 1080p bluray': MOVIES_ID, 'just over a year': MOVIES_ID,
          'no release date': MOVIES_ID, 'unknown quality': MOVIES_ID}


@pytest.fixture
def taylor(tv_env):  # noqa: F811
    fm.store_path().parent.mkdir(parents=True, exist_ok=True)
    fm.store_path().write_text(json.dumps(CONFIG, indent=2))
    assert not tv_settings.store_path().exists()
    return tv_env


def _filing():
    data = fm.load()
    return {name: (fm.pick_destination(data, info, today=TODAY) or {}).get('id') for name, info in SAMPLES}


def _tv_db_rows():
    with tv_store.connect() as conn:
        return {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('series', 'unmatched', 'episode_files')}


def test_migration_is_a_no_op(taylor):
    raw = fm.store_path().read_bytes()
    assert tv_settings.migrate() == {}
    tv_settings.migrate_on_start()
    assert tv_settings.selected_ids() == []               # the migrating read
    assert tv_settings.libraries(migrate_first=True) == []
    tv_settings.view()
    assert fm.store_path().read_bytes() == raw            # byte-identical
    assert not tv_settings.store_path().exists()          # nothing created, nothing auto-selected
    data = fm.load()
    assert [p['id'] for p in data['paths']] == [TV_ID, MOVIES_ID, RECENT_ID]   # no duplicate TV path
    assert data['fallback_path_id'] == MOVIES_ID and data['rules'] == CONFIG['rules']
    assert _tv_db_rows() == {'series': 0, 'unmatched': 0, 'episode_files': 0}


def test_selecting_tv_leaves_movies_filing_unchanged(taylor, minimal_app):  # noqa: F811
    before = _filing()
    assert before == EXPECT
    owner = _client(minimal_app)
    r = owner.get('/api/settings/tv/libraries', environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json()['selected'] == []
    raw = fm.store_path().read_bytes()
    r = owner.put('/api/settings/tv/libraries', json={'selected': [TV_ID]}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json()['selected'] == [TV_ID]
    assert fm.store_path().read_bytes() == raw            # selecting never touches Library paths
    assert _filing() == before                            # identical filing
    data = fm.load()
    assert fm.effective_fallback(data)['id'] == MOVIES_ID
    assert all(fm.pick_destination(data, info, today=TODAY)['id'] != TV_ID for _, info in SAMPLES)
    # even with a matching rule or the fallback pointed at TV by hand, Movies never file there
    for hack in ({'fallback_path_id': TV_ID}, {'rules': [dict(CONFIG['rules'][0], path_id=TV_ID, released_within=None,
                                                                resolution=[])] + CONFIG['rules']}):
        d = {**data, **hack}
        assert all((fm.pick_destination(d, info, today=TODAY) or {}).get('id') != TV_ID for _, info in SAMPLES)
    # the Library paths view marks it for the Movies choices to leave out
    view = owner.get('/api/settings/library-paths', environ_base=REMOTE, headers=HDR).get_json()
    assert {p['id']: p['used_by'] for p in view['paths']} == {TV_ID: ['tv'], MOVIES_ID: [], RECENT_ID: []}
    assert view['fallback_path_id'] == MOVIES_ID and view['rules'] == CONFIG['rules']
    # deselecting: still identical
    owner.put('/api/settings/tv/libraries', json={'selected': []}, environ_base=REMOTE, headers=HDR)
    assert _filing() == before
