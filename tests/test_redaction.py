import pytest
from app.indexer_api import _redacted_snippet as r


@pytest.mark.parametrize('text,secret', [
    ('url=http://x/?apikey=abc123', 'abc123'),
    ('q%3Fkey%3Dshort1', 'short1'),
    ('next=api_key%3Dz9z9', 'z9z9'),
    ('apikey%3Dq1w2', 'q1w2'),
    ('key=k1', 'k1'),
    ('passkey=ab', 'ab'),
    ('"token": "t0"', 't0'),
    ('torrent_pass=xyz&x=1', 'xyz'),
    ('Authorization: Bearer abc.def', 'abc.def'),
    ('hash ' + 'a' * 30, 'a' * 30),
])
def test_secrets_redacted(text, secret):
    out = r(text)
    assert secret not in out and '[redacted]' in out


def test_plain_text_kept():
    assert r('Bad request: unknown category 2000') == 'Bad request: unknown category 2000'
