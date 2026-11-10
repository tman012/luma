"""v360 startup splash: the page the server sends paints the Luma logo from static HTML/CSS, and the native launch
(manifest colors, iOS launch screens) is the same color. Reads the source index.html, never the built app."""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / 'frontend' / 'index.html'
BG = '#15102a'


def _served(test_app, monkeypatch, tmp_path, base=''):
    (tmp_path / 'index.html').write_text(INDEX.read_text())
    monkeypatch.setitem(test_app.config, 'SPA_DIR', str(tmp_path))
    monkeypatch.setitem(test_app.config, 'URL_BASE', base)
    from app import main
    with test_app.test_request_context('/'):
        return main._serve_spa().get_data(as_text=True)


def test_the_splash_is_in_the_html_before_the_app_root_and_has_no_text(test_app, monkeypatch, tmp_path):
    html = _served(test_app, monkeypatch, tmp_path)
    assert html.index('id="splash"') < html.index('<div id="root">')
    assert 'data:image/webp;base64,' in html.split('<div id="root">')[0]
    splash = html[html.index('<div id="splash"'):html.index('<script>')]
    assert re.sub(r'<[^>]+>', '', splash).strip() == ''
    for word in ('Loading', 'spinner', 'splash-ring', 'splash-status'):
        assert word not in html


def test_launch_colors_match_the_splash_background():
    manifest = json.loads((ROOT / 'static' / 'manifest.json').read_text())
    html = INDEX.read_text()
    assert manifest['background_color'] == BG and manifest['theme_color'] == BG
    assert f'<meta name="theme-color" content="{BG}">' in html
    assert f'#splash-bg {{ position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: {BG}; }}' in html


def test_ios_launch_images_follow_the_url_base(test_app, monkeypatch, tmp_path):
    html = _served(test_app, monkeypatch, tmp_path, base='/luma')
    links = re.findall(r'<link rel="apple-touch-startup-image"[^>]*href="([^"]+)"', html)
    assert len(links) >= 12
    for href in links:
        assert href.startswith('/luma/static/images/splash/ios-'), href
        assert (ROOT / href.replace('/luma/', '', 1)).is_file(), href
    assert 'href="/luma/static/images/luma_text.webp"' in html
    assert 'href="/luma/static/images/luma_wordmark.webp"' in html


def test_ios_launch_images_are_solid_splash_color():
    PIL = pytest.importorskip('PIL.Image')
    for p in sorted((ROOT / 'static' / 'images' / 'splash').glob('ios-*.png')):
        im = PIL.open(p).convert('RGB')
        w, h = im.size
        assert p.name == f'ios-{w}x{h}.png'
        assert im.getcolors(maxcolors=4) == [(w * h, (0x15, 0x10, 0x2a))], p.name


def test_the_service_worker_is_v362_and_the_stamp_is_truthful():
    assert "const CACHE_VERSION = 'media-kiosk-v362';" in (ROOT / 'static' / 'sw.js').read_text()
    stamp = json.loads((ROOT / 'static' / 'sw-stamp.json').read_text())
    assert stamp['cache_version'] == 'media-kiosk-v362'
