"""Shared devices: the service worker never keeps an account's images, never
stores a no-store/private response, and drops everything on CLEAR_ALL_CACHES."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which('node')
O = 'http://192.0.2.10:5001'
pytestmark = pytest.mark.skipif(NODE is None, reason='node is not installed')


def _sw(urls, spec=None, message=None, caches=None):
    env = dict(os.environ)
    if spec is not None:
        env['SW_SHELL_RESP'] = json.dumps(spec)
    if message:
        env['SW_MESSAGE'] = message
    if caches is not None:
        env['SW_CACHES'] = json.dumps(caches)
    out = subprocess.run([NODE, str(ROOT / 'tests/js/sw_harness.js'), str(ROOT / 'static/sw.js'), f'{O}/', *urls],
                         capture_output=True, text=True, timeout=30, check=True, env=env)
    return json.loads(out.stdout)


def test_proxied_images_are_never_handled_or_stored():
    urls = [f'{O}/api/img/plex?url=/library/metadata/1/thumb/2&w=300', f'{O}/api/img/tmdb?path=/a.jpg&size=w500']
    r = _sw(urls, {'ok': True, 'headers': {'Cache-Control': 'private, max-age=86400'}})
    assert not any(r['handled'][u] for u in urls)   # straight to the network (browser HTTP cache still applies)
    assert not any('/api/img/' in p for p in r['puts'])


@pytest.mark.parametrize('cc', ['no-store', 'private, max-age=86400', 'No-Store'])
def test_no_store_or_private_responses_are_never_stored(cc):
    urls = [f'{O}/static/images/imdb-logo.png', f'{O}/static/app.css', f'{O}/assets/index-abc123.js']
    r = _sw(urls, {'ok': True, 'headers': {'Cache-Control': cc}})
    assert all(r['handled'][u] for u in urls)
    assert not [p for p in r['puts'] if p != '/']   # nothing but (maybe) the app shell


def test_plain_static_responses_are_still_stored():
    urls = [f'{O}/static/images/imdb-logo.png', f'{O}/assets/index-abc123.js']
    r = _sw(urls, {'ok': True, 'headers': {}})
    assert set(urls) <= set(r['puts'])


def test_clear_all_caches_drops_every_cache():
    names = ['media-kiosk-v126', 'media-kiosk-immutable', 'notification-deeplink']
    r = _sw([], message='CLEAR_ALL_CACHES', caches=names)
    assert set(names) <= set(r['deleted'])
