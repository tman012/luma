"""Every frontend change ships a new service worker version (scripts/sw_stamp.py)."""
import importlib.util

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location('sw_stamp', ROOT / 'scripts' / 'sw_stamp.py')
sw_stamp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sw_stamp)


def test_the_sw_version_was_bumped_for_this_frontend():
    assert sw_stamp.check() == []


def test_the_sw_version_is_above_the_last_shipped_one():
    assert sw_stamp.version_number() > sw_stamp.LAST_SHIPPED >= 138
    assert sw_stamp.version_number() not in sw_stamp.TAKEN


def test_v145_is_taken_by_the_build_ops_is_shipping():
    """01abfd9 (v145) is going out: this branch's next frontend needs a newer one."""
    assert 145 in sw_stamp.TAKEN and 144 in sw_stamp.TAKEN
    assert sw_stamp.version_number() >= 146


def test_v146_is_never_reused():
    """Tester M38: v146 went out in the f0db05d bundle; TV Import is v147."""
    assert 146 in sw_stamp.TAKEN
    assert sw_stamp.version_number() >= 147
    with pytest.raises(SystemExit):
        sw_stamp.set_version(146)


def test_v147_stays_with_the_tv_import_build():
    """4d22f6c (TV Import, v147) is with the gates: the Import fixes are v148 or later."""
    assert 147 in sw_stamp.TAKEN
    assert sw_stamp.version_number() >= 148
    with pytest.raises(SystemExit):
        sw_stamp.set_version(147)


def _fake(tmp_path, monkeypatch, version, last):
    for rel in ('static', 'frontend/src'):
        (tmp_path / rel).mkdir(parents=True)
    (tmp_path / 'static' / 'sw.js').write_text(f"const CACHE_VERSION = 'media-kiosk-v{version}';\n")
    (tmp_path / 'frontend' / 'src' / 'a.ts').write_text('export const a = 1;\n')
    monkeypatch.setattr(sw_stamp, 'ROOT', tmp_path)
    monkeypatch.setattr(sw_stamp, 'SW', tmp_path / 'static' / 'sw.js')
    monkeypatch.setattr(sw_stamp, 'STAMP', tmp_path / 'static' / 'sw-stamp.json')
    monkeypatch.setattr(sw_stamp, 'LAST_SHIPPED', last)
    monkeypatch.setattr(sw_stamp, 'TAKEN', frozenset())


def test_a_frontend_change_needs_a_new_version(tmp_path, monkeypatch):
    _fake(tmp_path, monkeypatch, 7, 7)
    assert sw_stamp.check()[0] == 'static/sw-stamp.json is missing or unreadable'
    assert sw_stamp.bump() == 'media-kiosk-v8'
    assert sw_stamp.check() == []
    (tmp_path / 'frontend' / 'src' / 'a.ts').write_text('export const a = 2;\n')
    assert any('frontend changed' in p for p in sw_stamp.check())
    # v8 hasn't shipped: it may absorb the change
    assert sw_stamp.restamp() == 'media-kiosk-v8' and sw_stamp.check() == []
    # once v8 ships, a change needs a new version
    monkeypatch.setattr(sw_stamp, 'LAST_SHIPPED', 8)
    assert any('not above the last shipped v8' in p for p in sw_stamp.check())
    with pytest.raises(SystemExit):
        sw_stamp.restamp()
    with pytest.raises(SystemExit):
        sw_stamp.set_version(8)
    assert sw_stamp.bump() == 'media-kiosk-v9' and sw_stamp.check() == []
    assert "const CACHE_VERSION = 'media-kiosk-v9';" in (tmp_path / 'static' / 'sw.js').read_text()


def test_bump_skips_versions_taken_elsewhere(tmp_path, monkeypatch):
    _fake(tmp_path, monkeypatch, 138, 138)
    monkeypatch.setattr(sw_stamp, 'TAKEN', frozenset({139}))   # another branch's
    assert sw_stamp.bump() == 'media-kiosk-v140'
    monkeypatch.setattr(sw_stamp, 'TAKEN', frozenset({140}))
    assert any('taken' in p for p in sw_stamp.check())
    with pytest.raises(SystemExit):
        sw_stamp.set_version(140)


def test_v148_stays_with_the_bundled_import_fixes():
    """4b82ab5 (Import fixes, v148) was bundled: later builds are v149 or later."""
    assert 148 in sw_stamp.TAKEN
    assert sw_stamp.version_number() >= 149


def test_v149_stays_with_testers_import_fixes():
    """a201cfa (Tester's Import fixes, v149): TV Calendar is v150 or later."""
    assert 149 in sw_stamp.TAKEN
    assert sw_stamp.version_number() >= 150
