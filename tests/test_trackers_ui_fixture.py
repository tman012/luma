"""The Trackers UI tests run against frontend/tests/fixtures/trackerTypes.json.
Keep it equal to what GET /api/trackers/types serves, so a schema change on
the server can't leave the UI tests green against a stale shape."""

import json
from pathlib import Path

from app.tracker_types import public_types

FIXTURE = Path(__file__).resolve().parent.parent / 'frontend' / 'tests' / 'fixtures' / 'trackerTypes.json'


def test_ui_fixture_matches_public_types():
    assert json.loads(FIXTURE.read_text(encoding='utf-8')) == public_types()


def test_ui_fixture_has_no_proxy_types():
    names = ' '.join(t['id'] + ' ' + t['name'] for t in public_types()).lower()
    assert 'jackett' not in names and 'prowlarr' not in names
