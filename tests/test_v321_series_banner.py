"""v321 (PM): the series detail header's banner: TheTVDB series artwork type 1 (best score, artwork host only),
stored with the air-time refresh, kept when a later refresh finds none, and returned by the detail view."""
from app import tv_airtimes, tv_monitor, tv_store, tv_tvdb
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.test_file_management import store  # noqa: F401
from tests.test_tvdb_connector import KEY, Maze, Tvdb, tvdb  # noqa: F401
from tests.test_tv_airtimes import SEV

ART = 'https://artworks.thetvdb.com/banners/graphical/371980-g{}.jpg'


class ArtTvdb(Tvdb):
    def __init__(self, arts):
        super().__init__()
        self.arts = arts

    def get(self, path, params, tok):
        if path.endswith('/artworks'):
            self.gets.append((path, tok))
            assert params == {'type': 1}
            return 200, {'data': {'artworks': self.arts.get(int(path.split('/')[2]), [])}}
        return super().get(path, params, tok)


def test_banner_picks_the_best_scored_artwork_host_image():
    fake = ArtTvdb({5: [{'image': ART.format(1), 'score': 10, 'type': 1}, {'image': ART.format(2), 'score': 90, 'type': 1},
                        {'image': 'https://evil.example/x.jpg', 'score': 999, 'type': 1}, {'image': ART.format(3), 'score': 500, 'type': 2}]})
    tv_tvdb.configure(post=fake.post, get=fake.get)
    try:
        assert tv_tvdb.banner(5, (KEY, '')) == ART.format(2)
        assert tv_tvdb.banner(6, (KEY, '')) is None
    finally:
        tv_tvdb.configure()


def test_refresh_stores_the_banner_and_detail_returns_it(scanned, monkeypatch):  # noqa: F811
    monkeypatch.setattr('app.connectors.tvdb_credentials', lambda: (KEY, ''))
    fake = ArtTvdb({371980: [{'image': '/banners/graphical/371980-g1.jpg', 'score': 5, 'type': 1}]})
    fake.series[371980] = {'airs': '21:00', 'country': 'usa', 'eps': {(2, 1): ('2026-10-12', 42)}}
    tv_tvdb.configure(post=fake.post, get=fake.get)
    try:
        tv_airtimes.configure(get=Maze({1: SEV}), sleep=lambda s: None)
        tv_airtimes.refresh(now=5000.0)
        with tv_store.connect() as conn:
            sid = conn.execute('SELECT id FROM series WHERE tvdb_id = 371980').fetchone()['id']
        assert tv_monitor.detail_view(sid)['banner_url'] == ART.format(1)
        fake.arts.clear()                                    # TVDB later has none: the saved banner stays
        tv_airtimes.refresh(now=5000.0 + 10 ** 7)
        assert tv_monitor.detail_view(sid)['banner_url'] == ART.format(1)
    finally:
        tv_tvdb.configure()
        tv_airtimes.configure()


def test_schema_has_the_banner_column(scanned):  # noqa: F811
    with tv_store.connect() as conn:
        assert 'banner_url' in {r[1] for r in conn.execute('PRAGMA table_info(series)')}
