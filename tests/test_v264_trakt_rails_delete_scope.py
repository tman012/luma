"""v264 (Tester LOW): clearing Home's Trakt rails deletes exactly <data dir>/home_trakt_rails.json and nothing
else (no sibling, backup, temp file, other JSON or directory). No network: no fetch is configured."""
import os
from pathlib import Path

import pytest

from app import home_rails


@pytest.fixture
def data(tmp_path, monkeypatch):
    d = tmp_path / 'data'
    d.mkdir()
    monkeypatch.setattr(home_rails.first_run, 'data_dir', lambda: d)
    with home_rails._lock:
        home_rails._trakt_saved.clear()
        home_rails._trakt_state['loaded'] = False
    yield d
    with home_rails._lock:
        home_rails._trakt_saved.clear()
        home_rails._trakt_state['loaded'] = False


def _tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob('*'))


def test_clear_deletes_only_that_exact_file(data, monkeypatch):
    target = data / 'home_trakt_rails.json'
    target.write_text('{}')
    for name in ('home_trakt_rails.json.bak', 'home_trakt_rails.tmp', 'home_trakt_rails.json.1', 'home_rails.json',
                 'wym_pool.json', 'download_monitor.json', 'grab_origins.json', 'xhome_trakt_rails.json'):
        (data / name).write_text('keep')
    (data / 'sub').mkdir()
    (data / 'sub' / 'home_trakt_rails.json').write_text('keep')
    outside = data.parent / 'home_trakt_rails.json'
    outside.write_text('keep')
    before = _tree(data)

    calls = []
    real_unlink = Path.unlink

    def unlink(self, *a, **k):
        calls.append(str(self))
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(Path, 'unlink', unlink)
    for name in ('remove', 'rmdir', 'removedirs'):
        monkeypatch.setattr(os, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError(f'os.{name}')))
    import shutil
    monkeypatch.setattr(shutil, 'rmtree', lambda *a, **k: (_ for _ in ()).throw(AssertionError('rmtree')))

    home_rails.clear_trakt_last()

    assert calls == [str(target)]
    assert not target.exists()
    assert _tree(data) == [p for p in before if p != 'home_trakt_rails.json']
    assert outside.read_text() == 'keep'


def test_clear_with_no_file_deletes_nothing(data, monkeypatch):
    (data / 'home_trakt_rails.json.bak').write_text('keep')
    before = _tree(data)
    home_rails.clear_trakt_last()
    assert _tree(data) == before


def test_save_writes_only_the_file_and_its_temp(data):
    with home_rails._lock:
        home_rails._trakt_state['loaded'] = True
        home_rails._trakt_saved['x'] = {'at': 1.0, 'cards': [{'key': 'k'}]}
        home_rails._save_trakt_locked()
    assert _tree(data) == ['home_trakt_rails.json']
