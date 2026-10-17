"""Plex responses with invalid UTF-8 (seen on /library/sections/1/all) still
parse: bad bytes become U+FFFD instead of losing the whole response, so the
kiosk hero and Home rails keep working."""

from app.plex_service import PlexService

BODY = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<MediaContainer size="2" totalSize="2">'
        b'<Video ratingKey="1" type="movie" title="Am\xe9lie" addedAt="10" art="/library/metadata/1/art/1"/>'
        b'<Video ratingKey="2" type="movie" title="Caf\xc3\xa9 \xff\xfe Society\x01" addedAt="9" art="/library/metadata/2/art/1"/>'
        b'</MediaContainer>')


class _Resp:
    status_code = 200
    content = BODY

    def raise_for_status(self):
        pass


class _Session:
    headers = {}

    def get(self, url, params=None, timeout=None):
        return _Resp()


def _plex():
    p = PlexService('http://plex.test:32400', 'tok')
    p._session = _Session()
    return p


def test_invalid_utf8_still_parses():
    root = _plex()._get('/library/sections/1/all')
    assert root is not None
    titles = [v.get('title') for v in root.findall('.//Video')]
    assert titles == ['Am\ufffdlie', 'Caf\u00e9 \ufffd\ufffd Society']   # valid UTF-8 kept, bad bytes replaced, control char dropped


def test_hero_and_rails_paths_get_items():
    out = _plex().get_all_movies(1, sort='addedAt:desc', limit=10)
    assert [m['rating_key'] for m in out['movies']] == [1, 2]
    assert out['total'] == 2
    assert len(_plex().get_recently_added(section_id=1, limit=10)) == 2
