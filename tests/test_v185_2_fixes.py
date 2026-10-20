"""v185.2 (Tester BLOCK + PM): the FF-21 reconcile is by the row's own hash only (tests in
test_v184_grab_import) and the owner's monitor-ON / manual searches count toward the TV tally (not its caps)."""
from app import tracker_search, tv_budget, tv_search


class _Guard:
    def __init__(self):
        self.noted = []

    def note_purpose(self, tid, purpose, now=None):
        self.noted.append((tid, purpose))


class _Engine:
    def __init__(self):
        self.guard = _Guard()
        self.tokens = type('T', (), {'flush': lambda self: None})()

    def search_one(self, cfg, q, cats, limit, interactive, purpose=None):
        return []

    def to_item(self, cfg, r, sv):
        return None


def _run(monkeypatch, purpose, interactive, capped=True):
    e = _Engine()
    monkeypatch.setattr(tracker_search, 'get_search', lambda: e)
    monkeypatch.setattr(tracker_search, '_match_categories', lambda cfg, cats: cats)
    monkeypatch.setattr(tracker_search, '_secret_values', lambda cfg: [])
    monkeypatch.setattr(tv_budget, 'capped', lambda cfg: capped)
    monkeypatch.setattr(tv_budget, 'slot_free', lambda cfg, now: True)
    monkeypatch.setattr(tv_search, '_log', lambda *a, **k: None)
    tv_search.query_trackers('Severance S02', purpose, 1, interactive, 0.0, [{'id': 1, 'name': 'X', 'type': 'privatehd'}])
    return e.guard.noted


def test_manual_and_monitor_on_searches_count_toward_the_tv_tally(monkeypatch):
    for purpose, inter in (('manual', True), (tv_search.MANUAL_BACKGROUND, False)):
        noted = _run(monkeypatch, purpose, inter)
        assert len(noted) == 1 and noted[0][1] == tv_budget.PURPOSE


def test_automatic_and_uncapped_searches_are_not_noted_twice(monkeypatch):
    assert _run(monkeypatch, 'episode', False) == []            # automatic: take() already counted it
    assert _run(monkeypatch, 'manual', True, capped=False) == []
