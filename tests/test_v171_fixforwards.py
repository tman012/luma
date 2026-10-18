"""v171: Tester's fbe2e55 fix-forwards. Fakes and mocks only.

1) TV grab: a magnet with no hash or a short one is a 400 (no fallback), not a 200 {ok:false}.
2) Movie Sources grab: the magnet is checked before qBit; a hash-less one is a 400 and nothing reaches qBit.
3) 'No writable destination' only when read-only is the true cause; else the item says 'No destination'.
"""
import os

import pytest

import app.main
from app import file_management as fm, library_mover as lm, tv_grab
from tests.test_downloads_add_ssrf import _owner_client, _post, _require_auth_everywhere, client, net, qbit  # noqa: F401
from tests.test_tv_v158c import BODY, _post as _tv_post, known_token, qenv  # noqa: F401
from tests.test_file_management import minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import lib  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

NOT_ROOT = pytest.mark.skipif(os.geteuid() == 0, reason='root ignores directory permissions')
BAD_MAGNETS = [
    'magnet:?dn=Heat.1995.1080p',                              # no hash
    'magnet:?xt=urn:btih:abcdef0123',                          # short
    'magnet:?xt=urn:btih:' + 'z' * 40,                         # 40 chars, not hex
    'magnet:?xt=urn:btih:' + 'a' * 39 + '&dn=x',               # one short
]


# ------------------------------------------------------------------ 1) TV grab

@pytest.mark.parametrize('magnet', BAD_MAGNETS)
def test_tv_grab_of_a_hashless_magnet_is_a_400_with_no_fallback(qenv, owner, monkeypatch, known_token, magnet):  # noqa: F811
    real = tv_grab.grab
    sessions = []

    def fake(it, sid, season, ids, origin, now=None, **k):
        return real(it, sid, season, ids, origin, now, fetch=lambda u: ('magnet', magnet),
                    session_factory=lambda: sessions.append(1))
    monkeypatch.setattr(tv_grab, 'grab', fake)
    r = _tv_post(owner, BODY)
    assert r.status_code == 400
    assert r.get_json() == {'ok': False, 'error': tv_grab.NO_HASH, 'fallback': False}
    assert sessions == []                                       # qBit never contacted


def test_tv_info_hash_rules():
    assert tv_grab.info_hash('magnet', 'magnet:?xt=urn:btih:' + 'A' * 40) == 'a' * 40
    for m in BAD_MAGNETS:
        assert tv_grab.info_hash('magnet', m) is None


# ------------------------------------------------------------------ 2) movie Sources grab

@pytest.mark.parametrize('magnet', BAD_MAGNETS)
def test_movie_grab_refuses_a_hashless_magnet_before_qbit(client, net, qbit, magnet):  # noqa: F811
    _require_auth_everywhere()
    _owner_client(client)
    r = _post(client, magnet)
    assert r.status_code == 400
    assert r.get_json()['error'] in ('This release has no info hash', 'Link not allowed')
    assert qbit.added == [] and qbit.logins == 0                # nothing sent, not even a sign-in


def test_movie_grab_still_takes_a_good_magnet(client, net, qbit):  # noqa: F811
    _require_auth_everywhere()
    _owner_client(client)
    import base64
    b32 = base64.b32encode(bytes.fromhex('ab' * 20)).decode()
    r = _post(client, 'magnet:?xt=urn:btih:' + b32)
    assert r.status_code == 200, r.get_json()
    assert qbit.added and app.main._magnet_info_hash(qbit.added[0]['data']['urls']) == 'ab' * 20


def test_magnet_hash_helper_is_hex_only():
    assert app.main._magnet_info_hash('magnet:?xt=urn:btih:' + 'z' * 40) is None
    assert app.main._magnet_info_hash('magnet:?xt=urn:btih:' + 'F' * 40) == 'f' * 40


# ------------------------------------------------------------------ 3) the true no-destination reason

@NOT_ROOT
def test_read_only_is_named_only_when_it_is_the_cause(tmp_path):
    ro, rw = tmp_path / 'ro', tmp_path / 'rw'
    ro.mkdir()
    rw.mkdir()
    ro.chmod(0o555)
    try:
        P = lambda i, p: {'id': i, 'name': i, 'path': str(p)}  # noqa: E731
        lone_ro = {'paths': [P('p_ro', ro)], 'rules': [], 'fallback_path_id': None}
        assert fm.pick_destination(lone_ro, {}, taken=set()) is None
        assert fm.no_writable_destination(lone_ro, set()) is True              # the true cause
        mixed = {'paths': [P('p_ro', ro), P('p_rw', rw)], 'rules': [], 'fallback_path_id': None}
        assert fm.pick_destination(mixed, {}, taken=set()) is None
        assert fm.no_writable_destination(mixed, set()) is False               # no rule, no fallback: not read-only
        chosen = dict(lone_ro, fallback_path_id='p_ro')
        assert fm.no_writable_destination(chosen, set()) is False              # a chosen fallback: mover says not writable
    finally:
        ro.chmod(0o755)


def test_a_skipped_no_destination_item_says_so(monkeypatch):
    import time
    jobs = {'C' * 40: {'state': 'skipped', 'error': lm.NO_DESTINATION, 'finished': time.time() - 60,
                       'title': 'Heat', 'name': 'Heat.1995.1080p'},
            'D' * 40: {'state': 'skipped', 'error': lm.NO_DESTINATION, 'finished': time.time() - 2 * 86400,
                       'title': 'Old', 'name': 'Old.2001'},
            'E' * 40: {'state': 'waiting', 'status': lm.NO_WRITABLE_DEST, 'title': 'Ronin', 'name': 'Ronin.1998'}}
    monkeypatch.setattr(fm, 'load', lambda: {'jobs': jobs})
    downloads = []
    app.main._mark_not_mapped(downloads, [], {}, set())
    got = {d['hash']: d['status'] for d in downloads}
    assert got == {'c' * 40: 'no_destination', 'e' * 40: 'no_writable'}     # a day old skip is gone
