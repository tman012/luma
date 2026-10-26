"""v302: TV import Web Push shows SxxExx + episode name; season packs stay show-level.

Movies unchanged. Import only (never grab). PWA Web Push + in-app bell only.
"""
from __future__ import annotations

import pytest

from app import library_notify


@pytest.fixture(autouse=True)
def _clean():
    library_notify.clear_local()
    library_notify.set_bell_hook(None)
    yield
    library_notify.clear_local()
    library_notify.set_bell_hook(None)


@pytest.fixture
def captured(monkeypatch):
    pushes = []
    bells = []

    def push(**kw):
        pushes.append(kw)
        return True

    monkeypatch.setattr('app.push_service.send_push_notification', push)
    monkeypatch.setattr(library_notify, '_backdrop', lambda *a, **k: '')
    library_notify.set_bell_hook(lambda r: bells.append(r))
    return pushes, bells


# ---- pure push_copy builders -------------------------------------------------

def test_push_copy_movie_unchanged():
    title, body = library_notify.push_copy(
        media_type='movie', title='Dune', year='2021',
    )
    assert title == 'Dune (2021) added!'
    assert body == 'Tap to view details'


def test_push_copy_movie_no_year():
    title, body = library_notify.push_copy(media_type='movie', title='Untitled')
    assert title == 'Untitled added!'
    assert body == 'Tap to view details'


def test_push_copy_tv_single_with_episode_title():
    title, body = library_notify.push_copy(
        media_type='tv', title='Severance', year='2022',
        season=1, episode=4, episode_title='The You You Are',
    )
    assert title == 'Severance (2022) · S01E04 · The You You Are added!'
    assert body == 'Tap to view details'


def test_push_copy_tv_single_zero_pads():
    title, _ = library_notify.push_copy(
        media_type='tv', title='Show', year='2019',
        season=9, episode=2, episode_title='Mr. Jingles',
    )
    assert title == 'Show (2019) · S09E02 · Mr. Jingles added!'


def test_push_copy_tv_single_no_usable_title():
    title, _ = library_notify.push_copy(
        media_type='tv', title='Show', year='2020',
        season=1, episode=3, episode_title=None,
    )
    assert title == 'Show (2020) · S01E03 added!'


def test_push_copy_tv_drops_generic_episode_title():
    for generic in ('Episode 3', 'episode 3', 'Ep. 3', 'Ep 3', 'EP.3'):
        title, _ = library_notify.push_copy(
            media_type='tv', title='Show', year='2020',
            season=1, episode=3, episode_title=generic,
        )
        assert title == 'Show (2020) · S01E03 added!', generic


def test_push_copy_tv_season_pack_show_level():
    """Season set, no episode numbers → show-level (full-season / pack)."""
    title, body = library_notify.push_copy(
        media_type='tv', title='Severance', year='2022', season=1,
    )
    assert title == 'Severance (2022) added!'
    assert body == 'Tap to view details'


def test_push_copy_tv_multi_ep_range():
    title, body = library_notify.push_copy(
        media_type='tv', title='The IT Crowd', year='2006',
        season=1, episodes=[1, 2],
    )
    assert title == 'The IT Crowd (2006) · S01E01–E02 added!'
    assert body == 'Tap to view details'


def test_push_copy_tv_multi_ep_three():
    title, _ = library_notify.push_copy(
        media_type='tv', title='Show', year='2021',
        season=2, episodes=[4, 5, 6],
    )
    assert title == 'Show (2021) · S02E04–E06 added!'


def test_push_copy_tv_no_year_omits_parens():
    title, _ = library_notify.push_copy(
        media_type='tv', title='Show', season=1, episode=1, episode_title='Pilot',
    )
    assert title == 'Show · S01E01 · Pilot added!'


# ---- notify_library_added integration ---------------------------------------

def test_notify_tv_single_episode_push_and_url(captured):
    pushes, bells = captured
    library_notify.notify_library_added(
        media_type='tv', title='Severance', year='2022', tmdb_id=95396,
        season=1, episode=4, episode_title='The You You Are',
    )
    assert pushes[0]['title'] == 'Severance (2022) · S01E04 · The You You Are added!'
    assert pushes[0]['body'] == 'Tap to view details'
    assert pushes[0]['url'] == '/?modal=tv&tmdb=95396&season=1&episode=4'
    assert bells[0]['episode_number'] == 4
    assert bells[0]['episode_title'] == 'The You You Are'


def test_notify_tv_season_pack_show_level(captured):
    pushes, _ = captured
    library_notify.notify_library_added(
        media_type='tv', title='Severance', year='2022', tmdb_id=95396, season=2,
    )
    assert pushes[0]['title'] == 'Severance (2022) added!'
    assert pushes[0]['body'] == 'Tap to view details'
    assert pushes[0]['url'] == '/?modal=tv&tmdb=95396&season=2'


def test_notify_tv_multi_ep_range(captured):
    pushes, _ = captured
    library_notify.notify_library_added(
        media_type='tv', title='The IT Crowd', year='2006', tmdb_id=1220,
        season=1, episodes=[1, 2],
    )
    assert pushes[0]['title'] == 'The IT Crowd (2006) · S01E01–E02 added!'
    assert pushes[0]['body'] == 'Tap to view details'
    # Multi-ep deep-links to season, not a single bogus episode.
    assert pushes[0]['url'] == '/?modal=tv&tmdb=1220&season=1'


def test_notify_movie_unchanged(captured):
    pushes, _ = captured
    library_notify.notify_library_added(
        media_type='movie', title='Dune', year='2021', tmdb_id=438631,
    )
    assert pushes[0]['title'] == 'Dune (2021) added!'
    assert pushes[0]['body'] == 'Tap to view details'


# ---- _notify_imported wiring -------------------------------------------------

def test_notify_imported_single_ep_passes_detail(monkeypatch, captured):
    """tv_downloads._notify_imported → notify with season/episode/title."""
    from app import tv_downloads

    class FakeRow(dict):
        def __getitem__(self, k):
            return dict.get(self, k)

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=()):
            class R:
                def fetchone(self_inner):
                    if 'FROM series' in sql:
                        return FakeRow(title='Severance', year=2022, tmdb_id=95396)
                    if 'FROM episodes' in sql and 'id = ?' in sql:
                        return FakeRow(season_number=1, episode_number=4, title='The You You Are')
                    return None

                def __iter__(self_inner):
                    return iter([])

            return R()

    monkeypatch.setattr(tv_downloads.tv_store, 'connect', lambda: FakeConn())
    calls = []
    real = library_notify.notify_library_added

    def wrap(**kw):
        calls.append(kw)
        return real(**kw)

    monkeypatch.setattr(library_notify, 'notify_library_added', wrap)
    # Import via module attribute used inside _notify_imported
    import app.library_notify as ln
    monkeypatch.setattr(ln, 'notify_library_added', wrap)

    tv_downloads._notify_imported({
        'series_id': 1,
        'episode_ids': [42],
        'files': [{'path': '/TV/Severance/Season 01/x.mkv'}],
        'name': 'Severance',
    })
    pushes, _ = captured
    assert len(calls) == 1
    assert calls[0]['season'] == 1
    assert calls[0]['episode'] == 4
    assert calls[0]['episode_title'] == 'The You You Are'
    assert calls[0].get('episodes') in (None, [])
    assert pushes[0]['title'] == 'Severance (2022) · S01E04 · The You You Are added!'


def test_notify_imported_season_pack_show_level(monkeypatch, captured):
    """Many same-season eids → season only, show-level push (no invented SxxExx)."""
    from app import tv_downloads

    class FakeRow(dict):
        pass

    eids = list(range(100, 110))  # 10 episodes → pack

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=()):
            class R:
                def fetchone(self_inner):
                    if 'FROM series' in sql:
                        return FakeRow(title='Severance', year=2022, tmdb_id=95396)
                    return None

                def __iter__(self_inner):
                    if 'FROM episodes' in sql:
                        return iter([
                            FakeRow(season_number=1, episode_number=i, title=f'Ep {i}')
                            for i in range(1, 11)
                        ])
                    return iter([])

            return R()

    monkeypatch.setattr(tv_downloads.tv_store, 'connect', lambda: FakeConn())
    calls = []
    import app.library_notify as ln
    real = ln.notify_library_added

    def wrap(**kw):
        calls.append(kw)
        return real(**kw)

    monkeypatch.setattr(ln, 'notify_library_added', wrap)

    tv_downloads._notify_imported({
        'series_id': 1,
        'episode_ids': eids,
        'files': [{'path': '/TV/Severance/Season 01/x.mkv'}],
    })
    pushes, _ = captured
    assert calls[0]['season'] == 1
    assert calls[0]['episode'] is None
    assert calls[0].get('episodes') in (None, [])
    assert pushes[0]['title'] == 'Severance (2022) added!'


def test_notify_imported_multi_ep_file_range(monkeypatch, captured):
    """≤3 contiguous same-season eids → S01E01–E02 range in push title."""
    from app import tv_downloads

    class FakeRow(dict):
        pass

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=()):
            class R:
                def fetchone(self_inner):
                    if 'FROM series' in sql:
                        return FakeRow(title='The IT Crowd', year=2006, tmdb_id=1220)
                    return None

                def __iter__(self_inner):
                    if 'FROM episodes' in sql:
                        return iter([
                            FakeRow(season_number=1, episode_number=1, title='Yesterday\'s Jam'),
                            FakeRow(season_number=1, episode_number=2, title='Calamity Jen'),
                        ])
                    return iter([])

            return R()

    monkeypatch.setattr(tv_downloads.tv_store, 'connect', lambda: FakeConn())
    import app.library_notify as ln
    calls = []
    real = ln.notify_library_added

    def wrap(**kw):
        calls.append(kw)
        return real(**kw)

    monkeypatch.setattr(ln, 'notify_library_added', wrap)

    tv_downloads._notify_imported({
        'series_id': 1,
        'episode_ids': [11, 12],
        'files': [{'path': '/TV/The IT Crowd/Season 01/x.mkv'}],
    })
    pushes, _ = captured
    assert calls[0]['season'] == 1
    assert calls[0]['episodes'] == [1, 2]
    assert pushes[0]['title'] == 'The IT Crowd (2006) · S01E01–E02 added!'


def test_grab_path_does_not_call_notify():
    """Guard: _notify_imported is only referenced from the import-success path, not grab."""
    import ast
    from pathlib import Path
    src = Path('app/tv_downloads.py').read_text()
    tree = ast.parse(src)
    # Collect names of functions that call _notify_imported
    callers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    if child.func.id == '_notify_imported':
                        callers.append(node.name)
    assert callers == ['tick'], callers
    # Ensure grab helpers don't mention notify_library_added
    assert 'notify_library_added' not in src.split('def finish_grab')[1].split('def ')[0]
