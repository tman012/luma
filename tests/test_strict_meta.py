"""Strict-tracker metadata: unknown or unavailable means strict; TTL refresh."""
from unittest.mock import Mock

from app import indexer_api


def _client():
    c = indexer_api.ProwlarrClient.__new__(indexer_api.ProwlarrClient)
    c.base_url = 'http://prowlarr.lan:9696'
    c.session = Mock()
    return c


def _ok(rows):
    r = Mock(); r.raise_for_status = Mock(); r.json.return_value = rows
    return r


def test_prowlarr_failure_means_strict():
    c = _client()
    c.session.get.side_effect = OSError('down')
    assert c._is_strict_tracker(5) is True
    assert c._ix_meta_ts == 0.0


def test_unknown_indexer_is_strict_known_lenient_is_not():
    c = _client()
    c.session.get.return_value = _ok([{'id': 1, 'name': 'Open', 'definitionName': 'yts'}])
    assert c._is_strict_tracker(1) is False
    assert c._is_strict_tracker(2) is True


def test_metadata_refreshed_after_ttl(monkeypatch):
    c = _client()
    c.session.get.return_value = _ok([{'id': 1, 'name': 'X', 'definitionName': 'yts'}])
    assert c._is_strict_tracker(1) is False
    calls = c.session.get.call_count
    assert c._is_strict_tracker(1) is False and c.session.get.call_count == calls   # cached
    c._ix_meta_ts -= indexer_api._IX_META_TTL + 1
    c.session.get.return_value = _ok([{'id': 1, 'name': 'X', 'definitionName': 'privatehd'}])
    assert c._is_strict_tracker(1) is True and c.session.get.call_count == calls + 1


def test_unscoped_never_strict_by_meta():
    assert _client()._is_strict_tracker(indexer_api.UNSCOPED) is False
