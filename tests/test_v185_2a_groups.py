"""v185.2a (Taylor): preferred release groups, empty by default, in the owner's order (Settings > TV)."""
from app import tv_search, tv_settings


def _c(title, rank=0, seeders=10):
    return {'item': {'title': title, 'seeders': seeders}, 'covers': [1], 'rank': rank, 'proper': False,
            'pack': False, 'prefer_packs': True}


CANDS = [_c('Silo.S03E04.1080p.WEB-DL-FLUX', seeders=50), _c('Silo.S03E04.1080p.WEB-DL-NTb', seeders=5),
         _c('Silo.S03E04.1080p.WEB-DL-KiNGS', seeders=20), _c('Silo.S03E04.720p.WEB-DL-NTb', rank=3, seeders=99)]


def _pick(monkeypatch, groups):
    monkeypatch.setattr(tv_settings, 'load', lambda: {**tv_settings.empty(), 'preferred_groups': groups})
    return tv_search.choose(list(CANDS))[0]['item']['title']


def test_nothing_is_seeded():
    assert tv_settings.empty()['preferred_groups'] == []
    assert 'NTb' not in repr(tv_settings.DEFAULT_GROUPS)


def test_an_empty_list_leaves_the_ranking_unchanged(monkeypatch):
    assert _pick(monkeypatch, []) == 'Silo.S03E04.1080p.WEB-DL-FLUX'       # seeders decide, as before


def test_preferred_groups_win_at_the_same_quality_in_the_owner_s_order(monkeypatch):
    assert _pick(monkeypatch, ['ntb']) == 'Silo.S03E04.1080p.WEB-DL-NTb'   # never the 720p NTb (quality first)
    assert _pick(monkeypatch, ['KiNGS', 'NTb']) == 'Silo.S03E04.1080p.WEB-DL-KiNGS'
    assert _pick(monkeypatch, ['NTb', 'KiNGS']) == 'Silo.S03E04.1080p.WEB-DL-NTb'


def test_put_validates_and_keeps_the_order():
    d = tv_settings.empty()
    assert tv_settings.apply_put(d, {'preferred_groups': ['KiNGS', ' NTb ', 'ntb']}) == {}
    assert d['preferred_groups'] == ['KiNGS', 'NTb']
    for bad in ('NTb', [1], ['a/b'], ['x' * 41], ['g'] * 21):
        assert tv_settings.apply_put(tv_settings.empty(), {'preferred_groups': bad}) == {'preferred_groups': 'Invalid'}
    assert tv_settings._normalize({'preferred_groups': ['NTb']})['preferred_groups'] == ['NTb']
    assert tv_settings._normalize({})['preferred_groups'] == []
