"""settings-unmask: the /api backstop masks passkey=/token= inside ordinary settings strings
(a private RSS feed URL). Saving that masked value back must keep the stored secret."""
import json

import pytest

import app.main
from app import user_auth
from app.err_text import unmask, redact, UnmaskAmbiguous

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


def test_edit_path_keeps_masked_passkey(client):
    _put(client, FEED)
    _put(client, 'https://tracker.example/rss2?passkey=***&cat=2')
    assert app.main.settings.get('radarr.url') == 'https://tracker.example/rss2?passkey=abc&cat=2'


def test_edit_host_with_masked_passkey_is_refused_not_leaked(client):
    """Reviewer F2: the stored secret only follows the same scheme+host."""
    _put(client, FEED)
    r = client.put('/api/settings', json={'radarr': {'url': 'https://evil.example/rss?passkey=***'}}, environ_base=LAN)
    assert r.status_code == 400
    assert app.main.settings.get('radarr.url') == FEED and '***' not in json.dumps(_stored())


def test_explicit_new_passkey_replaces(client):
    _put(client, FEED)
    _put(client, 'https://tracker.example/rss?passkey=xyz&cat=1')
    assert app.main.settings.get('radarr.url') == 'https://tracker.example/rss?passkey=xyz&cat=1'


def test_mask_never_stored(client):
    _put(client, FEED)
    for v in ('https://tracker.example/rss?passkey=***', 'https://tracker.example/x?passkey=%2A%2A%2A'):
        _put(client, v)
        assert '***' not in json.dumps(_stored()) and '%2A' not in json.dumps(_stored())
    for v in ('https://h/rss?passkey=***&token=***', 'x?apikey=***'):
        client.put('/api/settings', json={'radarr': {'url': v}}, environ_base=LAN)
        assert '***' not in json.dumps(_stored())


def test_unmask_nested_and_ambiguous():
    old = {'a': [{'id': 1, 'u': FEED}], 'b': 'https://h/x?token=t1&apikey=k1', 'c': 'plain'}
    new = {'a': [{'id': 1, 'u': redact(FEED)}], 'b': 'https://h/x?token=***', 'c': 'plain2', 'd': 'z?passkey=***'}
    out = unmask(new, old)
    assert out['a'][0]['u'] == FEED
    assert out['b'] == 'https://h/x?token=t1'     # Tester F1: fewer params = an edit, apikey dropped
    assert out['c'] == 'plain2' and out['d'] == 'z?passkey='    # nothing stored (lenient): marker dropped
    assert '***' not in json.dumps(out)
    with pytest.raises(UnmaskAmbiguous):
        unmask({'d': 'z?passkey=***'}, {}, strict=True)


def test_fewer_params_applies_edit():
    old = 'https://t.example/rss?token=T1&passkey=P1'
    assert unmask('https://t.example/rss?token=***', old, strict=True) == 'https://t.example/rss?token=T1'


def test_encoded_mask_is_the_mask():
    assert unmask('https://tracker.example/rss?passkey=%2A%2A%2A&cat=1', FEED, strict=True) == FEED
    assert unmask('https://tracker.example/rss?passkey=%2a%2a%2a', FEED, strict=True) == 'https://tracker.example/rss?passkey=abc'


def test_bare_mask_typed_keeps_stored(client):
    app.main.settings.update({'radarr': {'api_key': 'realkey123'}})
    client.put('/api/settings', json={'radarr': {'api_key': '***'}}, environ_base=LAN)
    assert app.main.settings.get('radarr.api_key') == 'realkey123'
    assert unmask('***', None) == '' and unmask('%2A%2A%2A', 'k') == 'k'


def test_list_delete_a_while_b_masked_keeps_bs_own_secret():
    """Reviewer F1: items match by id/name/key or URL host, never by position."""
    a = {'id': 'a', 'feed': 'https://a.example/rss?passkey=AAA'}
    b = {'id': 'b', 'feed': 'https://b.example/rss?passkey=BBB'}
    out = unmask([{'id': 'b', 'feed': redact(b['feed'])}], [a, b], strict=True)
    assert out == [b]
    urls = ['https://a.example/rss?passkey=AAA', 'https://b.example/rss?passkey=BBB']
    assert unmask([redact(urls[1])], urls, strict=True) == [urls[1]]
    nameless = [{'feed': 'https://a.example/rss?passkey=AAA'}, {'feed': 'https://b.example/rss?passkey=BBB'}]
    with pytest.raises(UnmaskAmbiguous):                   # no key and lengths differ: refuse, never guess
        unmask([{'feed': redact(nameless[1]['feed'])}], nameless, strict=True)


def test_tracker_store_update_keeps_masked_secret(tmp_path):
    """Reviewer F3: the tracker_store path (a non-secret field holding a token= part)."""
    from app.tracker_store import TrackerStore
    st = TrackerStore(tmp_path / 't.json', tmp_path / 'k')
    name = 'IPT token=T1'
    rec, err = st.create({'type': 'iptorrents', 'name': name, 'url': 'https://ipt.example', 'cookie': 'c=1'})
    assert not err, err
    _, err = st.update(rec['id'], {'type': 'iptorrents', 'name': redact(name), 'url': 'https://ipt.example'})
    assert not err, err
    assert st.get(rec['id'])['name'] == name
    assert '***' not in (tmp_path / 't.json').read_text()
    rec3, err = st.update(rec['id'], {'type': 'iptorrents', 'name': 'IPT passkey=***', 'url': 'https://ipt.example'})
    assert rec3 is None and err and st.get(rec['id'])['name'] == name


def test_user_settings_unmask(test_app):
    uid = user_auth.create_user('unmask_u', 'pw1234')['id']
    user_auth.save_user_settings(uid, {'rss': FEED})
    user_auth.save_user_settings(uid, {'rss': redact(FEED)})
    assert user_auth.get_user_settings(uid)['rss'] == FEED


def test_user_settings_route_ambiguous_is_400(test_app):
    uid = user_auth.create_user('unmask_u2', 'pw1234')['id']
    user_auth.save_user_settings(uid, {'rss': FEED})
    with pytest.raises(UnmaskAmbiguous):
        user_auth.save_user_settings(uid, {'rss': 'https://other.example/rss?passkey=***'})
    assert user_auth.get_user_settings(uid)['rss'] == FEED
