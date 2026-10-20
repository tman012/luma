"""v185 (PM): a monitor-ON click's first MS_BURST (5) searches go on the interactive bucket; the rest are
background requests paced one per BG_GAP_S, so one show or season click can't drain the 40/h.
Also (Reviewer d): the Series grid's Missing opt-in is the signed-in owner's."""
import pytest

from app import tv_search
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import _client
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.test_tv_search_v158b import allow_grabs, env, item  # noqa: F401
from tests.test_v182_series_detail import _req
from tests.tv_fake import fake_tmdb  # noqa: F401


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def sleep(self, s):
        self.t += s


@pytest.fixture
def lanes(monkeypatch):
    c = Clock()
    ran = []
    monkeypatch.setattr(tv_search, '_clock', lambda: c.t)
    monkeypatch.setattr(tv_search, '_sleep', c.sleep)
    monkeypatch.setattr(tv_search, 'trackers', lambda: [{'id': 1}])
    tv_search._ms_bucket[:] = [float(tv_search.MS_BURST), c.t]
    picks = {'pack': []}

    def one(sid, season, kind, ids, now=None, interactive=True):
        ran.append((kind, tuple(ids), interactive, c.t))
        return {'ok': True, 'searched': 1, 'results': 0, 'picks': picks[kind] if kind == 'pack' else [], 'statuses': [], 'error': None}
    monkeypatch.setattr(tv_search, '_one', one)
    return c, ran


def _plan(n, season=2, pack=False, start=100):
    return [{'season': season, 'ids': list(range(start, start + n)), 'pack': pack}]


def test_a_click_spends_its_burst_interactively_then_goes_background_paced(lanes):
    c, ran = lanes
    t0 = c.t
    tv_search.run_plan(7, _plan(8))
    tv_search.drain()
    assert [r[2] for r in ran] == [True] * 5 + [False] * 3
    assert [r[3] - t0 for r in ran[:5]] == [0] * 5                       # the burst goes at once
    bg = [r[3] for r in ran[5:]]
    assert bg[1] - bg[0] == tv_search.BG_GAP_S and bg[2] - bg[1] == tv_search.BG_GAP_S
    assert tv_search._ms_clicks == {}                                    # the click's allowance is gone with it


def test_one_click_never_takes_more_than_the_burst_from_the_interactive_bucket(lanes):
    c, ran = lanes
    tv_search.run_plan(7, _plan(30))
    tv_search.drain()
    assert sum(1 for r in ran if r[2]) == tv_search.MS_BURST and len(ran) == 30
    assert ran[-1][3] - ran[5][3] == 24 * tv_search.BG_GAP_S             # 25 background searches, 180 s apart
    # 30 searches in one click took ~72 min, never 30 interactive tokens (the 40/h stays for the owner)


def test_a_pack_s_fallback_belongs_to_the_same_click(lanes):
    c, ran = lanes
    tv_search.run_plan(7, _plan(6, pack=True))
    tv_search.drain()
    assert ran[0][0] == 'pack' and ran[0][2] is True
    assert [r[2] for r in ran[1:]] == [True] * 4 + [False] * 2         # 1 pack + 4 episodes = the burst


def test_the_search_button_runs_only_its_interactive_part_now(lanes):
    c, ran = lanes
    out = tv_search.run_plan(7, _plan(8), sync=True)
    assert [r[2] for r in ran] == [True] * 5 and out['queued'] == 3
    tv_search.drain()
    assert [r[2] for r in ran] == [True] * 5 + [False] * 3


def test_background_lane_searches_are_background_requests_never_tv_slots(owner, env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tv_search, 'MS_REFILL_S', 0.0)
    monkeypatch.setattr(tv_search, 'BG_GAP_S', 0.0)
    monkeypatch.setattr(tv_search, 'MS_BURST', 2)
    allow_grabs(monkeypatch)
    with __import__('app.tv_store', fromlist=['x']).write() as c:
        c.execute('UPDATE episodes SET monitored = 0 WHERE series_id = ? AND season_number = 2', (env.sid,))
    r = _req(owner, 'put', f'/api/settings/tv/shows/{env.sid}/seasons/2/monitored', {'monitored': True})
    n = r.get_json()['search']['started']
    assert n >= 3
    tv_search.drain()
    lanes_seen = [(p, i) for _, p, i in env.sent if p != 'season']
    assert lanes_seen[:2] == [('manual', True)] * 2
    assert set(lanes_seen[2:]) == {(tv_search.MANUAL_BACKGROUND, False)}


def test_query_trackers_background_lane_skips_tv_slots_and_purpose(monkeypatch):
    from app import tracker_search, tv_budget
    seen = []

    class Eng:
        class tokens:
            @staticmethod
            def flush():
                pass

        def search_one(self, cfg, q, cats, limit, interactive, purpose=None):
            seen.append((interactive, purpose))
            return []

        def to_item(self, cfg, r, sv):
            return None
    monkeypatch.setattr(tracker_search, 'get_search', lambda: Eng())
    monkeypatch.setattr(tracker_search, '_match_categories', lambda cfg, cats: [5000])
    monkeypatch.setattr(tracker_search, '_secret_values', lambda cfg: [])
    monkeypatch.setattr(tv_budget, 'capped', lambda cfg: True)
    monkeypatch.setattr(tv_budget, 'under_daily', lambda cfg, now=None: False)   # v196c: daily cap reached
    monkeypatch.setattr(tv_search, '_log', lambda *a, **k: None)
    cfg = {'id': 1, 'name': 'T'}
    _, st = tv_search.query_trackers('X S01E01', 'tv', None, False, 0.0, [cfg])
    assert seen == [] and st[0]['status'] == tv_search.SLOT_BUSY                  # automatic: waits for its slot
    _, st = tv_search.query_trackers('X S01E01', tv_search.MANUAL_BACKGROUND, None, False, 0.0, [cfg])
    assert seen == [(False, None)]                                               # background bucket, no 'tv' purpose


def test_monitor_missing_is_the_signed_in_owners(minimal_app, scanned, owner):  # noqa: F811
    ids = [r['id'] for r in rows('SELECT id FROM series')]
    anon = _client(minimal_app)
    r = anon.post('/api/settings/tv/shows/monitor-missing', json={'ids': ids}, headers=HDR)
    assert r.status_code in (401, 403)
    assert {r['monitor'] for r in rows('SELECT monitor FROM series')} != {'missing'}
    assert _req(owner, 'post', '/api/settings/tv/shows/monitor-missing', {'ids': ids}).get_json() == {'ok': True, 'updated': len(ids)}
