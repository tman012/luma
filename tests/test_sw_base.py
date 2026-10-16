"""The service worker follows the URL base (round 4, item 4)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(NODE is None, reason='node is not installed')


def _run(scope, *urls):
    out = subprocess.run([NODE, str(ROOT / 'tests/js/sw_harness.js'), str(ROOT / 'static/sw.js'),
                          scope, *urls], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def test_shell_and_routes_under_base():
    o = 'http://192.0.2.10:5001'
    r = _run(f'{o}/luma/', f'{o}/luma/api/settings', f'{o}/luma/api/img/tmdb?path=/a.jpg',
             f'{o}/luma/assets/index-abc.js', f'{o}/luma/static/images/x.png', f'{o}/luma/#nav',
             f'{o}/api/img/tmdb?path=/a.jpg', f'{o}/lumafoo/static/x.js', 'https://image.tmdb.org/t/p/a.jpg')
    assert r['shell'] == ['/luma/', '/luma/static/manifest.json']
    h = r['handled']
    assert h[f'{o}/luma/api/settings'] is False           # API data: network only
    assert h[f'{o}/luma/api/img/tmdb?path=/a.jpg'] is True
    assert h[f'{o}/luma/assets/index-abc.js'] is True
    assert h[f'{o}/luma/static/images/x.png'] is True
    assert h[f'{o}/luma/#nav'] is True
    assert h[f'{o}/api/img/tmdb?path=/a.jpg'] is False   # outside the base
    assert h[f'{o}/lumafoo/static/x.js'] is False
    assert h['https://image.tmdb.org/t/p/a.jpg'] is False


def test_shell_and_routes_at_root():
    o = 'http://192.0.2.10:5001'
    r = _run(f'{o}/', f'{o}/api/settings', f'{o}/api/img/plex?url=/x', f'{o}/assets/a.js')
    assert r['shell'] == ['/', '/static/manifest.json']
    assert r['handled'] == {f'{o}/api/settings': False, f'{o}/api/img/plex?url=/x': True,
                            f'{o}/assets/a.js': True}


def test_no_root_relative_literals_left():
    src = (ROOT / 'static/sw.js').read_text()
    for literal in ("fetch('/api", "c.put('/'", "caches.match('/')", "|| '/static/", "icon: '/static/"):
        assert literal not in src
