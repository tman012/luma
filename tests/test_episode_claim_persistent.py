"""RC-MOBLAND-DOUBLEGRAB (Taylor 2026-10-29): MobLand S02E07 was grabbed twice (FLUX, RAWR) by two
trackers in one feed tick, imported twice and pushed twice. One persistent, cross-caller episode claim in
tv_grab.grab: a second release of a claimed episode is never sent, from another run, another caller or
another thread; pre-qBittorrent failures never claim; a second import of the same episode sends no push."""
import json
import threading
import time

import pytest
import requests

from app import tv_downloads, tv_grab, tv_store
from tests.test_library_mover import Resp
from tests.test_tv_v158c import FakeQbit, qenv  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import lib  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.test_file_management import minimal as minimal_app, store  # noqa: F401
from tests.test_tv_search_v158b import env, eps, item  # noqa: F401

FLUX = b'd8:announce3:url4:infod4:name4:FLUX6:lengthi1eee'
RAWR = b'd8:announce3:url4:infod4:name4:RAWR6:lengthi1eee'


def _g(q, sid, payload, origin='auto', now=5000.0, ep=3):
    e = eps(sid, 2)[ep]
    return tv_grab.grab(item(f'Severance.S02E0{ep}.1080p.WEB-DL-{payload[-12:-8].decode()}'), sid, 2, [e], origin, now,
                        session_factory=q.factory, fetch=lambda u: ('torrent', payload))


@pytest.fixture(autouse=True)
def _clean():
    tv_grab._inflight.clear()
    yield
    tv_grab._inflight.clear()


@pytest.fixture
def safe(monkeypatch):
    from app import tv_safety
    monkeypatch.setattr(tv_safety, 'check', lambda *a, **k: None)


def test_two_runs_second_release_is_not_sent(qenv, safe):  # noqa: F811
    q = FakeQbit()
    assert _g(q, qenv, FLUX)['ok']
    out = _g(q, qenv, RAWR, now=5900.0)                     # next feed run, another tracker's release
    assert out == {'ok': False, 'error': tv_grab.ALREADY, 'claimed': True, 'by': out['by']}
    assert len(q.torrents) == 1


def test_two_callers_manual_after_auto_is_not_sent(qenv, safe):  # noqa: F811
    q = FakeQbit()
    assert _g(q, qenv, FLUX, origin='auto')['ok']
    assert _g(q, qenv, RAWR, origin='manual')['claimed']
    assert len(q.torrents) == 1


def test_concurrent_threads_only_one_add_reaches_qbit(qenv, safe):  # noqa: F811
    q = FakeQbit()
    real = q.factory
    gate = threading.Event()

    def slow():
        s = real()
        post = s.post

        def p(url, *a, **k):
            if url.endswith('/torrents/add'):
                gate.wait(2)
            return post(url, *a, **k)
        s.post = p
        return s
    q.factory = slow
    outs = []
    ts = [threading.Thread(target=lambda p=p: outs.append(_g(q, qenv, p))) for p in (FLUX, RAWR)]
    for t in ts:
        t.start()
    time.sleep(0.3)
    gate.set()
    for t in ts:
        t.join(5)
    assert sorted(bool(o.get('ok')) for o in outs) == [False, True]
    assert len(q.torrents) == 1
    assert tv_grab._inflight == {}


def test_pre_qbit_failure_does_not_claim(qenv, safe):  # noqa: F811
    q = FakeQbit()
    real = q.factory

    def down():
        s = real()
        post = s.post

        def p(url, *a, **k):
            if url.endswith('/torrents/add'):
                raise requests.ConnectionError('down')
            return post(url, *a, **k)
        s.post = p
        return s
    q.factory = down
    assert _g(q, qenv, FLUX).get('client')
    q.factory = real
    assert _g(q, qenv, RAWR)['ok']                           # the next tracker may still grab it
    assert tv_store.get_meta(f'{tv_grab.CLAIM_KEY}{eps(qenv, 2)[3]}') is None


def test_real_qbit_refusal_claims_for_a_while(qenv, safe):  # noqa: F811
    q = FakeQbit()
    real = q.factory

    def refuse():
        s = real()
        post = s.post

        def p(url, *a, **k):
            if url.endswith('/torrents/add'):
                return Resp(200, '{"added_torrent_ids":[],"failure_count":1}')
            return post(url, *a, **k)
        s.post = p
        return s
    q.factory = refuse
    assert not _g(q, qenv, FLUX)['ok']
    q.factory = real
    assert _g(q, qenv, RAWR, now=5100.0)['claimed']
    assert _g(q, qenv, RAWR, now=5000.0 + tv_grab.CLAIM_REFUSED_TTL + 1)['ok']


def test_other_episode_is_not_blocked(qenv, safe):  # noqa: F811
    q = FakeQbit()
    assert _g(q, qenv, FLUX, ep=3)['ok']
    assert _g(q, qenv, RAWR, ep=4)['ok']


def test_failed_grab_row_releases_the_claim(qenv, safe):  # noqa: F811
    q = FakeQbit()
    h = _g(q, qenv, FLUX)['hash']
    with tv_store.write() as conn:
        conn.execute("UPDATE grabs SET state = 'failed' WHERE hash = ?", (h,))
    assert _g(q, qenv, RAWR)['ok']


def test_second_import_of_same_episode_sends_one_push(monkeypatch, tv_env):  # noqa: F811
    sent = []
    monkeypatch.setattr(tv_downloads, '_notify_imported', lambda res: sent.append(res))
    res = {'episode_ids': [42], 'files': [{'path': '/media/TV/M/S02E07.mkv', 'how': 'copied'}]}
    for r in (res, {**res, 'files': [{'path': '/media/TV/M/S02E07.mkv', 'how': 'copied'}]}):
        if tv_downloads._first_notify(r, now=1000.0):
            tv_downloads._notify_imported(r)
    assert len(sent) == 1
    other = {'episode_ids': [42], 'files': [{'path': '/media/TV/M/S02E07.new.mkv', 'how': 'copied'}]}
    assert tv_downloads._first_notify(other, now=1001.0)        # a real replacement file is announced
    assert tv_downloads._first_notify(res, now=1000.0 + tv_downloads.NOTIFY_WINDOW + 5)


def test_tick_uses_the_per_episode_push_guard():
    src = open(tv_downloads.__file__).read()
    i = src.index("_notify_imported(res)\n                    except")
    assert 'elif not _first_notify(res):' in src[i - 400:i]


def _refuse_factory(real):
    def refuse():
        s = real()
        post = s.post

        def p(url, *a, **k):
            if url.endswith('/torrents/add'):
                return Resp(200, '{"added_torrent_ids":[],"failure_count":1}')
            return post(url, *a, **k)
        s.post = p
        return s
    return refuse


def test_manual_grab_is_not_held_by_a_refusal_claim(qenv, safe):  # noqa: F811
    q = FakeQbit()
    real = q.factory
    q.factory = _refuse_factory(real)
    assert not _g(q, qenv, FLUX)['ok']
    q.factory = real
    assert _g(q, qenv, RAWR, now=5100.0)['claimed']                  # auto still held
    r = _g(q, qenv, RAWR, origin='manual', now=5100.0)
    assert r['ok'] and not r.get('existing') and not r.get('claimed')


def test_manual_grab_held_by_active_grab_shows_existing(qenv, safe):  # noqa: F811
    q = FakeQbit()
    first = _g(q, qenv, FLUX)
    assert first['ok']
    r = _g(q, qenv, RAWR, origin='manual', now=5100.0)
    assert r['ok'] and r['existing'] and r['claimed'] and r['hash'] == first['hash']
