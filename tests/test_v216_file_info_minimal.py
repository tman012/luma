"""v216: the wide popup's file-info sheet (and its owner Fix Match) reach their routes in minimal builds."""
from app import minimal


def test_file_info_get_allowed():
    assert minimal.path_allowed('/api/plex/file-info/123', 'GET')
    assert not minimal.path_allowed('/api/plex/file-info/123', 'POST')


def test_fix_match_allowed():
    assert minimal.path_allowed('/api/plex/match/123/search', 'GET')
    assert minimal.path_allowed('/api/plex/match/123/apply', 'POST')


def test_routes_still_check_sign_in():
    src = open('app/main.py').read()
    i = src.index("def plex_file_info(")
    assert '_require_auth()' in src[i:i + 600]
    for name in ('plex_match_search', 'plex_match_apply', 'plex_match_unmatch'):
        j = src.index(f"def {name}(")
        assert '_require_admin()' in src[j:j + 300]
