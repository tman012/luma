"""v170 fix-forwards (Reviewer on fbe2e55, PM rulings). Fake trackers only.

2) tracker_guard.failure: a rate or strict-400 backoff is max(schedule, Retry-After).
A) AvistaZ-family id-search 400: a plain 400 keeps the strict backoff and sends no fallback;
   a 400 that clearly says the id is invalid starts no backoff, records the rejected id and
   sends the (charged) title search, and that id isn't sent again for that title.
4) Plex XML parses go through safe_xml.
"""
import pathlib
import re

import pytest

from app import tracker_clients, tracker_guard
from app.tracker_guard import TrackerGuard, guard_key
from app.tracker_http import TrackerError
from tests.test_tracker_search import R, env  # noqa: F401


# ------------------------------------------------------------------ 2) Retry-After can't shrink the floor

def test_short_retry_after_cannot_shrink_a_rate_backoff(tmp_path):
    g = TrackerGuard(tmp_path / 'g.json')
    until = g.failure('site:beyondhd', 'rate', retry_after=30, now=1000.0)
    assert until == 1000.0 + tracker_guard.LIMITS['rate_s']
    long = g.failure('site:cinemaz', 'rate', retry_after=5 * 3600, now=1000.0)
    assert long == 1000.0 + 5 * 3600                     # a longer one still wins


def test_short_retry_after_cannot_shrink_a_strict_400(tmp_path):
    g = TrackerGuard(tmp_path / 'g.json')
    err = TrackerError('http', 400, retry_after=10)
    gk = 'site:avistaz'
    assert tracker_guard.strict_400(gk, err)
    until = tracker_guard.record_failure(g, gk, err)
    import time
    assert until - time.time() == pytest.approx(tracker_guard.LIMITS['rate_s'], abs=5)


# ------------------------------------------------------------------ A) AvistaZ id-search 400

@pytest.fixture
def avz(env):  # noqa: F811
    tracker_clients.clear_avz_rejected_ids()
    rec, errors = env['store'].create({'type': 'avistaz', 'name': 'AvZ', 'url': 'https://avz.example',
                                       'username': 'me', 'password': 'pw_secret_1', 'pid': 'pid_secret_1',
                                       'categories': [2000], 'priority': 5})
    assert not errors
    yield env, env['store'].config(rec)
    tracker_clients.clear_avz_rejected_ids()


def _gets(env):
    return [c['url'] for c in env['mock'].calls if c['method'] == 'GET']


def _sends(env, gk):
    env['guard']._load()
    return len(env['guard'].log.get(gk, []))


def test_plain_400_on_an_id_search_backs_off_and_sends_no_fallback(avz):
    env, cfg = avz
    env['mock'].set('avz.example', R(body={'token': 'tok', 'expiry': 3600}), R(400, body={'message': 'Bad request'}))
    with pytest.raises(TrackerError):
        env['engine'].search_one(cfg, 'Heat', [2000], 10, True, imdb_id='tt0113277')
    gets = _gets(env)
    assert len(gets) == 1 and 'imdb=tt0113277' in gets[0]          # no title fallback
    cd = env['guard'].blocked(guard_key(cfg))
    assert cd and cd[1] == 'rate'                                   # the strict backoff (1h -> 6h)
    assert not tracker_clients.avz_id_rejected(cfg, 'imdb', 'tt0113277', 'Heat')


def test_invalid_id_400_falls_back_to_a_charged_title_search_without_backoff(avz):
    env, cfg = avz
    gk = guard_key(cfg)
    env['mock'].set('avz.example', R(body={'token': 'tok', 'expiry': 3600}),
                    R(400, body={'message': 'The imdb id is invalid.'}),
                    R(body={'data': [{'file_name': 'Heat.1995.1080p.BluRay-GRP', 'download': 'https://avz.example/dl/1',
                                      'file_size': 1, 'seed': 5, 'leech': 0}]}))
    items = env['engine'].search_one(cfg, 'Heat', [2000], 10, True, imdb_id='tt0113277')
    assert [i['title'] for i in items] == ['Heat.1995.1080p.BluRay-GRP']
    gets = _gets(env)
    assert len(gets) == 2 and 'imdb=' in gets[0] and 'imdb=' not in gets[1] and 'search=Heat' in gets[1]
    assert env['guard'].blocked(gk) is None                         # no backoff
    assert _sends(env, gk) == 3                                     # sign-in, id search, title search: all charged
    assert tracker_clients.avz_id_rejected(cfg, 'imdb', 'tt0113277', 'Heat')
    # the rejected id isn't sent again for that title
    env['mock'].calls.clear()
    env['mock'].set('avz.example', R(body={'data': []}))
    env['engine'].search_one(cfg, 'Heat', [2000], 20, True, imdb_id='tt0113277', refresh=True)
    gets = _gets(env)
    assert len(gets) == 1 and 'imdb=' not in gets[0] and 'search=Heat' in gets[0]
    # another title still searches by its id
    env['mock'].calls.clear()
    env['engine'].search_one(cfg, 'Ronin', [2000], 10, True, imdb_id='tt0122690')
    assert 'imdb=tt0122690' in _gets(env)[0]


def test_invalid_id_fallback_respects_the_budget(avz, monkeypatch):
    env, cfg = avz
    env['mock'].set('avz.example', R(body={'token': 'tok', 'expiry': 3600}),
                    R(400, body={'error': 'Invalid IMDb id'}), R(body={'data': []}))
    real_take = env['guard'].take
    calls = {'n': 0}

    def take(*a, **kw):
        calls['n'] += 1
        return 'Daily limit reached' if calls['n'] >= 3 else real_take(*a, **kw)
    monkeypatch.setattr(env['guard'], 'take', take)
    from app.tracker_search import TrackerSkipped
    with pytest.raises(TrackerSkipped):
        env['engine'].search_one(cfg, 'Heat', [2000], 10, True, imdb_id='tt0113277')
    assert len(_gets(env)) == 1                                     # the fallback was refused, never sent


@pytest.mark.parametrize('body,rejected', [
    ('{"message":"The imdb id is invalid."}', True),
    ('{"error":"Invalid TMDB id"}', True),
    ('{"message":"tmdb_id not found"}', True),
    ('{"message":"Bad request"}', False),
    ('{"message":"Too many requests, slow down"}', False),
    ('{"message":"invalid search"}', False),
])
def test_only_a_clear_invalid_id_body_counts(body, rejected):
    assert bool(tracker_clients.AVZ_ID_REJECTED_RE.search(body)) is rejected


# ------------------------------------------------------------------ 4) Plex XML through safe_xml

def test_plex_xml_parses_go_through_safe_xml():
    """Every XML parse in the app (Plex included) goes through safe_xml; stdlib parsers only there."""
    root = pathlib.Path(__file__).resolve().parents[1] / 'app'
    offenders = []
    for f in root.rglob('*.py'):
        if f.name == 'safe_xml.py':
            continue
        for n, line in enumerate(f.read_text(encoding='utf-8').splitlines(), 1):
            if re.search(r'\b(ET|ElementTree|etree|minidom|cElementTree)\.(fromstring|parse|XML|parseString|XMLParser|iterparse)\(', line):
                offenders.append(f'{f.name}:{n}')
    assert offenders == []
    for name in ('plex_service.py', 'plex_match.py', 'plex_api_on_deck.py'):
        assert '_safe_xml.fromstring(' in (root / name).read_text(encoding='utf-8')


def test_plex_parse_refuses_an_entity_bomb():
    from app import plex_service  # noqa: F401 - the module the Plex client parses with
    from app import safe_xml
    bomb = ('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">'
            '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;">]><MediaContainer title="&c;"/>')
    with pytest.raises(safe_xml.ParseError):
        safe_xml.fromstring(bomb)


# ------------------------------------------------------------------ B) M34: a skipped path reads 'Unavailable'

from tests.test_tv_scan import PID, lib  # noqa: E402,F401
from tests.test_file_management import store  # noqa: E402,F401
from tests.test_tv_libraries import tv_env  # noqa: E402,F401
from tests.tv_fake import fake_tmdb  # noqa: E402,F401


def test_a_skipped_path_reads_unavailable_until_a_scan_succeeds(lib, tmp_path):  # noqa: F811
    import shutil
    from app import file_management as fm, library_selection, tv_scan, tv_settings
    tv_scan.scan_all(now=1000.0)
    rec = next(p for p in fm.load()['paths'] if p['id'] == PID)
    assert tv_settings.tv_path_status(rec) in ('ok', 'read_only')
    away = tmp_path / 'away'
    shutil.move(str(lib), str(away))
    lib.mkdir()
    (lib / 'lost+found').mkdir()                              # system folders only: skipped
    assert tv_scan.scan_all(now=2000.0)['missing'] == [PID]
    assert tv_settings.tv_path_status(rec) == 'unavailable'
    assert [p['status'] for p in tv_settings.view()['paths'] if p['id'] == PID] == ['unavailable']
    sel = library_selection.view([PID], fm.load()['paths'], tv_settings.tv_path_status)
    assert [l['status'] for l in sel['libraries'] if l['id'] == PID] == ['unavailable']
    assert library_selection.view([PID], fm.load()['paths'])['libraries'][0]['status'] != 'unavailable'  # Movies: plain
    shutil.rmtree(lib)
    shutil.move(str(away), str(lib))
    assert tv_scan.scan_all(now=3000.0)['missing'] == []
    assert tv_settings.tv_path_status(rec) in ('ok', 'read_only')


# ------------------------------------------------------------------ v171 (Tester): forbid_dtd

def test_safe_xml_refuses_an_external_dtd_and_any_doctype(monkeypatch):
    import socket
    from app import safe_xml
    connects = []
    monkeypatch.setattr(socket.socket, 'connect', lambda self, a: connects.append(a))
    for doc in ('<?xml version="1.0"?><!DOCTYPE rss SYSTEM "http://203.0.113.9/evil.dtd"><rss/>',
                '<?xml version="1.0"?><!DOCTYPE MediaContainer PUBLIC "-//x//y" "file:///etc/passwd"><MediaContainer/>',
                '<!DOCTYPE rss><rss/>'):
        with pytest.raises(safe_xml.ParseError):
            safe_xml.fromstring(doc)
    assert connects == []
    assert safe_xml.fromstring('<?xml version="1.0"?><MediaContainer size="1"><Video title="Heat"/></MediaContainer>')[0].get('title') == 'Heat'
