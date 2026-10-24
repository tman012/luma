"""v273: Luma library-import notifications — PWA Web Push + bell only (prod AutoRenamer copy).
Movies via library_mover, TV via tv_downloads. No Telegram, no email, no Sonarr/Radarr webhooks.
Outside services mocked; tokens never logged."""
from __future__ import annotations

import logging

import pytest

from app import library_notify, library_mover


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


def test_movie_copy_matches_prod_autorenamer(captured):
    pushes, bells = captured
    library_notify.notify_library_added(
        media_type='movie', title='Dune', year='2021', tmdb_id=438631, dest_path='/Movies/Dune (2021)',
    )
    assert pushes == [{
        'title': 'Dune (2021) added!',
        'body': 'Tap to view details',
        'tag': 'movie-Dune',
        'url': '/?modal=movie&tmdb=438631',
        'image': '',
    }]
    assert len(bells) == 1 and bells[0]['title'] == 'Dune'


def test_no_telegram_or_email_on_notify(captured, monkeypatch):
    """v273 PM: notify path must not call Telegram pending or any email send."""
    pushes, _ = captured
    called = {'tg': 0, 'mail': 0}

    def boom_tg(*a, **k):
        called['tg'] += 1
        raise AssertionError('check_and_notify must not run')

    monkeypatch.setattr('app.pending_requests.check_and_notify', boom_tg, raising=False)
    # Ensure library_notify has no telegram helper left.
    assert not hasattr(library_notify, '_telegram_pending')
    library_notify.notify_library_added(
        media_type='movie', title='Dune', year='2021', tmdb_id=438631,
    )
    assert len(pushes) == 1
    assert called['tg'] == 0


def test_movie_without_year_omits_parens(captured):
    pushes, _ = captured
    library_notify.notify_library_added(media_type='movie', title='Untitled', tmdb_id=1)
    assert pushes[0]['title'] == 'Untitled added!'


def test_tv_single_episode_modal_deep_link(captured):
    pushes, _ = captured
    library_notify.notify_library_added(
        media_type='tv', title='Severance', year='2022', tmdb_id=95396,
        season=1, episode=4, episode_title='The You You Are',
    )
    assert pushes[0]['title'] == 'Severance (2022) added!'
    assert pushes[0]['body'] == 'Tap to view details'
    assert pushes[0]['url'] == '/?modal=tv&tmdb=95396&season=1&episode=4'


def test_mover_done_fires_notify_exists_does_not(captured, monkeypatch):
    pushes, _ = captured
    calls = {'n': 0}
    real = library_notify.notify_library_added

    def wrap(**kw):
        calls['n'] += 1
        return real(**kw)

    monkeypatch.setattr(library_notify, 'notify_library_added', wrap)
    job = {'title': 'Dune', 'year': '2021', 'tmdb_id': 438631, 'media_type': 'movie'}
    fields_done = {'state': 'done', 'dest': '/Movies/Dune (2021)', 'files': [{'result': 'copied'}]}
    if fields_done.get('state') == 'done':
        library_notify.notify_library_added(
            media_type='movie', title=str(job.get('title') or ''), year=job.get('year') or '',
            tmdb_id=job.get('tmdb_id'), dest_path=str(fields_done.get('dest') or ''),
            source='library_mover',
        )
    assert calls['n'] == 1
    assert pushes[0]['title'] == 'Dune (2021) added!'
    pushes.clear()
    fields_exists = {'state': 'exists', 'dest': '/Movies/Dune (2021)', 'files': [{'result': 'exists'}]}
    if fields_exists.get('state') == 'done':
        library_notify.notify_library_added(media_type='movie', title='Dune', year='2021', tmdb_id=1)
    assert pushes == []


def test_never_logs_token(caplog, captured, monkeypatch):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(library_notify, '_backdrop', lambda *a, **k: 'https://image.tmdb.org/t/p/w780/x.jpg')
    library_notify.notify_library_added(media_type='movie', title='X', year='2020', tmdb_id=9)
    joined = ' '.join(r.message for r in caplog.records)
    assert '123456:ABC' not in joined


def test_minimal_allowlists_webpush_not_telegram():
    from app import minimal
    for p in (
        '/api/push/vapid-key', '/api/push/subscribe', '/api/push/unsubscribe', '/api/push/test',
        '/api/notifications/unseen', '/api/notifications/mark-seen',
        '/api/settings/connectors/webpush', '/api/settings/connectors/webpush/test',
    ):
        assert minimal.path_allowed(p, 'GET') or minimal.path_allowed(p, 'POST') or minimal.path_allowed(p, 'PUT')
    assert not minimal.path_allowed('/api/settings/connectors/telegram', 'GET')
    assert not minimal.path_allowed('/api/settings/connectors/telegram/test', 'POST')
