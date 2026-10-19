"""v184 Tester fix-forwards: Add series with no monitor stores Future; the detail page counts like the list;
the main.py Telegram notifies honour the test write guard; junk or oversized JSON on TV write routes is a 400
(never a 5xx); a same-origin Origin with a cross-site Sec-Fetch-Site is refused."""
import json
import re

import pytest

from app import tv_monitor
from tests.test_file_management import minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.test_tv_search_v158b import env, item  # noqa: F401
from tests.test_v182_series_detail import HDR, REMOTE, _req, spies  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

TV = ('/api/settings/tv', '/api/tv/')
JUNK = (b'not json', b'{"monitored": ', b'[' * 50000 + b']' * 50000, b'"' + b'x' * (3 << 20) + b'"',
        b'{"monitored": "' + b'x' * (3 << 20) + b'"}', b'\xff\xfe\x00junk', b'[]', b'"str"', b'7', b'null')


def _write_routes(app, env):
    out = []
    for r in app.url_map.iter_rules():
        if not r.rule.startswith(TV):
            continue
        for m in sorted((r.methods or set()) - {'GET', 'HEAD', 'OPTIONS'}):
            u = re.sub(r'<(?:int:)?sid>', str(env.sid), r.rule)
            u = re.sub(r'<(?:int:)?season>', '1', u)
            u = re.sub(r'<[^>]+>', str(next(iter(env.e2.values()))), u)
            out.append((m.lower(), u))
    return sorted(out, key=lambda x: x[0] == 'delete')   # deletes last


def test_junk_or_oversized_json_on_tv_write_routes_is_400_never_5xx(minimal_app, owner, env, spies):  # noqa: F811
    routes = _write_routes(minimal_app, env)
    assert len(routes) >= 20
    bad = []
    for m, u in routes:
        for body in JUNK:
            r = getattr(owner, m)(u, data=body, content_type='application/json', environ_base=REMOTE, headers=HDR)
            if r.status_code >= 500 or (len(body) > 1 << 20 and r.status_code < 400):
                bad.append((m, u, body[:12], r.status_code))
    assert bad == []
    assert spies == [] and env.sent == []


def test_add_series_with_no_monitor_stores_future(owner, env):  # noqa: F811
    assert tv_monitor.ADD_DEFAULT == 'future'
    r = _req(owner, 'post', '/api/settings/tv/shows', {'tmdb_id': 6001})
    assert r.status_code in (200, 201), r.get_json()
    got = rows('SELECT monitor FROM series WHERE tmdb_id = 6001')
    assert got and got[0]['monitor'] == 'future'


def test_the_detail_page_counts_aired_episodes_like_the_list(owner, env):  # noqa: F811
    from app import tv_store
    with tv_store.connect() as c:
        c.execute('UPDATE episodes SET monitored = 0 WHERE series_id = ?', (env.sid,))
    d = _req(owner, 'get', f'/api/settings/tv/shows/{env.sid}/detail').get_json()
    from app import tv_scan
    lst = [s for s in tv_scan.library_view()['shows'] if s['id'] == env.sid][0]
    assert d['total'] == lst['aired'] > 0 and d['have'] == min(lst['have'], lst['aired'])
    assert sum(s['total'] for s in d['seasons'] if s['season'] > 0) == d['total']


@pytest.mark.parametrize('fn', ['_send_account_request_notification', '_send_plex_access_request_notification'])
def test_the_main_telegram_notifies_are_blocked_on_test(fn, monkeypatch, tmp_path):
    from app import main
    sent = []
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.delenv('LUMA_TEST_ALLOW', raising=False)
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: sent.append(a))
    monkeypatch.setattr(main, '_telegram_admin_chat_id', lambda: '42')
    cfgp = tmp_path / 'telegram.json'
    cfgp.write_text(json.dumps({'bot_token': '123:abc'}))
    monkeypatch.setattr(main, 'telegram_bot_config_path', lambda: cfgp)
    src = open(main.__file__).read()
    body = src[src.index(f'def {fn}('):]
    body = body[:body.index('\ndef ', 10)]
    assert "writes_allowed('telegram')" in body.split('api.telegram.org')[0]
    getattr(main, fn)(7, 'Pat', 'pat@example.com')
    assert sent == []
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'telegram')      # opted in: it does send
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: sent.append(a) or (_ for _ in ()).throw(OSError('stop')))
    getattr(main, fn)(7, 'Pat', 'pat@example.com')
    assert len(sent) == 1


def test_same_origin_with_cross_site_fetch_metadata_is_refused(owner, env, spies):  # noqa: F811
    u, b = f'/api/settings/tv/shows/{env.sid}/monitored', {'monitored': False}
    for site in ('cross-site', 'same-site'):
        r = owner.put(u, json=b, environ_base=REMOTE, headers={**HDR, 'Sec-Fetch-Site': site})
        assert r.status_code == 403, site
    assert rows('SELECT monitored FROM series WHERE id = ?', env.sid)[0]['monitored'] == 1
    r = owner.put(u, json=b, environ_base=REMOTE, headers={**HDR, 'Sec-Fetch-Site': 'same-origin'})
    assert r.status_code == 200


# ---------------------------------------------------------------- v184 (PM, live :5002): a file on disk is Downloaded

def _unlinked_aired(sid):
    from app import tv_store
    with tv_store.connect() as c:
        return c.execute("SELECT id, season_number AS s, episode_number AS n FROM episodes e WHERE series_id = ? AND season_number > 0"
                         " AND air_date <= date('now') AND NOT EXISTS (SELECT 1 FROM episode_file_links l WHERE l.episode_id = e.id)"
                         " ORDER BY id LIMIT 1", (sid,)).fetchone()


def _grab(sid, eid, h='a' * 40):
    from app import tv_store
    with tv_store.write() as c:
        c.execute("INSERT INTO grabs (hash, series_id, season_number, episode_ids, release_title, origin, grabbed_at, state)"
                  " VALUES (?, ?, 3, ?, 'Time.S03E03.1080p.WEBRip.X264-DEADPOOL', 'auto', 1, 'downloading')", (h, sid, json.dumps([eid])))


def _state(sid, eid):
    return [e for e in tv_monitor.detail_view(sid)['episodes'] if e['id'] == eid][0]['state']


def test_a_stuck_grab_row_never_turns_a_file_on_disk_back_to_downloading(env, tmp_path, monkeypatch):  # noqa: F811
    from app import tv_downloads, tv_plex
    e = _unlinked_aired(env.sid)
    assert e is not None
    _grab(env.sid, e['id'])
    assert _state(env.sid, e['id']) == 'downloading'              # no file anywhere: the open grab answers
    f = tmp_path / 'Time.S03E03.1080p.WEBRip.X264-DEADPOOL.mkv'
    f.write_bytes(b'x')
    rec = {'state': 'imported', 'files': [{'path': str(f), 'how': 'hardlink', 'linked': True, 'episode_ids': [e['id']]}]}
    monkeypatch.setattr(tv_downloads, 'status', lambda h: rec if h == 'a' * 40 else None)
    assert _state(env.sid, e['id']) == 'downloaded'               # Luma's import put it on disk (link not caught up)
    d = tv_monitor.detail_view(env.sid)
    assert any(s['have'] >= 1 for s in d['seasons'])
    f.unlink()
    assert _state(env.sid, e['id']) == 'missing'                  # deleted since: Missing, never a stale Downloading
    monkeypatch.setattr(tv_plex, 'has', lambda *a: True)
    assert _state(env.sid, e['id']) == 'downloaded'               # Plex has it (as the calendar says)


def test_a_linked_file_wins_over_an_open_grab(env):  # noqa: F811
    from app import tv_store
    with tv_store.connect() as c:
        eid = c.execute("SELECT l.episode_id FROM episode_file_links l JOIN episode_files f ON f.id = l.file_id"
                        " WHERE f.series_id = ? AND f.state = 'linked' LIMIT 1", (env.sid,)).fetchone()['episode_id']
    _grab(env.sid, eid, 'b' * 40)
    assert _state(env.sid, eid) == 'downloaded'


# ---------------------------------------------------------------- v184 (PM): the monitor cascade, never a search

def _mon(sid):
    return {(r['season_number'], r['episode_number']): r['monitored'] for r in rows(
        'SELECT season_number, episode_number, monitored FROM episodes WHERE series_id = ?', sid)}


def test_the_monitor_cascade(owner, env, spies):  # noqa: F811
    from app import tv_store
    sid = env.sid
    with tv_store.write() as c:
        c.execute("UPDATE series SET monitor = 'future' WHERE id = ?", (sid,))
        c.execute('UPDATE episodes SET monitored = 1 WHERE series_id = ?', (sid,))
        tv_monitor.sync_seasons(c, sid)
    # a season off: its episodes off, the others untouched
    assert _req(owner, 'put', f'/api/settings/tv/shows/{sid}/seasons/2/monitored', {'monitored': False}).status_code == 200
    m = _mon(sid)
    assert all(v == 0 for (s, _), v in m.items() if s == 2) and any(v == 1 for (s, _), v in m.items() if s != 2)
    # the season back on: its episodes on again
    assert _req(owner, 'put', f'/api/settings/tv/shows/{sid}/seasons/2/monitored', {'monitored': True}).status_code == 200
    assert all(v == 1 for (s, _), v in _mon(sid).items() if s == 2)
    # the show off: every season and episode off; the saved option kept
    assert _req(owner, 'put', f'/api/settings/tv/shows/{sid}/monitored', {'monitored': False}).status_code == 200
    assert set(_mon(sid).values()) == {0}
    assert {r['monitored'] for r in rows('SELECT monitored FROM seasons WHERE series_id = ?', sid)} == {0}
    assert rows('SELECT monitored, monitor FROM series WHERE id = ?', sid)[0] == {'monitored': 0, 'monitor': 'future'}
    # the show on: its saved Monitor option (Future), not All
    assert _req(owner, 'put', f'/api/settings/tv/shows/{sid}/monitored', {'monitored': True}).status_code == 200
    today = __import__('datetime').date.today().isoformat()
    for r in rows('SELECT season_number, air_date, monitored FROM episodes WHERE series_id = ? AND season_number > 0', sid):
        assert r['monitored'] == int(r['air_date'] is None or r['air_date'] >= today), r
    assert rows('SELECT monitored FROM series WHERE id = ?', sid)[0]['monitored'] == 1
    assert spies == [] and env.sent == []


def test_the_calendar_answer_marks_unmonitored_episodes_seasons_and_shows(owner, env):  # noqa: F811
    import datetime as dt
    from app import tv_calendar, tv_store
    sid = env.sid
    with tv_store.write() as c:
        c.execute('UPDATE episodes SET monitored = 1 WHERE series_id = ?', (sid,))
        c.execute('UPDATE series SET monitored = 1 WHERE id = ?', (sid,))
    first = rows('SELECT MIN(air_date) AS d FROM episodes WHERE series_id = ? AND air_date IS NOT NULL', sid)[0]['d']
    start = dt.date.fromisoformat(first) if first else dt.date.today()
    cal = lambda: {e['episode_id']: e['monitored'] for e in tv_calendar.calendar(start, 62)['episodes'] if e['series_id'] == sid}  # noqa: E731
    before = cal()
    if not before:
        pytest.skip('no dated episodes in the calendar window')
    one = next(iter(before))
    _req(owner, 'put', f'/api/settings/tv/episodes/{one}/monitored', {'monitored': False})
    assert cal()[one] is False
    season = rows('SELECT season_number FROM episodes WHERE id = ?', one)[0]['season_number']
    _req(owner, 'put', f'/api/settings/tv/shows/{sid}/seasons/{season}/monitored', {'monitored': False})
    ids = {r['id'] for r in rows('SELECT id FROM episodes WHERE series_id = ? AND season_number = ?', sid, season)}
    assert all(v is False for k, v in cal().items() if k in ids)
    _req(owner, 'put', f'/api/settings/tv/shows/{sid}/monitored', {'monitored': False})
    assert set(cal().values()) == {False}
