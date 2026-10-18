"""v175 (Ops on c1f337b live; Tester).

(A) The tracker search migration is idempotent: it runs whenever ANY legacy key is present
    (indexer.movie_categories/max_age_days/min_seeders/limit, or tracker_search.max_age_days/
    min_seeders/limit), backs settings.json up first, strips them, logs once at INFO; a clean file is
    a no-op (no backup, no log, no rewrite). Shapes: 1b4ea74 (prod), c669f76 (v173), c1f337b (v174, both
    upgrade routes), clean.
(C) SettingsManager.update() refuses an unknown top-level key (e.g. a literal
    'tracker_search.movie_categories'); PUT /api/settings answers 400 and stores nothing."""
import json
import logging
import os
import stat

import pytest

import app.main
from app.settings_manager import SettingsManager
from tests.test_trackers_api import ORIGIN, store  # noqa: F401
from tests.test_v174_tracker_search_ff8 import _client, keep_setting, users  # noqa: F401

MIG = 'Migrated tracker search settings'
CONN = {'provider': 'prowlarr', 'base_url': 'http://192.168.1.30:9696', 'api_key': 'k' * 32}
GONE = ('max_age_days', 'min_seeders', 'limit')

SHAPES = {
    # prod: the four under indexer, no tracker_search
    '1b4ea74': {'indexer': {**CONN, 'movie_categories': '2000,2040', 'max_age_days': 30, 'min_seeders': 1, 'limit': 100}},
    # v173 moved all four under tracker_search
    'c669f76': {'indexer': dict(CONN), 'tracker_search': {'movie_categories': '2000,2040', 'max_age_days': 30,
                                                          'min_seeders': 1, 'limit': 100}},
    # v174 after c669f76: the bug, the three extras never stripped
    'c1f337b-from-c669f76': {'indexer': dict(CONN), 'tracker_search': {'movie_categories': '2000,2040', 'max_age_days': 45,
                                                                       'min_seeders': 3, 'limit': 250}},
    # v174 straight from prod: categories moved, the three left under indexer
    'c1f337b-from-1b4ea74': {'indexer': {**CONN, 'max_age_days': 30, 'min_seeders': 1, 'limit': 100},
                             'tracker_search': {'movie_categories': '2000,2040'}},
}


def _recs(caplog):
    return [r for r in caplog.records if r.name == 'app.settings_manager' and r.getMessage().startswith(MIG)]


def _baks(tmp_path):
    return sorted(tmp_path.glob('settings.json.bak-*'))


@pytest.mark.parametrize('shape', sorted(SHAPES))
def test_every_legacy_shape_is_backed_up_stripped_and_logged_once(tmp_path, caplog, shape):
    caplog.set_level(logging.INFO, logger='app.settings_manager')
    p = tmp_path / 'settings.json'
    p.write_text(json.dumps(SHAPES[shape]))
    raw = p.read_bytes()
    sm = SettingsManager(str(p))
    baks = _baks(tmp_path)
    assert len(baks) == 1 and baks[0].read_bytes() == raw                 # backed up first, as it was
    assert stat.S_IMODE(os.stat(baks[0]).st_mode) == 0o600
    saved = json.loads(p.read_text())
    assert saved['tracker_search'] == {'movie_categories': '2000,2040'}   # nothing lost, nothing extra
    for k in GONE + ('movie_categories',):
        assert k not in saved['indexer']
    for k, v in CONN.items():
        assert saved['indexer'][k] == v                                   # the deprecated connection stays
    assert sm.get('tracker_search.movie_categories') == '2000,2040'
    recs = _recs(caplog)
    assert len(recs) == 1 and recs[0].levelno == logging.INFO
    # idempotent: a restart (or a second manager) finds it clean: no backup, no log, no rewrite
    before = p.read_bytes()
    SettingsManager(str(p))
    SettingsManager(str(p))
    assert len(_baks(tmp_path)) == 1 and len(_recs(caplog)) == 1 and p.read_bytes() == before


def test_the_log_line_names_what_changed(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger='app.settings_manager')
    p = tmp_path / 'settings.json'
    p.write_text(json.dumps(SHAPES['c1f337b-from-c669f76']))
    SettingsManager(str(p))
    msg = _recs(caplog)[0].getMessage()
    assert 'nothing moved' in msg
    for k in GONE:
        assert f'tracker_search.{k}' in msg
    assert '45' not in msg and '250' not in msg                          # names only, never values


def test_a_value_already_under_tracker_search_wins(tmp_path):
    p = tmp_path / 'settings.json'
    p.write_text(json.dumps({'indexer': {'movie_categories': '5000', 'limit': 9}, 'tracker_search': {'movie_categories': '2010'}}))
    SettingsManager(str(p))
    saved = json.loads(p.read_text())
    assert saved['tracker_search'] == {'movie_categories': '2010'} and 'movie_categories' not in saved['indexer']


@pytest.mark.parametrize('clean', [
    {'indexer': dict(CONN), 'tracker_search': {'movie_categories': '2000,2040'}},
    {'tracker_search': {'movie_categories': '2000'}},
    {'plex': {'url': 'http://plex:32400'}},
])
def test_a_clean_file_is_a_no_op(tmp_path, caplog, clean):
    caplog.set_level(logging.INFO, logger='app.settings_manager')
    p = tmp_path / 'settings.json'
    p.write_text(json.dumps(clean))
    SettingsManager(str(p))                     # a hand-made file: other first-load migrations may run once
    assert _recs(caplog) == []                  # ...but never this one
    before, baks = p.read_bytes(), _baks(tmp_path)
    SettingsManager(str(p))
    assert _recs(caplog) == [] and _baks(tmp_path) == baks and p.read_bytes() == before


def test_a_fresh_install_is_a_no_op(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger='app.settings_manager')
    SettingsManager(str(tmp_path / 'settings.json'))
    SettingsManager(str(tmp_path / 'settings.json'))
    assert _recs(caplog) == [] and _baks(tmp_path) == []


# ------------------------------------------------------------------ (C) unknown top-level keys

@pytest.mark.parametrize('bad', [
    {'tracker_search.movie_categories': '2040'},
    {'bogus': 1},
    {'plex': {'url': 'http://plex:32400'}, 'Plex': {}},
    {'_migrations': {}},
])
def test_update_refuses_unknown_top_level_keys(tmp_path, bad):
    p = tmp_path / 'settings.json'
    sm = SettingsManager(str(p))
    before = p.read_bytes()
    ok, errors = sm.update(bad)
    assert ok is False and errors and all(e.startswith('Unknown setting: ') for e in errors)
    assert p.read_bytes() == before and sm.get('plex.url') != 'http://plex:32400'
    assert 'tracker_search.movie_categories' not in sm.get_all()


def test_update_still_takes_known_keys(tmp_path):
    sm = SettingsManager(str(tmp_path / 'settings.json'))
    ok, errors = sm.update({'tracker_search': {'movie_categories': '2040'}})
    assert ok, errors
    assert sm.get('tracker_search.movie_categories') == '2040'


def test_put_api_settings_with_a_dotted_key_is_400(test_app, users, keep_setting):  # noqa: F811
    before = app.main.settings.get('tracker_search.movie_categories')
    c = _client(test_app, users['owner'])
    r = c.put('/api/settings', data=json.dumps({'tracker_search.movie_categories': '2040'}),
              content_type='application/json', headers=ORIGIN)
    assert r.status_code == 400
    assert r.get_json()['errors'] == ['Unknown setting: tracker_search.movie_categories']
    assert app.main.settings.get('tracker_search.movie_categories') == before
    assert 'tracker_search.movie_categories' not in app.main.settings.get_all()


@pytest.mark.parametrize('shape', sorted(SHAPES))
def test_the_legacy_keys_are_gone_from_get_api_settings(test_app, users, tmp_path, monkeypatch, shape):  # noqa: F811
    p = tmp_path / 'settings.json'
    p.write_text(json.dumps(SHAPES[shape]))
    monkeypatch.setattr(app.main, 'settings', SettingsManager(str(p)))
    r = _client(test_app, users['owner']).get('/api/settings')
    assert r.status_code == 200
    body = r.get_json()
    assert body['tracker_search'] == {'movie_categories': '2000,2040'}
    for k in GONE + ('movie_categories',):
        assert k not in body['indexer'], k
    assert body['indexer']['base_url'] == CONN['base_url'] and body['indexer']['api_key'] != CONN['api_key']   # masked
