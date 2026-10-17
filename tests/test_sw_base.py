"""The service worker follows the URL base (round 4, item 4)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(NODE is None, reason='node is not installed')


def _run(scope, *urls, push=None):
    import os
    env = dict(os.environ, SW_PUSH=json.dumps(push)) if push else None
    out = subprocess.run([NODE, str(ROOT / 'tests/js/sw_harness.js'), str(ROOT / 'static/sw.js'),
                          scope, *urls], capture_output=True, text=True, timeout=30, check=True, env=env)
    return json.loads(out.stdout)


def test_shell_and_routes_under_base():
    o = 'http://192.0.2.10:5001'
    r = _run(f'{o}/luma/', f'{o}/luma/api/settings', f'{o}/luma/api/img/tmdb?path=/a.jpg',
             f'{o}/luma/assets/index-abc.js', f'{o}/luma/static/images/x.png', f'{o}/luma/#nav',
             f'{o}/api/img/tmdb?path=/a.jpg', f'{o}/lumafoo/static/x.js', 'https://image.tmdb.org/t/p/a.jpg')
    assert r['shell'] == ['/luma/manifest.json']
    h = r['handled']
    assert h[f'{o}/luma/api/settings'] is False           # API data: network only
    assert h[f'{o}/luma/api/img/tmdb?path=/a.jpg'] is False   # images: network only since v111
    assert h[f'{o}/luma/assets/index-abc.js'] is True
    assert h[f'{o}/luma/static/images/x.png'] is True
    assert h[f'{o}/luma/#nav'] is True
    assert h[f'{o}/api/img/tmdb?path=/a.jpg'] is False   # outside the base
    assert h[f'{o}/lumafoo/static/x.js'] is False
    assert h['https://image.tmdb.org/t/p/a.jpg'] is False


def test_shell_and_routes_at_root():
    o = 'http://192.0.2.10:5001'
    r = _run(f'{o}/', f'{o}/api/settings', f'{o}/api/img/plex?url=/x', f'{o}/assets/a.js')
    assert r['shell'] == ['/manifest.json']
    assert r['handled'] == {f'{o}/api/settings': False, f'{o}/api/img/plex?url=/x': False,
                            f'{o}/assets/a.js': True}


def test_no_root_relative_literals_left():
    src = (ROOT / 'static/sw.js').read_text()
    for literal in ("fetch('/api", "c.put('/'", "caches.match('/')", "|| '/static/", "icon: '/static/"):
        assert literal not in src


def test_push_icons_follow_base():
    o = 'http://192.0.2.10:5001'
    push = {'title': 't', 'icon': '/static/images/a.png', 'badge': '/luma/static/images/b.png',
            'image': '/api/img/plex?url=/x'}
    (note,) = _run(f'{o}/luma/', push=push)['notes']
    assert note['icon'] == '/luma/static/images/a.png'
    assert note['badge'] == '/luma/static/images/b.png'     # not doubled
    assert note['image'] == '/luma/api/img/plex?url=/x'
    (note,) = _run(f'{o}/luma/', push={'image': 'https://image.tmdb.org/t/p/x.jpg'})['notes']
    assert note['image'] == 'https://image.tmdb.org/t/p/x.jpg'
    assert note['icon'] == '/luma/static/images/ic_launcher_round.png'
    (note,) = _run(f'{o}/', push={'icon': '/static/images/a.png'})['notes']
    assert note['icon'] == '/static/images/a.png'
