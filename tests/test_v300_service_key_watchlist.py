"""v300: the service key may read, add and remove native movie watchlist entries; a request without it still needs the signed-in owner. Unit level: no Flask app, no network."""
import types

import pytest
from flask import Flask, g

from app import start_flow


@pytest.fixture
def app():
    return Flask(__name__)


def _req(app, user, key=True, implicit=False):
    ctx = app.test_request_context('/', headers={'X-API-Key': 'k'} if key else {})
    ctx.push()
    g.current_user = user
    g.implicit_user = implicit
    return ctx


SERVICE = {"id": 0, "name": "Service", "is_admin": 1}


def test_service_key_detected(app):
    ctx = _req(app, SERVICE)
    try:
        assert start_flow.service_key_request() is True
    finally:
        ctx.pop()


@pytest.mark.parametrize("user,key,implicit", [
    (SERVICE, False, False),                     # id 0 without the header (never set by Luma, but refuse)
    ({"id": 1, "is_admin": 1}, True, False),     # a real admin session with a stray header
    ({"id": 0, "is_admin": 0}, True, False),
    (None, True, False),
    (SERVICE, True, True),                       # implicit LAN owner is never the service key
])
def test_not_service_key(app, user, key, implicit):
    ctx = _req(app, user, key, implicit)
    try:
        assert start_flow.service_key_request() is False
    finally:
        ctx.pop()


def test_watchlist_routes_use_service_guard():
    import inspect
    from app import movie_watchlist as mw
    for fn in (mw.ids_get, mw.add_post, mw.remove_delete):
        assert '_guard_or_service' in inspect.getsource(fn)
    # the searches and grabs on the watchlist stay owner-only
    for fn in (mw.search_post, mw.interactive_post, mw.interactive_grab_post, mw.list_get, mw.profiles_get):
        assert '_guard_or_service' not in inspect.getsource(fn)
