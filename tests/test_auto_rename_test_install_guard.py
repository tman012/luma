"""Production's auto-rename monitor never picks up the test install's grabs
in a shared qBittorrent (qBittorrent tag / category LUMA_TEST_QBIT_TAG,
default 'luma-test'), and nothing else about its selection changes."""

import importlib
import os
import sys
from unittest import mock

import pytest

from app import qbit_owner

H_PROD = '1' * 40
H_TEST = '2' * 40


@pytest.fixture
def renamer(tmp_path, monkeypatch):
    """A real AutoRenamer whose data files live in tmp_path (the module makes
    /app/data at import, so that import is sandboxed)."""
    monkeypatch.delenv('LUMA_TEST_QBIT_TAG', raising=False)
    real_makedirs, real_exists = os.makedirs, os.path.exists
    with mock.patch('os.makedirs', lambda p, *a, **k: None if str(p).startswith('/app') else real_makedirs(p, *a, **k)), \
            mock.patch('os.path.exists', lambda p: False if str(p).startswith('/app') else real_exists(p)):
        sys.modules.pop('app.auto_rename', None)
        ar = importlib.import_module('app.auto_rename')
    r = ar.AutoRenamer.__new__(ar.AutoRenamer)
    r.monitor_file = str(tmp_path / 'download_monitor.json')
    r.history_file = str(tmp_path / 'download_history.json')
    r.copy_progress_file = str(tmp_path / 'copy_progress.json')
    r.radarr_processed_file = str(tmp_path / 'radarr_processed.json')
    r.tracked_downloads, r.history, r.radarr_processed = {}, [], set()
    r.processed = []
    monkeypatch.setattr(r, 'process_completed_torrent', lambda h, info: r.processed.append(h) or True, raising=False)
    yield ar, r
    sys.modules.pop('app.auto_rename', None)


def _t(h, tags='', category='luma', progress=1.0, state='stalledUP'):
    return {'hash': h, 'name': h[:4], 'tags': tags, 'category': category, 'progress': progress, 'state': state,
            'content_path': '/downloads/x'}


def _info(ar, h):
    return ar.DownloadInfo(torrent_hash=h, tmdb_id=1, media_type='movie', title='T', year='2000', added_timestamp=0)


def test_prod_processes_its_own_tracked_grab_even_when_tagged(renamer, monkeypatch):
    """Prod only tracks its own grabs. When Taylor grabs a release on prod that
    :5002 grabbed first (same hash, so it carries the test tag), prod still files it."""
    ar, r = renamer
    r.tracked_downloads = {H_PROD: _info(ar, H_PROD), H_TEST: _info(ar, H_TEST)}
    torrents = {H_PROD: _t(H_PROD), H_TEST: _t(H_TEST, tags='luma-test, other')}
    monkeypatch.setattr(r, 'get_torrent_info', lambda h: torrents.get(h))
    r.check_downloads()
    assert sorted(r.processed) == sorted([H_PROD, H_TEST])


def test_prod_processes_its_own_tracked_grab_in_the_test_category(renamer, monkeypatch):
    ar, r = renamer
    r.tracked_downloads = {H_TEST: _info(ar, H_TEST)}
    monkeypatch.setattr(r, 'get_torrent_info', lambda h: _t(h, category='luma-test'))
    r.check_downloads()
    assert r.processed == [H_TEST]


def test_unfinished_and_missing_prod_torrents_behave_as_before(renamer, monkeypatch):
    ar, r = renamer
    r.tracked_downloads = {H_PROD: _info(ar, H_PROD)}
    monkeypatch.setattr(r, 'get_torrent_info', lambda h: _t(h, progress=0.4, state='downloading'))
    r.check_downloads()
    assert r.processed == [] and H_PROD in r.tracked_downloads
    monkeypatch.setattr(r, 'get_torrent_info', lambda h: None)
    r.check_downloads()
    assert H_PROD not in r.tracked_downloads      # 24 h old (added_timestamp 0): prod drops stale tracking


def test_radarr_category_scan_skips_the_test_install_s_torrents(renamer, monkeypatch):
    ar, r = renamer
    monkeypatch.setattr(r, '_radarr_monitoring_enabled', lambda: True)
    monkeypatch.setattr(r, '_radarr_settings', lambda: ('http://radarr.invalid', 'k'))
    monkeypatch.setattr(r, 'save_radarr_processed', lambda: None)
    monkeypatch.setattr(r, 'get_radarr_category_torrents', lambda: [
        _t(H_PROD, category='radarr'), _t(H_TEST, tags='luma-test', category='radarr')])
    looked_up = []
    monkeypatch.setattr(r, 'lookup_radarr_movie', lambda h: looked_up.append(h) or None)
    r.check_radarr_downloads()
    assert looked_up == [H_PROD.upper()]


def test_custom_tag_from_the_environment(renamer, monkeypatch):
    ar, r = renamer
    monkeypatch.setenv('LUMA_TEST_QBIT_TAG', 'qa-box')
    monkeypatch.setattr(r, '_radarr_monitoring_enabled', lambda: True)
    monkeypatch.setattr(r, '_radarr_settings', lambda: ('http://radarr.invalid', 'k'))
    monkeypatch.setattr(r, 'save_radarr_processed', lambda: None)
    monkeypatch.setattr(r, 'get_radarr_category_torrents', lambda: [
        _t(H_PROD, tags='luma-test', category='radarr'), _t(H_TEST, tags='QA-Box', category='radarr')])
    looked_up = []
    monkeypatch.setattr(r, 'lookup_radarr_movie', lambda h: looked_up.append(h) or None)
    r.check_radarr_downloads()
    assert looked_up == [H_PROD.upper()]


@pytest.mark.parametrize('raw,expect', [('', 'luma-test'), ('qa-box', 'qa-box'), ('bad,tag', 'luma-test'),
                                        ('x' * 65, 'luma-test'), (' spaced name ', 'spaced name')])
def test_tag_name(monkeypatch, raw, expect):
    monkeypatch.setenv('LUMA_TEST_QBIT_TAG', raw)
    assert qbit_owner.tag_name() == expect


def test_is_test_torrent_shapes():
    assert qbit_owner.is_test_torrent({'tags': 'a,luma-test'})
    assert qbit_owner.is_test_torrent({'tags': ['LUMA-TEST']})
    assert qbit_owner.is_test_torrent({'category': ' Luma-Test '})
    assert not qbit_owner.is_test_torrent({'tags': 'luma-testing', 'category': 'luma'})
    assert not qbit_owner.is_test_torrent({})
    assert not qbit_owner.is_test_torrent(None)
