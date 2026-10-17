"""The mover's qBittorrent sign-in never risks the shared IP's ban.

qBittorrent bans an IP after 5 failed sign-ins by default, and :5002 shares the
IP with production. Rejected sign-ins back off exponentially and stop after 3
in a row until the credentials change or a Test of the saved credentials
succeeds. The count survives a restart and a Save.
"""
import pytest
import requests

from app import library_mover as lm
from app import qbit_connector
from tests.test_library_mover import H1, TMDB, Resp, add_path, env  # noqa: F401

T0 = 1_000_000.0


class Client:
    """qBittorrent sign-in with a scripted answer; records every request."""

    def __init__(self, answer=lambda: Resp(200, 'Fails.')):
        self.answer = answer
        self.logins = []
        self.gets = []
        self.sessions = []

    def factory(self):
        c = self

        class S:
            trust_env = True

            def __init__(self):
                c.sessions.append(self)

            def post(self, url, data=None, timeout=None, **kw):
                c.logins.append(kw)
                a = c.answer()
                if isinstance(a, Exception):
                    raise a
                return a

            def get(self, url, params=None, timeout=None, **kw):
                c.gets.append(kw)
                return Resp(data=[])

            def close(self):
                pass
        return S()


@pytest.fixture
def waiting(env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'Heat.1995')
    return env


def run(c, times):
    for t in times:
        lm.tick(now=T0 + t, session_factory=c.factory)


def test_wrong_password_ticking_every_30s_signs_in_at_most_3_times(waiting):
    c = Client()
    run(c, range(0, 3600, 30))                 # an hour of 30 s ticks
    assert len(c.logins) == 3                  # well under qBittorrent's 5-failure ban
    assert lm.login_state()['held'] is True


def test_backoff_is_exponential(waiting):
    c = Client()
    seen = []
    for t in range(0, 600, 30):
        n = len(c.logins)
        lm.tick(now=T0 + t, session_factory=c.factory)
        if len(c.logins) > n:
            seen.append(t)
    assert seen == [0, 60, 180]                # waits of 60 s then 120 s, then held


@pytest.mark.parametrize('answer', [lambda: Resp(403, 'Forbidden'), lambda: Resp(401, ''),
                                    lambda: Resp(200, 'Fails.')])
def test_403_401_and_fails_all_count(waiting, answer):
    c = Client(answer)
    run(c, range(0, 3600, 30))
    assert len(c.logins) == 3


def test_new_credentials_start_fresh(waiting):
    c = Client()
    run(c, range(0, 600, 30))
    assert len(c.logins) == 3
    waiting.settings.v['qbittorrent.password'] = 'fixed'
    c.answer = lambda: Resp(200, 'Ok.')
    run(c, [630])
    assert len(c.logins) == 4 and lm.login_state()['held'] is False


def test_saving_the_connector_keeps_the_hold(waiting):
    """A Save with the same credentials is not a fresh start (it would allow 3
    more rejected sign-ins, past qBittorrent's 5-failure ban)."""
    c = Client()
    run(c, range(0, 600, 30))
    assert lm.login_state()['held'] is True
    qbit_connector._changed()                  # what Save calls
    run(c, [630, 3600, 7200])
    assert len(c.logins) == 3


def test_a_successful_test_of_the_saved_credentials_releases_the_hold(waiting):
    c = Client()
    run(c, range(0, 600, 30))
    assert lm.login_state()['held'] is True
    qbit_connector._verified()                 # what a successful Test of the saved credentials calls
    run(c, [630])
    assert len(c.logins) == 4


def test_connector_save_and_test_routes_notify(monkeypatch):
    import inspect
    assert '_changed()' in inspect.getsource(qbit_connector.qbit_save)
    assert '_verified()' not in inspect.getsource(qbit_connector.qbit_save)
    src = inspect.getsource(qbit_connector.qbit_test)
    assert src.index('_changed()') > src.index('if problem:')   # only after a successful sign-in
    assert src.index('_verified()') > src.index('if _is_saved(cfg):') > src.index('if problem:')


def test_a_success_resets_the_count(waiting):
    answers = iter([Resp(200, 'Fails.'), Resp(200, 'Fails.'), Resp(200, 'Ok.'),
                    Resp(200, 'Fails.'), Resp(200, 'Fails.'), Resp(200, 'Fails.')])
    c = Client(lambda: next(answers))
    run(c, range(0, 3600, 30))
    assert len(c.logins) == 6 and lm.login_state()['held'] is True


def test_unreachable_backs_off_without_counting_as_a_failed_sign_in(waiting):
    c = Client(lambda: requests.ConnectionError('down'))
    seen = []
    for t in range(0, 4 * 3600, 30):
        n = len(c.logins)
        lm.tick(now=T0 + t, session_factory=c.factory)
        if len(c.logins) > n:
            seen.append(t)
    gaps = [b - a for a, b in zip(seen, seen[1:])]
    assert gaps[:5] == [30, 60, 120, 240, 480]
    assert max(gaps) <= lm.UNREACHABLE_MAX_WAIT_S + 30
    assert lm.login_state()['held'] is False and lm.login_state()['failures'] == 0
    c.answer = lambda: Resp(200, 'Ok.')
    run(c, [4 * 3600 + 900])
    assert lm.login_state()['unreachable'] == 0


def test_no_proxy_from_env_and_no_redirects(waiting):
    c = Client(lambda: Resp(200, 'Ok.'))
    run(c, [0])
    assert c.sessions and all(s.trust_env is False for s in c.sessions)
    assert c.logins == [{'allow_redirects': False}]
    assert c.gets and all(g.get('allow_redirects') is False for g in c.gets)


def test_connector_check_uses_the_same_session_setup(monkeypatch):
    made = []

    class S:
        trust_env = True

        def __init__(self):
            made.append(self)

        def post(self, *a, **kw):
            assert kw.get('allow_redirects') is False
            return Resp(200, 'Fails.')

        def close(self):
            pass
    monkeypatch.setattr(qbit_connector.requests, 'Session', S)
    problem, _ = qbit_connector.check('http://qbit.invalid:8080', 'u', 'p')
    assert problem == qbit_connector.BAD_LOGIN and made[0].trust_env is False


def test_no_paths_or_nothing_waiting_never_signs_in(env):
    c = Client(lambda: Resp(200, 'Ok.'))
    run(c, range(0, 300, 30))
    assert c.logins == []
