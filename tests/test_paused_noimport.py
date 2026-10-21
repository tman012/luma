"""PM: no importer (TV watcher, movie importer, library_mover) ever imports a paused/stopped torrent."""
import pytest

from app import library_mover as lm, movie_import, tv_downloads

PAUSED = ['pausedUP', 'pausedDL', 'stoppedUP', 'stoppedDL']


def tor(state, **kw):
    return {'hash': 'a' * 40, 'name': 'Time.S03E03.mkv', 'progress': 1, 'amount_left': 0, 'state': state, **kw}


@pytest.mark.parametrize('state', PAUSED)
def test_paused_complete_not_imported(state):
    assert lm.is_paused(tor(state))
    assert not tv_downloads._complete(tor(state))
    assert not movie_import._complete(tor(state))
    assert state not in lm.COMPLETED_STATES


@pytest.mark.parametrize('state', PAUSED)
def test_resumed_is_imported(state):
    resumed = 'stalledUP' if state.endswith('UP') else 'uploading'
    assert tv_downloads._complete(tor(resumed))
    assert movie_import._complete(tor(resumed))
    assert resumed in lm.COMPLETED_STATES


def test_incomplete_still_not_imported():
    assert not tv_downloads._complete(tor('downloading', progress=0.5))
