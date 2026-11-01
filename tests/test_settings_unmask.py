"""settings-unmask: the /api backstop masks passkey=/token= inside ordinary settings strings
(a private RSS feed URL). Saving that masked value back must keep the stored secret."""
import json

import pytest

import app.main
from app import user_auth
from app.err_text import unmask, redact

LAN = {'REMOTE_ADDR': '192.168.1.50'}
FEED = 'https://tracker.example/rss?passkey=abc&cat=1'


ADMIN = {'id': 1, 'name': 'admin', 'is_admin': True, 'status': 'active'}


@pytest.fixture
def client(test_app, monkeypatch):
    monkeypatch.setattr(app.main, '_get_current_user', lambda: ADMIN)
    return test_app.test_client()


def _stored():
    return json.loads(open(app.main.settings._path).read())


def _put(client, url):
    r = client.put('/api/settings', json={'radarr': {'url': url}}, environ_base=LAN)
    assert r.status_code == 200, r.get_data(as_text=True)


def test_round_trip_masked_get_saved_unchanged_keeps_secret(client):
    _put(client, FEED)
    got = client.get('/api/settings', environ_base=LAN).get_json()
    shown = got['radarr']['url']
    assert 'abc' not in shown and 'passkey=***' in shown      # what Settings shows
    _put(client, shown)                                       # saved unchanged
    assert app.main.settings.get('radarr.url') == FEED
    assert _stored()['radarr']['url'] == FEED


def test_edit_host_keeps_masked_passkey(client):
    _put(client, FEED)
    _put(client, 'https://new-host.example/rss?passkey=***&cat=1')
    assert app.main.settings.get('radarr.url') == 'https://new-host.example/rss?passkey=abc&cat=1'


def test_explicit_new_passkey_replaces(client):
    _put(client, FEED)
    _put(client, 'https://tracker.example/rss?passkey=xyz&cat=1')
    assert app.main.settings.get('radarr.url') == 'https://tracker.example/rss?passkey=xyz&cat=1'


def test_mask_never_stored(client):
    _put(client, FEED)
    for v in ('https://h/rss?passkey=***', 'https://h/rss?passkey=***&token=***', 'x?apikey=***'):
        _put(client, v)
        assert '***' not in json.dumps(_stored())


def test_unmask_nested_and_ambiguous():
    old = {'a': [{'u': FEED}], 'b': 'x?token=t1&apikey=k1', 'c': 'plain'}
    new = {'a': [{'u': redact(FEED)}], 'b': 'y?token=***', 'c': 'plain2', 'd': 'z?passkey=***'}
    out = unmask(new, old)
    assert out['a'][0]['u'] == FEED
    assert out['b'] == old['b']                  # 1 mask vs 2 stored secrets: ambiguous, keep stored
    assert out['c'] == 'plain2' and out['d'] == 'z?passkey='    # nothing stored: marker dropped
    assert '***' not in json.dumps(out)


def test_user_settings_unmask(test_app):
    uid = user_auth.create_user('unmask_u', 'pw1234')['id']
    user_auth.save_user_settings(uid, {'rss': FEED})
    user_auth.save_user_settings(uid, {'rss': redact(FEED)})
    assert user_auth.get_user_settings(uid)['rss'] == FEED
