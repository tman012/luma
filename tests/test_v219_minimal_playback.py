"""v219 (PM): prod 1b4ea74's playback is on in minimal builds.

Only the routes Play/Watch and the TV Shuffle call are opened, each on its one
method. Nothing that writes to the Plex library (scan, refresh, delete, match
edits, mark watched/unwatched, metadata edits) becomes reachable through them.
"""
import inspect
import re

import pytest

from app import minimal

OPENED = [
    ('GET', '/api/settings/rooms'),
    ('GET', '/api/plex/resume_info/123'),
    ('POST', '/api/plex/playqueue'),
    ('POST', '/api/player/play'),
]


@pytest.mark.parametrize('method,path', OPENED)
def test_playback_routes_open(method, path):
    assert minimal.path_allowed(path, method)


@pytest.mark.parametrize('method,path', [
    ('POST', '/api/settings/rooms'),            # room create
    ('PUT', '/api/settings/rooms'),
    ('DELETE', '/api/settings/rooms'),
    ('PUT', '/api/settings/rooms/r1'),
    ('DELETE', '/api/settings/rooms/r1'),
    ('GET', '/api/settings/rooms/r1/devices'),
    ('POST', '/api/plex/resume_info/123'),
    ('GET', '/api/plex/resume_info/../x'),
    ('GET', '/api/plex/resume_info/abc'),
    ('GET', '/api/plex/playqueue'),
    ('GET', '/api/player/play'),
    ('POST', '/api/player/stop'),
    ('POST', '/api/player/pause'),
])
def test_neighbours_stay_closed(method, path):
    assert not minimal.path_allowed(path, method)


# Write routes that were already open before v219 (owner/admin-only, not
# playback): v216's Fix Match and Luma's own TV import. v219 adds none.
_LIB_WRITE = re.compile(r'scan|refresh|analy[sz]e|empty.?trash|clean|delete|remove|unmatch|fix.?match|'
                        r'mark|watched|scrobble|rating|edit|library/sections', re.I)
_PREEXISTING = {
    ('POST', '/api/plex/match/<int:rating_key>/apply'),
    ('POST', '/api/plex/match/<int:rating_key>/unmatch'),
    ('POST', '/api/settings/tv/scan'),
    ('POST', '/api/settings/tv/unmatched/<int:uid>/match'),
    # v260 (PM): the Storage pill's Free up. Not a library write: deletes finished downloads only through
    # qBittorrent (deleteFiles=true), never inside a library path; the signed-in owner only.
    ('POST', '/api/storage/downloads/cleanup'),
    # v254: What you're missing's Refresh now rebuilds its own pool (reads Plex, Trakt, TMDb, OMDb; writes only
    # its JSON in the data dir: instance.DATA_DIR_ONLY); the signed-in owner only. Not a library write.
    ('POST', '/api/rails/what-youre-missing/refresh'),
    # v267 Activity (Home pill): delete/remove a torrent through qBittorrent. Not a library write; the signed-in
    # owner + instance.writes_allowed('qbit') only (same gate as Downloads Guard / storage cleanup).
    ('DELETE', '/api/downloads/remove/<torrent_hash>'),
    ('POST', '/api/downloads/remove/<torrent_hash>'),
}


def test_no_library_write_route_reachable_in_minimal():
    from app.main import app
    hits = set()
    for rule in app.url_map.iter_rules():
        if not _LIB_WRITE.search(rule.rule):
            continue
        path = re.sub(r'<(?:[^:>]+:)?([^>]+)>', '1', rule.rule)
        for m in (rule.methods or set()) - {'HEAD', 'OPTIONS', 'GET'}:
            if minimal.path_allowed(path, m):
                hits.add((m, rule.rule))
                assert not minimal._playback_allowed(path, m), (m, rule.rule)
    assert hits <= _PREEXISTING, hits - _PREEXISTING


def test_play_path_makes_no_library_writes():
    """The handlers behind the opened routes only read Plex, create a play
    queue and send a play command to the chosen client."""
    import app.main as M
    src = '\n'.join(inspect.getsource(f) for f in (
        M.player_play, M._play_via_plex, M._play_via_kodi, M.plex_resume_info,
        M.create_plex_playqueue, M.get_rooms))
    for needle in ('/refresh', 'scrobble', 'unscrobble', '/library/sections', '.delete(', 'requests.put',
                   'requests.delete', 'markWatched', 'markPlayed', 'VideoLibrary.Scan', 'VideoLibrary.Clean',
                   'fixMatch', '/match'):
        assert needle not in src, needle


def test_player_play_refuses_unconfigured_client(monkeypatch):
    """A caller-supplied address that isn't a configured room device or a
    player Plex lists is refused: no default client is ever picked."""
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    import app.main as M
    monkeypatch.setattr(M, '_get_current_user', lambda: {'id': 1, 'is_admin': 1})
    monkeypatch.setattr(M.user_auth, 'is_plex_approved', lambda u: True)
    monkeypatch.setattr(M, '_plex_client_target', lambda raw: None)
    called = []
    monkeypatch.setattr(M, '_play_via_plex', lambda *a, **k: called.append(a))
    monkeypatch.setattr(M, '_play_via_kodi', lambda *a, **k: called.append(a))
    with M.app.test_request_context('/api/player/play', method='POST',
                                    json={'rating_key': 5, 'client_ip': '10.0.0.99'}):
        r = M.player_play()
    resp, code = r if isinstance(r, tuple) else (r, r.status_code)
    assert code == 400 and not called
    with M.app.test_request_context('/api/player/play', method='POST', json={'rating_key': 5}):
        r = M.player_play()
    assert (r[1] if isinstance(r, tuple) else r.status_code) == 400 and not called
