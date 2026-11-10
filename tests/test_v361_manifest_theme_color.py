"""v361: the served /manifest.json launches with the splash color. An install whose settings.json still holds the
retired default ui.theme_color #0a0a0a (bootstrap wrote it before SW v108) served it as theme_color next to the
#15102a splash; the settings load now moves that old default to #15102a, once. Fixtures only."""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / 'frontend' / 'index.html'
BG = '#15102a'
LAN = {'REMOTE_ADDR': '192.168.1.20'}


def _manifest(test_app, monkeypatch, tmp_path, ui=None, minimal=True):
    from app import main
    from app.settings_manager import SettingsManager
    path = tmp_path / 'settings.json'
    if ui is not None:
        path.write_text(json.dumps({'ui': ui}))
    monkeypatch.setattr(main, 'SettingsManager', lambda: SettingsManager(str(path)))
    if minimal:
        monkeypatch.setenv('LUMA_MINIMAL', '1')
    r = test_app.test_client().get('/manifest.json', environ_base=LAN)
    assert r.status_code == 200
    return r.json, path


def _html_colors():
    html = INDEX.read_text()
    meta = re.search(r'<meta name="theme-color" content="(#[0-9a-fA-F]{6})">', html).group(1)
    splash = re.search(r'#splash-bg \{[^}]*background: (#[0-9a-fA-F]{6});', html).group(1)
    return meta, splash


@pytest.mark.parametrize('minimal', [True, False])
def test_installs_holding_the_retired_default_serve_the_splash_color(test_app, monkeypatch, tmp_path, minimal):
    body, path = _manifest(test_app, monkeypatch, tmp_path, ui={'theme_color': '#0a0a0a'}, minimal=minimal)
    meta, splash = _html_colors()
    assert body['theme_color'] == body['background_color'] == BG
    assert meta == splash == body['theme_color']
    assert json.loads(path.read_text())['ui']['theme_color'] == BG


def test_fresh_install_serves_the_splash_color(test_app, monkeypatch, tmp_path):
    body, _ = _manifest(test_app, monkeypatch, tmp_path)
    assert body['theme_color'] == body['background_color'] == BG == _html_colors()[0]


def test_a_deliberate_theme_color_is_kept(test_app, monkeypatch, tmp_path):
    body, path = _manifest(test_app, monkeypatch, tmp_path, ui={'theme_color': '#123456'})
    assert body['theme_color'] == '#123456' and body['background_color'] == BG
    assert json.loads(path.read_text())['ui']['theme_color'] == '#123456'


def test_the_old_default_set_again_after_the_migration_ran_is_kept(test_app, monkeypatch, tmp_path):
    from app.settings_manager import SettingsManager
    _manifest(test_app, monkeypatch, tmp_path, ui={'theme_color': '#0a0a0a'})
    SettingsManager(str(tmp_path / 'settings.json')).update({'ui': {'theme_color': '#0a0a0a'}})
    body, _ = _manifest(test_app, monkeypatch, tmp_path)
    assert body['theme_color'] == '#0a0a0a'
