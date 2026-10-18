"""Tracker port step 1 (v169, parity guard). Fake trackers only.

* PixelHD is a site type (AvistaZ network): strict bucket, TV cap, its own budget key.
* The strict 400 cooldown of the Prowlarr path (indexer_api GUARD_LIMITS) is ported: a 400 from
  the AvistaZ family or IPTorrents starts a rate backoff, 1 h doubling to 6 h (or Retry-After).
* Every tracker XML parse goes through defusedxml (app.safe_xml): entity bombs and external
  entities are refused, and nothing in the tracker modules calls the stdlib parser directly.
* Parity: the shipped rate limits, the per-capped-tracker TV cap (8 a day) and the spacing
  (8 slots 3 h apart; at least 600 s between automatic requests; 2 s between any two requests to
  one tracker) are asserted as numbers AND as behavior on a fake clock. Nothing here raises a cap.
"""
import pathlib
import re
import time

import pytest

from app import safe_xml, tracker_guard, tracker_types as tt, tv_budget
from app.tracker_guard import TrackerGuard, guard_key, record_failure, strict_400
from app.tracker_http import Response, TrackerError, raise_for_status
from tests.test_tracker_search import R, env, ipt, rss, torznab  # noqa: F401

HOUR, DAY = 3600, 86400
T0 = 1_800_000_000.0
APP = pathlib.Path(__file__).resolve().parent.parent / 'app'

# v172 (tracker port step 4): the Prowlarr path (indexer_api.GUARD_LIMITS) is gone. These are the
# numbers it shipped with, frozen here so the native limits stay pinned to them (never raised).
PROWLARR_PATH_LIMITS = {
    'interactive_per_hour': 40, 'background_burst': 5, 'background_refill_s': 180.0, 'per_day': 300,
    'global_per_hour': 120, 'min_interval_s': 2.0, 'cooldown_429_s': 3600, 'cooldown_400_s': 900,
    'cooldown_400_strict_s': 3600, 'cooldown_max_s': 6 * 3600,
    'strict_400_definitions': ('avistaz', 'privatehd', 'pixelhd', 'cinemaz', 'exoticaz', 'iptorrents'),
}


def site(t):
    return {'type': t, 'url': (tt.get_type(t) or {}).get('fields', [{}, {}])[1].get('default') or 'https://x.example'}


# ------------------------------------------------------------------ PixelHD

def test_pixelhd_is_an_avistaz_network_site_type():
    t = tt.get_type('pixelhd')
    assert t and t['name'] == 'PixelHD' and t['client'] == 'avistaz' and t['family'] == 'avistaz'
    fields = {f['key']: f for f in t['fields']}
    assert fields['url']['default'] == 'https://pixelhd.me'
    assert {k for k, f in fields.items() if f['kind'] == 'secret'} == {'password', 'pid'}
    assert tt.is_rate_strict('pixelhd') and tt.is_strict_400('pixelhd') and not tt.is_generic('pixelhd')
    assert guard_key({'type': 'pixelhd', 'url': 'https://pixelhd.me'}) == 'site:pixelhd'
    assert guard_key({'type': 'pixelhd', 'url': 'https://mirror.example'}) == 'site:pixelhd'   # one budget
    assert guard_key({'type': 'privatehd', 'url': 'https://pixelhd.me'}) == 'site:privatehd'    # its own
    assert tv_budget.capped({'type': 'pixelhd'})


def test_pixelhd_is_not_preconfigured(env):
    assert env['store'].list() == []


# ------------------------------------------------------------------ strict 400 cooldown

def test_strict_400_types_match_the_prowlarr_paths_list():
    assert set(tt.STRICT_400_TYPES) == set(PROWLARR_PATH_LIMITS['strict_400_definitions'])
    assert set(tt.STRICT_400_TYPES) == {'avistaz', 'cinemaz', 'exoticaz', 'iptorrents', 'pixelhd', 'privatehd'}
    for t in tt.STRICT_400_TYPES:
        assert tt.get_type(t) is not None, t
    for t in ('beyondhd', 'morethantv', 'torznab', 'newznab'):
        assert not tt.is_strict_400(t)


@pytest.mark.parametrize('t', ['avistaz', 'cinemaz', 'exoticaz', 'iptorrents', 'pixelhd', 'privatehd'])
def test_a_400_from_a_strict_site_backs_off_1h_doubling_to_6h(tmp_path, t):
    g = TrackerGuard(tmp_path / 'g.json')
    gk = guard_key(site(t))
    waits = []
    for i in range(6):
        now = T0 + i * 10 * DAY
        g.cooldown.pop(gk, None)
        orig = time.time
        try:
            time.time = lambda: now
            until = record_failure(g, gk, TrackerError('http', 400))
        finally:
            time.time = orig
        waits.append(until - now)
        assert g.blocked(gk, now)[1] == 'rate'
        assert g.take(gk, interactive=True, strict=True, now=now + 1) == 'backoff (rate)'
    # the Prowlarr path: cooldown_400_strict_s 3600, doubled per consecutive 400, cooldown_max_s 6 h
    assert waits == [3600, 7200, 14400, 21600, 21600, 21600]
    assert PROWLARR_PATH_LIMITS['cooldown_400_strict_s'] == 3600
    assert PROWLARR_PATH_LIMITS['cooldown_max_s'] == 6 * HOUR


def test_a_strict_400_honors_a_longer_retry_after(tmp_path):
    g = TrackerGuard(tmp_path / 'g.json')
    err = None
    try:
        raise_for_status(Response(400, {'Retry-After': '9000'}, b'slow down'))
    except TrackerError as e:
        err = e
    assert err.kind == 'http' and err.status == 400 and err.retry_after == 9000
    until = record_failure(g, 'site:privatehd', err)
    assert g.cooldown['site:privatehd'][1] == 'rate' and until - time.time() == pytest.approx(9000, abs=5)


def test_a_400_from_other_sites_backs_off_as_on_the_prowlarr_path(tmp_path):
    """v170 (PM): 15 min doubling to 6 h (indexer_api cooldown_400_s / cooldown_max_s), the stricter value
    winning over the native http backoff (5 min doubling to 1 h)."""
    for gk in ('site:beyondhd', 'site:morethantv', 'host:t.example'):
        g = TrackerGuard(tmp_path / f'{gk.replace(":", "_")}.json')
        assert not strict_400(gk, TrackerError('http', 400))
        waits = []
        for i in range(7):
            now = T0 + i * 10 * DAY
            g.cooldown.pop(gk, None)
            orig = time.time
            try:
                time.time = lambda: now
                until = record_failure(g, gk, TrackerError('http', 400))
            finally:
                time.time = orig
            waits.append(until - now)
            assert g.cooldown[gk][1] == 'http'
        assert waits == [900, 1800, 3600, 7200, 14400, 21600, 21600]
    assert (PROWLARR_PATH_LIMITS['cooldown_400_s'], PROWLARR_PATH_LIMITS['cooldown_max_s']) == (900, 6 * HOUR)
    assert (tracker_guard.LIMITS['http400_s'], tracker_guard.LIMITS['http400_max_s']) == (900, 6 * HOUR)
    g = TrackerGuard(tmp_path / 'other.json')              # other 5xx/4xx keep the native http backoff
    until = record_failure(g, 'site:beyondhd', TrackerError('http', 500))
    assert until - time.time() == pytest.approx(300, abs=5)
    assert not strict_400('site:iptorrents', TrackerError('http', 500))
    assert not strict_400('site:iptorrents', TrackerError('auth', 400))


def test_a_strict_400_on_a_search_blocks_the_next_one_without_a_request(env):
    rec = ipt(env['store'])
    env['mock'].set('ipt.example', R(400, 'bad request'))
    cfg = env['store'].config(env['store'].get(rec['id']))
    with pytest.raises(TrackerError):
        env['engine'].search_one(cfg, 'The Nut Job', [2000], 10, interactive=True)
    n = len(env['mock'].calls)
    assert env['guard'].blocked('site:iptorrents')[1] == 'rate'
    with pytest.raises(Exception) as e:
        env['engine'].search_one(cfg, 'Another Film', [2000], 10, interactive=True)
    assert 'rate' in str(getattr(e.value, 'reason', e.value)).lower() or 'backoff' in str(getattr(e.value, 'reason', '')).lower()
    assert len(env['mock'].calls) == n                       # refused before the wire


def test_a_strict_400_on_a_test_starts_the_backoff_too(env):
    calls = []

    def run(cfg):
        calls.append(1)
        raise TrackerError('http', 400)
    cfg = {'type': 'iptorrents', 'url': 'https://ipt.example'}
    with pytest.raises(TrackerError):
        tracker_guard.guarded_test(cfg, run, env['guard'])
    with pytest.raises(TrackerError) as e:
        tracker_guard.guarded_test(cfg, run, env['guard'])
    assert e.value.kind == 'rate' and calls == [1]


# ------------------------------------------------------------------ defusedxml

BOMB = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        + ''.join(f'<!ENTITY lol{i} "{("&lol%s;" % (i - 1 if i > 1 else "")) * 10}">' for i in range(1, 10))
        + ']><rss><channel><item><title>&lol9;</title></item></channel></rss>')
XXE = ('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
       '<rss><channel><item><title>&x;</title></item></channel></rss>')


def test_defusedxml_is_installed_and_psf():
    import importlib.metadata as m
    assert 'PSF' in (m.metadata('defusedxml')['License'] or '')
    req = (APP.parent / 'requirements.txt').read_text()
    assert re.search(r'^defusedxml[<>=]', req, re.M)


@pytest.mark.parametrize('doc', [BOMB, XXE, BOMB.encode()])
def test_entity_bombs_and_external_entities_are_refused(doc):
    t = time.monotonic()
    with pytest.raises(safe_xml.ParseError):
        safe_xml.fromstring(doc)
    assert time.monotonic() - t < 1.0


def test_a_missing_defusedxml_fails_closed(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_defused(name, *a, **kw):
        if name.startswith('defusedxml'):
            raise ImportError(name)
        return real(name, *a, **kw)
    monkeypatch.setattr(builtins, '__import__', no_defused)
    with pytest.raises(safe_xml.ParseError):
        safe_xml.fromstring('<rss/>')


def test_a_tracker_sending_an_entity_bomb_is_a_bad_answer_with_no_rows(env):
    rec = torznab(env['store'])
    env['mock'].set('t.example', R(200, BOMB, {'Content-Type': 'application/rss+xml'}))
    cfg = env['store'].config(env['store'].get(rec['id']))
    with pytest.raises(TrackerError) as e:
        env['engine'].search_one(cfg, 'The Nut Job', [2000], 10, interactive=True)
    assert e.value.kind == 'response'


def test_a_normal_feed_still_parses(env):
    rec = torznab(env['store'])
    env['mock'].set('t.example', R(200, rss(('The.Nut.Job.2014.1080p', 12, 4_000_000_000))))
    cfg = env['store'].config(env['store'].get(rec['id']))
    assert len(env['engine'].search_one(cfg, 'The Nut Job', [2000], 10, interactive=True)) == 1


def test_no_tracker_module_parses_xml_with_the_stdlib():
    mods = sorted(APP.glob('tracker_*.py')) + [APP / 'indexer_api.py', APP / 'tracker_search.py']
    bad = []
    for p in mods:
        src = p.read_text()
        for pat in (r'\bET\.(fromstring|parse|XML|iterparse|XMLParser)\(', r'\bminidom\b', r'\blxml\b',
                    r'\bxml\.sax\b', r'\bexpat\b', r'ElementTree\.(fromstring|parse)\('):
            bad += [f'{p.name}: {m.group(0)}' for m in re.finditer(pat, src)]
    assert bad == []


# ------------------------------------------------------------------ parity: the shipped numbers

def test_parity_numbers_are_the_shipped_ones():
    L = tracker_guard.LIMITS
    assert (L['interactive_per_hour'], L['burst'], L['refill_s'], L['per_day'], L['global_per_hour'],
            L['min_interval_s']) == (40, 5, 180.0, 300, 120, 2.0)
    assert (L['rate_s'], L['rate_max_s'], L['banned_s'], L['auth_s'], L['auth_max_s'], L['network_s'],
            L['network_max_s'], L['response_s'], L['response_max_s']) == (
        3600, 6 * HOUR, DAY, 3600, 6 * HOUR, 300, HOUR, 900, 6 * HOUR)
    G = PROWLARR_PATH_LIMITS          # the Prowlarr path: the same numbers where both have one
    assert (G['interactive_per_hour'], G['background_burst'], G['background_refill_s'], G['per_day'],
            G['global_per_hour'], G['min_interval_s']) == (40, 5, 180.0, 300, 120, 2.0)
    assert (G['cooldown_429_s'], G['cooldown_400_strict_s'], G['cooldown_max_s']) == (3600, 3600, 6 * HOUR)
    assert G['cooldown_400_s'] == 900 == L['http400_s'] and L['http400_max_s'] == G['cooldown_max_s']   # v170
    assert (tv_budget.AUTO_PER_TRACKER_DAILY, tv_budget.AUTO_PER_TRACKER_HOURLY, tv_budget.AUTO_ALL_TRACKERS_DAILY,
            tv_budget.AUTO_MIN_GAP_S, tv_budget.SLOT_S) == (8, 2, 40, 600, 3 * HOUR)
    assert tv_budget.GUARD_LIMITS == {'per_tracker_day': 8, 'per_tracker_hour': 2, 'all_trackers_day': 40,
                                      'min_gap_s': 600}


CAPPED = ['avistaz', 'beyondhd', 'cinemaz', 'exoticaz', 'iptorrents', 'morethantv', 'pixelhd', 'privatehd']


def test_every_site_type_is_capped_and_generic_ones_are_not():
    assert sorted(t for t in tt.TRACKER_TYPES if tv_budget.capped({'type': t})) == CAPPED
    assert not tv_budget.capped({'type': 'torznab'}) and not tv_budget.capped({'type': 'newznab'})


@pytest.mark.parametrize('t', CAPPED)
def test_a_capped_tracker_gets_8_slots_a_day_3h_apart(tmp_path, t):
    """The feed polls a capped tracker whenever it has a free slot (tv_search.feed_due), checked
    every minute here: 8 polls in any 24 h, exactly 3 h apart."""
    g = TrackerGuard(tmp_path / 'g.json')
    cfg = site(t)
    sent = []
    for m in range(0, 3 * DAY, 60):
        now = T0 + m
        if tv_budget.slot_free(cfg, now, g):
            assert tv_budget.take(cfg, now, g) is None
            sent.append(now)
    assert len(sent) == 24
    gaps = {b - a for a, b in zip(sent, sent[1:])}
    assert gaps == {3 * HOUR}
    for s in sent:
        assert sum(1 for x in sent if s <= x < s + DAY) <= 8
    assert tv_budget.usage(cfg, sent[-1], g) == {'used': 8, 'cap': 8}


@pytest.mark.parametrize('t', ['iptorrents', 'pixelhd', 'privatehd'])
def test_the_daily_cap_holds_even_without_slots(tmp_path, t):
    """Straight to the guard (no slot check), every minute: 2 in an hour, 600 s apart, 8 a day,
    then the 9th is refused until the first falls out of the 24 h window."""
    g = TrackerGuard(tmp_path / 'g.json')
    cfg = site(t)
    sent, why = [], set()
    for m in range(0, DAY, 60):
        now = T0 + m
        r = tv_budget.take(cfg, now, g)
        if r is None:
            sent.append(now)
        else:
            why.add(r)
    assert len(sent) == 8
    assert min(b - a for a, b in zip(sent, sent[1:])) >= 600
    assert sent[1] - sent[0] == 600                         # 2 in the first hour, 600 s apart
    assert all(sum(1 for x in sent if s <= x < s + HOUR) <= 2 for s in sent)
    assert 'tv daily cap 8' in why and 'tv hourly cap 2' in why and 'tv spacing' in why
    assert tv_budget.take(cfg, sent[0] + DAY - 1, g) == 'tv daily cap 8'
    assert tv_budget.take(cfg, sent[0] + DAY + 1, g) is None


def test_40_a_day_across_trackers(tmp_path):
    g = TrackerGuard(tmp_path / 'g.json')
    hosts = [{'type': 'torznab', 'url': f'https://t{i}.example/api'} for i in range(6)]
    sites = [site(t) for t in CAPPED]
    n = 0
    for i, cfg in enumerate(sites):              # 8 site trackers x 5 = 40, through the guard's purpose caps
        for k in range(5):
            assert g.take(guard_key(cfg), False, tt.is_rate_strict(cfg['type']), T0 + i * 7 + k * HOUR,
                          purpose='tv') is None
            n += 1
    assert n == 40
    assert g.take(guard_key(hosts[0]), False, False, T0 + 6 * HOUR, purpose='tv') == 'tv daily cap across trackers 40'


def test_rate_limits_on_a_fake_clock(tmp_path):
    g = TrackerGuard(tmp_path / 'g.json')
    # interactive, ordinary site: 40 per hour, no bucket
    ok = [g.take('site:beyondhd', True, False, T0 + i) for i in range(41)]
    assert ok[:40] == [None] * 40 and ok[40] == 'hourly cap 40'
    # strict site (AvistaZ family): every request through the bucket: burst 5, then one per 180 s
    ok = [g.take('site:pixelhd', True, True, T0) for _ in range(6)]
    assert ok[:5] == [None] * 5 and ok[5] == 'rate limit (bucket empty)'
    assert g.take('site:pixelhd', True, True, T0 + 179) == 'rate limit (bucket empty)'
    assert g.take('site:pixelhd', True, True, T0 + 180) is None
    got = [s for s in range(1, 2 * HOUR + 1) if g.take('site:pixelhd', True, True, T0 + 180 + s) is None]
    assert len(got) in (39, 40)                            # 20 an hour after the burst (polled each second,
    assert {b - a for a, b in zip(got, got[1:])} <= {180, 181}   # float refill lands on 180 or 181 s)
    # background, ordinary site: the same bucket
    ok = [g.take('host:bg.example', False, False, T0) for _ in range(6)]
    assert ok[:5] == [None] * 5 and ok[5] == 'rate limit (bucket empty)'


def test_300_a_day_per_tracker_and_120_an_hour_across(tmp_path):
    g = TrackerGuard(tmp_path / 'g.json')
    for i in range(120):
        assert g.take(f'host:h{i % 4}.example', True, False, T0 + i) is None
    assert g.take('host:h9.example', True, False, T0 + 200) == 'global hourly cap 120'
    g2 = TrackerGuard(tmp_path / 'g2.json')
    sent = sum(g2.take('site:beyondhd', True, False, T0 + h * HOUR + i) is None for h in range(24) for i in range(40))
    assert sent == 300
    assert g2.take('site:beyondhd', True, False, T0 + 23 * HOUR + 3000) == 'daily cap 300'


def test_2s_between_requests_to_one_tracker(tmp_path, monkeypatch):
    g = TrackerGuard(tmp_path / 'g.json')
    clock = {'t': T0}
    slept = []
    monkeypatch.setattr(tracker_guard.time, 'time', lambda: clock['t'])

    def sleep(s):
        slept.append(round(s, 3))
        clock['t'] += s
    monkeypatch.setattr(tracker_guard.time, 'sleep', sleep)
    g.wait_spacing('site:pixelhd')
    clock['t'] += 0.5
    g.wait_spacing('site:pixelhd')
    g.wait_spacing('site:iptorrents')                       # another tracker: no wait
    assert slept == [1.5]
