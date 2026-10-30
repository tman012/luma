"""Tester (0a292585): a manual search on a test instance with grabs off answers grabbed: false WITH a reason
('disabled'); existing / send_failed keep theirs."""
from app import tv_grab, tv_search
from tests.test_tv_search_v158b import allow_grabs, env, eps, item, sev  # noqa: F401
from tests.test_tv_v158c import FakeQbit, T, _grab, qenv  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401


def test_manual_search_with_grabs_off_has_reason_disabled(env, monkeypatch):  # noqa: F811
    monkeypatch.delenv('LUMA_TEST_ALLOW', raising=False)
    monkeypatch.setattr(tv_grab, 'allowed', lambda: False)
    env.results = [item('Severance.S02E03.1080p.WEB-DL-B')]
    out = tv_search.manual(env.sid, episode_id=env.e2[3], now=5000.0)
    p = out['picks'][0]
    assert p['grabbed'] is False and p['reason'] == 'disabled' and p['error'] == tv_grab.OFF


def test_manual_search_existing_and_send_failed_keep_their_reason(env, monkeypatch):  # noqa: F811
    allow_grabs(monkeypatch)
    env.results = [item('Severance.S02E03.1080p.WEB-DL-B')]
    for g, want in [({'ok': True, 'hash': 'a' * 40, 'existing': True, 'grabbed': False, 'reason': 'existing'}, 'existing'),
                    ({'ok': False, 'error': 'qBittorrent returned HTTP 500'}, 'send_failed')]:
        monkeypatch.setattr(tv_grab, 'grab', lambda *a, **k: g)
        p = tv_search.manual(env.sid, episode_id=env.e2[3], now=5000.0)['picks'][0]
        assert p['grabbed'] is False and p['reason'] == want and p['error']


def test_interactive_grab_with_grabs_off_has_reason_disabled(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tv_grab, 'allowed', lambda: False)
    out = tv_search.grab_release(env.sid, {'title': 'Severance.S02E03.1080p.WEB-DL-B', 'downloadUrl': 'x'}, now=5000.0)
    assert out['grabbed'] is False and out['reason'] == 'disabled'
