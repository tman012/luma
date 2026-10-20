"""v206 (Reviewer): movie_dupes fails closed when the movie title is missing: skip, logged at INFO,
and nothing (disk or qBittorrent) is asked."""
import logging

import pytest

from app import movie_dupes


@pytest.mark.parametrize('title', [None, '', '   '])
def test_missing_title_skips(title, monkeypatch, caplog):
    monkeypatch.setattr(movie_dupes, 'on_disk', lambda *a, **k: pytest.fail('disk asked'))
    monkeypatch.setattr(movie_dupes, 'in_qbit', lambda *a, **k: pytest.fail('qbit asked'))
    with caplog.at_level(logging.INFO, logger='app.movie_dupes'):
        assert movie_dupes.check(title, 2026) == movie_dupes.NO_TITLE
    assert any(r.levelno == logging.INFO and movie_dupes.NO_TITLE in r.getMessage() for r in caplog.records)


def test_title_present_still_checked(monkeypatch):
    monkeypatch.setattr(movie_dupes, 'on_disk', lambda *a, **k: None)
    monkeypatch.setattr(movie_dupes, 'in_qbit', lambda *a, **k: {'hit': None})
    assert movie_dupes.check('Dune', 2021) is None


def test_scanner_grab_without_title_is_skipped():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / 'app' / 'movie_scanner.py').read_text()
    assert "movie_dupes.check(m.get('title'), m.get('year')" in src and "if m.get('title'):\n        from app import movie_dupes" not in src
