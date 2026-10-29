"""v324b: every movie grab (Genre Roulette included) uses the luma-test category/tag the importer
watches, TV stays luma-test-tv, and an empty helper reply / reset is treated like a timeout."""
from __future__ import annotations

import http.client
import os
import re
from pathlib import Path

import pytest

from app import movie_scanner as sc, qbit_owner, tv_downloads, tv_grab
from app import remote_mover as rm

from tests.test_v324_remote_import_safety import env, FakeScript  # noqa: F401

APP = Path(__file__).resolve().parents[1] / 'app'


def test_gr_movie_grab_category_is_luma_test(monkeypatch):
    monkeypatch.delenv('LUMA_TEST_QBIT_TAG', raising=False)
    monkeypatch.setenv('LUMA_MOVIE_CATEGORY', 'movie')       # the old knob no longer steers the grab
    assert sc.category() == qbit_owner.tag_name() == 'luma-test'
    f = tv_grab._fields(sc.category())
    assert f['category'] == f['tags'] == 'luma-test'


def test_gr_engine_grabs_through_movie_scanner():
    src = (APP / 'automation' / 'gr_engine.py').read_text()
    assert 'sc.grab(' in src and 'media_category' not in src


def test_movie_grab_follows_custom_test_tag(monkeypatch):
    monkeypatch.setenv('LUMA_TEST_QBIT_TAG', 'luma-test2')
    assert sc.category() == qbit_owner.tag_name() == 'luma-test2'


def test_tv_grab_keeps_luma_test_tv(monkeypatch):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.delenv('LUMA_TEST_QBIT_TAG', raising=False)
    assert tv_downloads.category() == 'luma-test-tv'
    assert tv_downloads.category() != sc.category()


def test_no_grab_path_uses_media_category_as_qbit_category():
    bad = re.compile(r"(category|tags?)\s*[=:]\s*[^\n]*media_category|createCategory[^\n]*media_category")
    for p in APP.rglob('*.py'):
        for i, line in enumerate(p.read_text(errors='ignore').splitlines(), 1):
            assert not bad.search(line), f'{p}:{i}'


def _raise(exc):
    def post(sock, path, body, timeout):
        try:
            raise exc
        except Exception as e:
            raise rm.RemoteMoverError('helper dropped') from e
    return post


def _setup(env, steps, exc):
    from app import library_mover as lm
    env['mp'].setattr(lm, 'partial_name', lambda: '.luma-00000000000000aa.luma-partial')
    fs = FakeScript(env, steps)
    fs.part = str(env['dest'].parent / '.luma-00000000000000aa.luma-partial')
    fs.advance()
    env['mp'].setattr(rm, '_http_post_unix', _raise(exc))
    env['mp'].setattr(rm, '_sleep', fs.sleep)


def test_empty_reply_final_lands_success(env):
    _setup(env, [('part', 10), ('part', 500), ('final', 1000)],
           http.client.RemoteDisconnected('Remote end closed connection without response'))
    assert rm.transfer(str(env['src']), str(env['dest'])) == 'copied'


def test_bad_status_line_final_lands_success(env):
    _setup(env, [('part', 10), ('final', 1000)], http.client.BadStatusLine(''))
    assert rm.transfer(str(env['src']), str(env['dest'])) == 'copied'


def test_connection_reset_tmp_vanishes_fails(env):
    _setup(env, [('part', 300), ('gone', 0)], ConnectionResetError(104, 'reset'))
    with pytest.raises(rm.RemoteMoverError, match='partial removed'):
        rm.transfer(str(env['src']), str(env['dest']))
    assert not env['dest'].exists()


def test_empty_reply_nothing_grows_stall_fails(env):
    _setup(env, [('part', 300)], http.client.RemoteDisconnected('empty'))
    with pytest.raises(rm.RemoteMoverError, match='stalled'):
        rm.transfer(str(env['src']), str(env['dest']))
    assert not env['dest'].exists()


def test_http_post_unix_maps_empty_reply(monkeypatch):
    class C:
        def __init__(self, *a, **k): pass
        def request(self, *a, **k): pass
        def getresponse(self): raise http.client.RemoteDisconnected('x')
        def close(self): pass
    monkeypatch.setattr(rm, '_UnixHTTPConnection', C)
    with pytest.raises(rm.RemoteMoverError, match='empty reply') as ei:
        rm._http_post_unix('/x.sock', '/move', b'{}', 5)
    assert isinstance(ei.value.__cause__, http.client.RemoteDisconnected)
