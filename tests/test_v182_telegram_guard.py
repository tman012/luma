"""v182: the pending-requests Telegram send (urllib, outside the requests write guard) is blocked on the
test instance unless 'telegram' is opted in; prod sends as before."""
import pytest

from app import instance, pending_requests


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(pending_requests, '_get_telegram_bot_token', lambda: 'tok')

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    def urlopen(req, timeout=None):
        out.append(req.full_url.replace('tok', '<token>'))
        return Resp()
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(instance, 'prod_conflict', lambda: [])
    for k in ('LUMA_INSTANCE', 'LUMA_TEST_ALLOW'):
        monkeypatch.delenv(k, raising=False)
    return out


def test_test_instance_blocks_the_send(sent, monkeypatch):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    pending_requests.send_telegram('1', 'Severance is ready on Luma!')
    assert sent == []
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'tv_grab')                    # another opt-in doesn't open it
    pending_requests.send_telegram('1', 'x')
    assert sent == []


def test_test_instance_sends_when_opted_in(sent, monkeypatch):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'telegram')
    pending_requests.send_telegram('1', 'x')
    assert sent == ['https://api.telegram.org/bot<token>/sendMessage']


@pytest.mark.parametrize('value', ['', 'TEST', 'staging'])
def test_unknown_instance_fails_closed_and_ignores_the_opt_in(sent, monkeypatch, value):
    monkeypatch.setenv('LUMA_INSTANCE', value)
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'telegram')
    pending_requests.send_telegram('1', 'x')
    assert sent == []


def test_prod_sends(sent, monkeypatch):
    monkeypatch.setenv('LUMA_INSTANCE', 'prod')
    monkeypatch.setattr(instance, 'test_signs', lambda: [], raising=False)
    pending_requests.send_telegram('1', 'x')
    assert sent == ['https://api.telegram.org/bot<token>/sendMessage']
