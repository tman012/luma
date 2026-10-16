"""Vibe Engine / Luma AI backend: works without OpenAI, and the REST client."""

import pytest

import app.main
import app.user_auth as user_auth
from app import openai_client

LAN = {'REMOTE_ADDR': '192.168.1.20'}


@pytest.fixture
def client(test_app):
    user_auth.create_user('owner', '1234', is_admin=True)
    app.main.settings.update({'apis': {'openai': {'enabled': False, 'api_key': '', 'voice_enabled': False}}})
    return test_app.test_client()


def test_describe_it_without_key_is_a_clean_503(client):
    r = client.post('/api/search/natural', json={'description': 'cozy heist movie'}, environ_base=LAN)
    assert r.status_code == 503
    assert 'not configured' in r.get_json()['error']


def test_voice_without_key_is_refused_cleanly(client):
    r = client.post('/api/voice/command', json={'transcript': 'pause'}, environ_base=LAN)
    assert r.status_code in (400, 403, 503)
    assert r.is_json


def test_alfred_not_configured(client):
    app.main.settings.update({'apis': {'alfred': {'enabled': False, 'base_url': ''}}})
    assert client.get('/api/alfred/health', environ_base=LAN).get_json() == {
        'available': False, 'reason': 'not_configured'}
    assert client.post('/api/alfred/command', json={'transcript': 'hi'}, environ_base=LAN).status_code == 503


def test_kiosk_settings_report_voice_flags(client):
    data = client.get('/api/kiosk/settings', environ_base=LAN).get_json()
    assert data['voice_enabled'] is False
    for key in ('vibe_newer_than_years', 'vibe_genres', 'vibe_excluded_libraries'):
        assert key in data


def test_vibe_engine_needs_no_openai(client, monkeypatch):
    app.main._vibe_cache.clear()
    monkeypatch.setattr(app.main, '_vibe_build', lambda plex, key, now: ({'items': [{'title': 'A'}], 'total': 1}, None))
    r = client.get('/api/library/vibe-engine', environ_base=LAN)
    assert r.status_code == 200 and r.get_json()['total'] == 1


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def test_client_chat_completion(monkeypatch):
    sent = {}

    def post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, json=json, headers=headers, timeout=timeout)
        return _Resp(200, {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'hi'}}]})
    monkeypatch.setattr(openai_client.requests, 'post', post)
    monkeypatch.delenv('OPENAI_BASE_URL', raising=False)
    c = openai_client.OpenAI(api_key='sk-x', max_retries=0)
    out = c.chat.completions.create(model='m', messages=[{'role': 'user', 'content': 'x'}], timeout=5)
    assert out.choices[0].message.content == 'hi'
    assert out.choices[0].finish_reason == 'stop'
    assert sent['url'] == 'https://api.openai.com/v1/chat/completions'
    assert sent['headers']['Authorization'] == 'Bearer sk-x'
    assert sent['timeout'] == 5 and 'timeout' not in sent['json']
    assert isinstance(out.choices[0].message, dict)   # can be sent back in the next request


def test_client_tool_calls_shape(monkeypatch):
    body = {'choices': [{'finish_reason': 'tool_calls', 'message': {
        'role': 'assistant', 'content': None,
        'tool_calls': [{'id': 't1', 'function': {'name': 'pause', 'arguments': '{}'}}]}}]}
    monkeypatch.setattr(openai_client.requests, 'post', lambda *a, **k: _Resp(200, body))
    msg = openai_client.OpenAI('k').chat.completions.create(model='m', messages=[]).choices[0].message
    assert msg.tool_calls[0].function.name == 'pause' and msg.tool_calls[0].id == 't1'


@pytest.mark.parametrize('status,cls', [(401, openai_client.AuthenticationError),
                                        (404, openai_client.NotFoundError),
                                        (429, openai_client.RateLimitError),
                                        (500, openai_client.APIError)])
def test_client_errors(monkeypatch, status, cls):
    monkeypatch.setattr(openai_client.requests, 'get',
                        lambda *a, **k: _Resp(status, {'error': {'message': 'nope'}}))
    with pytest.raises(cls) as e:
        openai_client.OpenAI('k').models.retrieve('m')
    assert 'nope' in str(e.value) and type(e.value).__name__ == cls.__name__


def test_no_openai_package_needed():
    import pathlib
    req = pathlib.Path(__file__).resolve().parent.parent.joinpath('requirements.txt').read_text().lower()
    assert 'openai' not in req
