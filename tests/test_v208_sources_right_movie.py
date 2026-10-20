"""v208 (PM): Sources and the watchlist scanner only take releases of THIS movie: title with sequel
numbers, year ±1. 'Shrek 5' never matches Shrek (2001); an unreleased movie with no match shows nothing."""
import pytest

import app.main
from app import movie_scanner
from app.tracker_search import LumaTrackersClient

REL = ['Shrek.2001.1080p.BluRay.x264-GRP', 'Shrek.2.2004.1080p.WEB-DL', 'Shrek.5.2026.1080p.WEB-DL.DDP5.1-GRP',
       'Shrek.5.2027.2160p.WEB-DL-GRP', 'Shrek.5.2029.1080p.WEB-DL-GRP', 'Shrek.5.1080p.WEB-DL-GRP']


def test_scanner_matches_title_sequel_and_year():
    m = {'title': 'Shrek 5', 'year': 2026}
    assert [t for t in REL if movie_scanner.matches(t, m)] == REL[2:4]          # 2026 and 2027 (±1)
    assert not movie_scanner.matches('Shrek.2001.1080p', {'title': 'Shrek 5', 'year': 2026})
    assert movie_scanner.matches('Shrek.2001.1080p', {'title': 'Shrek', 'year': 2001})
    assert not movie_scanner.matches('Shrek.5.2026.1080p', {'title': 'Shrek', 'year': 2001})
    assert movie_scanner.matches('Shrek.2000.1080p', {'title': 'Shrek', 'year': 2001})              # -1
    assert not movie_scanner.matches('Shrek.1999.1080p', {'title': 'Shrek', 'year': 2001})


def test_scanner_never_takes_a_yearless_release_when_the_year_is_known():
    assert not movie_scanner.matches('Shrek 5', {'title': 'Shrek 5', 'year': 2026})


class _Fake(LumaTrackersClient):
    def __init__(self, titles):
        self.titles = titles

    def search_all(self, *a, **k):
        return [{'title': t, 'guid': t, 'download_url': 'x', 'indexer': 'T'} for t in self.titles]


def _get(test_app, monkeypatch, titles, qs):
    import app.indexer_api as ia
    monkeypatch.setattr(ia, 'get_indexer_client', lambda s: _Fake(titles))
    monkeypatch.setattr(app.main, '_indexer_viewer_denied', lambda: None)
    app.main._all_results_cache.clear()
    return test_app.test_client().get('/api/sources/all-results?' + qs).get_json()


def test_sources_drop_other_movies(test_app, monkeypatch):
    j = _get(test_app, monkeypatch, REL, 'title=Shrek%205&media_type=movie&year=2026&tmdb_id=808')
    assert [r['title'] for r in j['releases']] == REL[2:4]


def test_unreleased_movie_with_only_the_original_shows_nothing(test_app, monkeypatch):
    j = _get(test_app, monkeypatch, REL[:2], 'title=Shrek%205&media_type=movie&year=2026')
    assert j['releases'] == []


def test_plain_text_override_without_year_is_untouched(test_app, monkeypatch):
    j = _get(test_app, monkeypatch, REL[:2], 'title=shrek&media_type=movie')
    assert len(j['releases']) == 2


@pytest.mark.parametrize('name,hit', [('Shrek.2001.1080p', False), ('Shrek.5.2027.1080p', True), ('Shrek.5.2029', False)])
def test_dupes_match_title_and_year_pm1(name, hit, monkeypatch):
    from app import library_mover as mover, movie_dupes

    class S:
        def get(self, *a, **k):
            class R:
                status_code = 200
                def json(self):
                    return [{'name': name}]
            return R()
    monkeypatch.setattr(mover, '_qbit_session', lambda *a: (S(), 'http://q'))
    monkeypatch.setattr(mover, '_close', lambda s: None)
    assert bool(movie_dupes.in_qbit('Shrek 5', 2026)['hit']) is hit
