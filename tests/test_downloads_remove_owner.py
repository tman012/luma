"""PM ruling, ported from cursor/tv-phase1 56b522d (the tail of its
tests/test_signed_in_owner_guard.py): removing a download WITH its files
needs the owner signed in with a session; without files, today's access."""
import pytest

from tests.test_home_rails import REMOTE, _client
from tests.test_storage_cleanup_owner import HDR, KEY, LAN, full  # noqa: F401  (fixture)


# ------------------------------------------------- /api/downloads/remove/<hash>

H = 'a' * 40


class _Qbit:
    def __init__(self):
        self.posts = []

    def __call__(self):
        q = self

        class R:
            def __init__(self, text='Ok.'):
                self.status_code, self.text = 200, text

        class S:
            def post(self, url, data=None, **kw):
                q.posts.append((url.rsplit('/api/v2/', 1)[-1], dict(data or {})))
                return R()
        return S()


@pytest.fixture
def qbit(full, monkeypatch):
    import app.main
    q = _Qbit()
    monkeypatch.setattr(app.main, '_qbit_new_session', q)
    real_get = app.main.settings.get
    monkeypatch.setattr(app.main.settings, 'get',
                        lambda k, d=None: 'http://qbit.test:8080' if k == 'qbittorrent.url' else real_get(k, d))
    return q


def _remove(c, delete, env=LAN, headers=HDR, how='query'):
    url = f'/api/downloads/remove/{H}'
    if how == 'query':
        return c.delete(f'{url}?deleteFiles={"true" if delete else "false"}', environ_base=env, headers=headers)
    return c.post(url, json={'delete_files': delete}, environ_base=env, headers=headers)


@pytest.mark.parametrize('how', ['query', 'body'])
def test_deleting_files_needs_the_signed_in_owner(full, qbit, how):
    owner = _client(full)
    r = _remove(full.test_client(), True, how=how)                     # implicit LAN owner
    assert r.status_code == 401 and r.get_json() == {'error': 'Sign in first', 'sign_in': True}
    assert qbit.posts == []                                            # qBit never contacted
    r = _remove(owner, True, how=how)
    assert r.status_code == 200 and r.get_json()['deleteFiles'] is True
    assert qbit.posts[-1] == ('torrents/delete', {'hashes': H, 'deleteFiles': 'true'})


def test_deleting_files_denies_everyone_else(full, qbit):
    _client(full)
    for c in (_client(full, name='admin2'), _client(full, admin=False, name='viewer'),
              _client(full, admin=False, name='guest')):
        assert _remove(c, True).status_code == 403
    for env in (LAN, REMOTE):
        r = _remove(full.test_client(), True, env, dict(HDR, **{'X-API-Key': KEY}))   # kiosk / companion apps
        assert r.status_code == 401 and r.get_json()['sign_in'] is True
    assert _remove(full.test_client(), True, REMOTE).status_code == 401
    assert qbit.posts == []


def test_removing_without_files_keeps_todays_access(full, qbit):
    _client(full)
    r = _remove(full.test_client(), False)                             # implicit LAN owner, as today
    assert r.status_code == 200 and r.get_json()['deleteFiles'] is False
    assert qbit.posts[-1] == ('torrents/delete', {'hashes': H, 'deleteFiles': 'false'})
    r = _remove(full.test_client(), False, how='body')
    assert r.status_code == 200
    r = _remove(full.test_client(), False, REMOTE, dict(HDR, **{'X-API-Key': KEY}))   # service key, as today
    assert r.status_code == 200
