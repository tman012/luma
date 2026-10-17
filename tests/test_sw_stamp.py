"""Every frontend change ships a new service worker version (scripts/sw_stamp.py)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location('sw_stamp', ROOT / 'scripts' / 'sw_stamp.py')
sw_stamp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sw_stamp)


def test_the_sw_version_was_bumped_for_this_frontend():
    assert sw_stamp.check() == []


def test_a_frontend_change_needs_a_bump(tmp_path, monkeypatch):
    for rel in ('static', 'frontend/src'):
        (tmp_path / rel).mkdir(parents=True)
    (tmp_path / 'static' / 'sw.js').write_text("const CACHE_VERSION = 'media-kiosk-v7';\n")
    (tmp_path / 'frontend' / 'src' / 'a.ts').write_text('export const a = 1;\n')
    monkeypatch.setattr(sw_stamp, 'ROOT', tmp_path)
    monkeypatch.setattr(sw_stamp, 'SW', tmp_path / 'static' / 'sw.js')
    monkeypatch.setattr(sw_stamp, 'STAMP', tmp_path / 'static' / 'sw-stamp.json')
    assert sw_stamp.check() == ['static/sw-stamp.json is missing or unreadable']
    assert sw_stamp.bump() == 'media-kiosk-v8'
    assert sw_stamp.check() == []
    (tmp_path / 'frontend' / 'src' / 'a.ts').write_text('export const a = 2;\n')
    assert any('frontend changed' in p for p in sw_stamp.check())
    assert sw_stamp.bump() == 'media-kiosk-v9' and sw_stamp.check() == []
    assert "const CACHE_VERSION = 'media-kiosk-v9';" in (tmp_path / 'static' / 'sw.js').read_text()
