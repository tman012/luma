"""v251 (Reviewer HOLD on 2d3d4e0): the importer finds the show with the same release key as search and the
feed, so a grabbed 'Grey.s.Anatomy.S21E01' torrent imports to Grey's Anatomy instead of waiting on NO_SHOW
forever. qBittorrent, Plex and TMDb are mocked (the download-watch fixture)."""
import pytest

from app import tv_downloads, tv_store
from tests.test_tv_download_watch_v158a import H1, dl, grab, _release_names  # noqa: F401
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.test_daily_match import _ep_id, _sev


def _greys(kind='standard'):
    with tv_store.connect() as conn:
        sid = _sev(conn, kind)
    with tv_store.write() as conn:
        conn.execute("UPDATE series SET title = ?, aliases = '[]', year = NULL WHERE id = ?", ("Grey's Anatomy", sid))
        conn.execute('UPDATE episodes SET season_number = 21, episode_number = 1 WHERE series_id = ?'
                     ' AND season_number = 2 AND episode_number = 3', (sid,))
    return sid


@pytest.mark.parametrize('name', ['Grey.s.Anatomy.S21E01.1080p.WEB.h264-ELiTE', "Grey's.Anatomy.S21E01.1080p.WEB.H264-playWEB",
                                  'Greys.Anatomy.S21E01.720p.HEVC.x265-MeGusta', 'Grey s Anatomy S21E01 1080p WEB H264-MeGusta'])
def test_series_for_finds_greys_anatomy_from_every_release_spelling(dl, name):
    sid = _greys()
    assert (tv_downloads._series_for(name) or {}).get('id') == sid


def test_grey_s_anatomy_torrent_imports_to_greys_anatomy(dl):
    sid = _greys()
    eid = _ep_id(sid, 21, 1)
    name = 'Grey.s.Anatomy.S21E01.1080p.WEB.h264-ELiTE'
    grab(dl, H1, name, [name + '.mkv'])
    out = dl.tick()
    assert out == {'checked': 1, 'imported': 1}, tv_downloads.status(H1)
    st = tv_downloads.status(H1)
    assert st['state'] == 'imported'
    assert st['files'][0]['linked'] is True
    linked = rows('SELECT 1 FROM episode_file_links l JOIN episode_files f ON f.id = l.file_id'
                  " WHERE l.episode_id = ? AND f.state = 'linked'", eid)
    assert linked


def test_is_daily_lookup_through_series_for_still_works(dl):
    sid = _greys('daily')
    assert tv_downloads._is_daily((tv_downloads._series_for('Grey.s.Anatomy.2026.10.19.1080p.WEB.h264-GRP') or {}).get('id'))
    with tv_store.write() as conn:
        conn.execute("UPDATE series SET series_type = 'standard' WHERE id = ?", (sid,))
    assert not tv_downloads._is_daily((tv_downloads._series_for('Grey.s.Anatomy.2026.10.19.1080p.WEB.h264-GRP') or {}).get('id'))
    assert tv_downloads._series_for('Grey.Anatomy.S21E01.1080p.WEB.h264-GRP') is None          # no lone 's': no match
