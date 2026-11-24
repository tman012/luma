"""Room playback through Plezy (plezy://play links over ADB)."""
from app import main


class FakeAdb:
    def __init__(self, installed=True, focus=''):
        self.installed = installed
        self.focus = focus
        self.calls = []

    def __call__(self, argv, timeout=None):
        self.calls.append(argv)
        line = ' '.join(argv)
        if argv[-2:] == ['echo', 'ok']:
            return {'ok': True, 'stdout': 'ok\n'}
        if 'pm path' in line:
            return {'ok': True, 'stdout': 'package:/data/app/x/base.apk\n' if self.installed else ''}
        if 'dumpsys window' in line:
            return {'ok': True, 'stdout': self.focus}
        return {'ok': True, 'stdout': 'Starting: Intent { }\n'}

    def starts(self):
        return [a[-1] for a in self.calls if a[-1].startswith('am start')]


def _run(monkeypatch, adb, sessions, **kw):
    monkeypatch.setattr('time.sleep', lambda s: None)
    seq = iter(sessions)
    monkeypatch.setattr(main, '_plex_session_on', lambda ip: next(seq, sessions[-1] if sessions else None))
    with main.app.test_request_context():
        return main._plezy_play_deeplink(adb, '10.0.0.5:5555', '10.0.0.5', 'abc123', kw.pop('rk', '501'),
                                          'Family Room', **kw)


def test_link_format():
    assert main._plezy_link('abc123', 501) == 'plezy://play?server=abc123&id=501'
    assert main._plezy_link('abc123', 77, shuffle=True) == 'plezy://play?server=abc123&id=77&mode=shuffle'


def test_movie_plays_in_plezy_and_confirms_session(monkeypatch):
    adb = FakeAdb()
    r = _run(monkeypatch, adb, [None, None, {'ratingKey': '501', 'state': 'playing', 'title': 'Dune'}])
    j = r.get_json()
    assert j['success'] and j['method'] == 'plezy' and j['verified'] == 'session'
    assert j['message'] == 'Playing in Family Room'
    starts = adb.starts()
    assert len(starts) == 1
    assert "'plezy://play?server=abc123&id=501'" in starts[0] and starts[0].endswith('com.edde746.plezy')
    assert any('force-stop' in ' '.join(a) and 'com.plexapp.android' in a for a in adb.calls)


def test_shuffle_sends_show_with_mode_shuffle(monkeypatch):
    adb = FakeAdb()
    r = _run(monkeypatch, adb, [None, {'ratingKey': '9001', 'state': 'playing'}], rk='9001',
             play_queue_id=55, source_rating_key='77')
    j = r.get_json()
    assert j['success'] and j['message'] == 'Shuffling in Family Room'
    assert "'plezy://play?server=abc123&id=77&mode=shuffle'" in adb.starts()[0]


def test_not_installed_falls_back_to_plex(monkeypatch):
    adb = FakeAdb(installed=False)
    assert _run(monkeypatch, adb, [None]) is None
    assert adb.starts() == []


def test_foreground_counts_when_no_session(monkeypatch):
    adb = FakeAdb(focus='  mCurrentFocus=Window{1 u0 com.edde746.plezy/com.edde746.plezy.PlezyActivity}')
    j = _run(monkeypatch, adb, [None]).get_json()
    assert j['success'] and j['verified'] == 'foreground'


def test_nothing_started_is_an_error(monkeypatch):
    adb = FakeAdb(focus='  mCurrentFocus=Window{1 u0 com.google.android.tvlauncher/.MainActivity}')
    resp, code = _run(monkeypatch, adb, [None])
    assert code == 409 and resp.get_json()['message'] == 'Plezy playback failed in Family Room'


def test_player_app_setting(monkeypatch):
    monkeypatch.setattr(main.settings, 'get', lambda k, d=None: 'plex' if k == 'plex.player_app' else d)
    assert main._plezy_wanted() is False
    monkeypatch.setattr(main.settings, 'get', lambda k, d=None: None)
    assert main._plezy_wanted() is True
