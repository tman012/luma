"""Downloads Guard / storage cleanup never delete the test install's downloads.

Production and the test install (LUMA_MINIMAL, :5002) can share one
qBittorrent. Torrents the test install added carry LUMA_TEST_QBIT_TAG
(default 'luma-test'); their data stays put until the test install has copied
it. The unattended guard deletes nothing while qBittorrent can't be read.
"""
import os
import time

import pytest

from app import downloads_guard, storage_cleanup, tracker_files

OLD = time.time() - 72 * 3600
GIB = 1024 ** 3


def _entry(root, name, size=1024, sub=None):
    path = root / name
    if sub:
        (path / sub).parent.mkdir(parents=True, exist_ok=True)
        f = path / sub
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        f = path
    f.write_bytes(b'x' * size)
    for p in [f, *([path] if sub else [])]:
        os.utime(p, (OLD, OLD))
    if sub:
        for d in f.parents:
            if d == path.parent:
                break
            os.utime(d, (OLD, OLD))
    return path


def _t(name, content_path, tags='', category='', progress=1.0, state='stalledUP', h=None):
    return {'hash': h or (name.encode().hex() * 4)[:40], 'name': name, 'content_path': content_path,
            'tags': tags, 'category': category, 'progress': progress, 'state': state,
            'tracker': '', 'trackers': []}


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    root = tmp_path / 'downloads'
    root.mkdir()
    monkeypatch.setattr(storage_cleanup, 'downloads_root', lambda: str(root))
    monkeypatch.setattr(storage_cleanup, '_forget_torrents', lambda names: 0)
    monkeypatch.setattr(downloads_guard, '_data_dir', lambda: tmp_path)
    monkeypatch.delenv('LUMA_TEST_QBIT_TAG', raising=False)
    _entry(root, 'Prod.Movie.2001.1080p', sub='Prod.Movie.2001.1080p.mkv')
    _entry(root, 'Prod.Single.2002.mkv')
    _entry(root, 'Test.Movie.2020.2160p', sub='Test.Movie.2020.2160p.mkv')
    _entry(root, 'Test.Single.2021.mkv')
    _entry(root, 'LumaSub', sub='Nested.Test.2022.mkv')
    torrents = [
        _t('Prod.Movie.2001.1080p', '/downloads/Prod.Movie.2001.1080p', category='radarr'),
        _t('Prod.Single.2002.mkv', '/downloads/Prod.Single.2002.mkv', tags='keep,other'),
        _t('Test.Movie.2020.2160p', '/downloads/Test.Movie.2020.2160p', tags='luma-test', category='Luma'),
        _t('Test.Single.2021.mkv', '/downloads/Test.Single.2021.mkv', tags='x, Luma-Test'),
        _t('Nested.Test.2022.mkv', '/downloads/LumaSub/Nested.Test.2022.mkv', tags='luma-test'),
    ]
    state = {'torrents': torrents}
    monkeypatch.setattr(tracker_files, 'fetch_torrents', lambda: state['torrents'])
    return root, state


def _names(cands):
    return sorted(c['name'] for c in cands)


def test_scan_skips_tagged_torrents_finished_or_not(downloads):
    root, _ = downloads
    assert _names(storage_cleanup.scan_candidates(str(root))) == ['Prod.Movie.2001.1080p', 'Prod.Single.2002.mkv']


def test_a_folder_holding_tagged_data_is_skipped_whole(downloads):
    root, _ = downloads
    assert 'LumaSub' not in _names(storage_cleanup.scan_candidates(str(root)))


def test_only_the_tag_counts_not_a_category_or_other_tags(downloads):
    root, state = downloads
    state['torrents'] = [
        _t('Prod.Movie.2001.1080p', '/downloads/Prod.Movie.2001.1080p', category='luma-test'),
        _t('Prod.Single.2002.mkv', '/downloads/Prod.Single.2002.mkv', tags='luma-testing', category='luma'),
    ]
    names = _names(storage_cleanup.scan_candidates(str(root)))
    assert 'Prod.Movie.2001.1080p' in names
    assert 'Prod.Single.2002.mkv' in names


def test_custom_tag_from_env(downloads, monkeypatch):
    root, state = downloads
    monkeypatch.setenv('LUMA_TEST_QBIT_TAG', 'qa-box')
    state['torrents'] = [_t('Prod.Single.2002.mkv', '/downloads/Prod.Single.2002.mkv', tags='qa-box'),
                         _t('Test.Single.2021.mkv', '/downloads/Test.Single.2021.mkv', tags='luma-test')]
    names = _names(storage_cleanup.scan_candidates(str(root)))
    assert 'Prod.Single.2002.mkv' not in names
    assert 'Test.Single.2021.mkv' in names


def test_untagged_behavior_unchanged(downloads):
    root, state = downloads
    state['torrents'] = []
    assert _names(storage_cleanup.scan_candidates(str(root))) == [
        'LumaSub', 'Prod.Movie.2001.1080p', 'Prod.Single.2002.mkv', 'Test.Movie.2020.2160p', 'Test.Single.2021.mkv']


def test_run_cleanup_deletes_only_production_data(downloads):
    root, _ = downloads
    out = storage_cleanup.run_cleanup(10 * GIB, require_qbit=True)
    assert out['success'] is True
    assert sorted(d['name'] for d in out['deleted']) == ['Prod.Movie.2001.1080p', 'Prod.Single.2002.mkv']
    assert sorted(os.listdir(root)) == ['LumaSub', 'Test.Movie.2020.2160p', 'Test.Single.2021.mkv']


def test_guard_fails_safe_when_qbit_unreadable(downloads, monkeypatch):
    root, state = downloads
    state['torrents'] = None
    before = sorted(os.listdir(root))
    out = storage_cleanup.run_cleanup(10 * GIB, require_qbit=True)
    assert out['success'] is False
    assert out['error'] == storage_cleanup.QBIT_UNREADABLE
    plan = storage_cleanup.plan_cleanup(10 * GIB, str(root), require_qbit=True)
    assert plan['entries'] == [] and plan['error'] == storage_cleanup.QBIT_UNREADABLE
    assert sorted(os.listdir(root)) == before


def test_manual_cleanup_deletes_nothing_when_qbit_is_down(downloads):
    """The storage pill's cleanup also needs qBittorrent: without it the
    test install's downloads can't be told apart."""
    root, state = downloads
    state['torrents'] = None
    before = sorted(os.listdir(root))
    out = storage_cleanup.run_cleanup(1)
    assert out['success'] is False and out['error'] == storage_cleanup.QBIT_UNREADABLE
    assert sorted(os.listdir(root)) == before


def test_opting_out_of_qbit_keeps_the_age_only_behavior(downloads):
    root, state = downloads
    state['torrents'] = None
    out = storage_cleanup.run_cleanup(1, require_qbit=False)
    assert out['success'] is True and len(out['deleted']) == 1


def test_a_category_folder_holding_tagged_data_offers_its_other_entries(downloads):
    root, state = downloads
    _entry(root, 'radarr', sub='Prod.Cat.2003/Prod.Cat.2003.mkv')
    _entry(root, 'radarr', sub='Test.Cat.2023/Test.Cat.2023.mkv')
    state['torrents'] = state['torrents'] + [
        _t('Prod.Cat.2003', '/downloads/radarr/Prod.Cat.2003', category='radarr'),
        _t('Test.Cat.2023', '/downloads/radarr/Test.Cat.2023', tags='luma-test', category='radarr')]
    names = _names(storage_cleanup.scan_candidates(str(root)))
    assert 'radarr' not in names and 'radarr/Test.Cat.2023' not in names
    assert 'radarr/Prod.Cat.2003' in names
    out = storage_cleanup.run_cleanup(10 * GIB, require_qbit=True)
    assert out['success'] is True
    assert (root / 'radarr' / 'Test.Cat.2023').exists()
    assert not (root / 'radarr' / 'Prod.Cat.2003').exists()


def test_luma_test_entry_names_are_only_each_torrent_s_own_entry():
    names = storage_cleanup.luma_test_entry_names([
        _t('Test.Cat.2023', '/downloads/radarr/Test.Cat.2023', tags='luma-test', category='radarr'),
        _t('Prod.Cat.2003', '/downloads/radarr/Prod.Cat.2003', category='radarr')])
    assert names == {'Test.Cat.2023'}


class _Settings:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def guard_on(monkeypatch):
    values = {'automation.downloads_guard.enabled': True,
              'automation.downloads_guard.min_free_gb': 10_000,
              'automation.downloads_guard.reclaim_gb': 10,
              'automation.downloads_guard.trackers': []}
    import app.settings_manager as sm
    monkeypatch.setattr(sm, 'SettingsManager', lambda *a, **k: _Settings(values))
    return values


def test_downloads_guard_cycle_skips_tagged_and_deletes_production(downloads, guard_on):
    root, _ = downloads
    r = downloads_guard.run_cycle()
    assert r['triggered'] is True and r['error'] is None
    assert sorted(d['name'] for d in r['deleted']) == ['Prod.Movie.2001.1080p', 'Prod.Single.2002.mkv']
    assert sorted(os.listdir(root)) == ['LumaSub', 'Test.Movie.2020.2160p', 'Test.Single.2021.mkv']


def test_downloads_guard_cycle_deletes_nothing_when_qbit_unreadable(downloads, guard_on):
    root, state = downloads
    state['torrents'] = None
    before = sorted(os.listdir(root))
    r = downloads_guard.run_cycle()
    assert r['triggered'] is True
    assert r['error'] == storage_cleanup.QBIT_UNREADABLE
    assert r['deleted'] == [] and r['freed_bytes'] == 0
    assert sorted(os.listdir(root)) == before
    dry = downloads_guard.run_cycle(dry_run=True)
    assert dry['error'] == storage_cleanup.QBIT_UNREADABLE and dry['deleted'] == []
    assert downloads_guard.preview()['entries'] == []


def test_downloads_guard_preview_never_lists_tagged(downloads, guard_on):
    names = [e['name'] for e in downloads_guard.preview()['entries']]
    assert names and not any(n.startswith(('Test.', 'LumaSub')) for n in names)
