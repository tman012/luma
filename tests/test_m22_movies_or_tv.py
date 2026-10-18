"""PM (M22): a Library path is used by Movies or by TV, never both. Blocked
both ways with a short sentence-case error; nothing is saved."""
import pytest

from app import file_management as fm, tv_settings
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE
from tests.test_tv_libraries import tv_env, owner, _add, _put  # noqa: F401

RULES = '/api/settings/library-rules'


def _rules(c, rules, fb):
    return c.put(RULES, json={'rules': rules, 'fallback_path_id': fb}, environ_base=REMOTE, headers=HDR)


def _rule(pid, rid=None):
    r = {'enabled': True, 'path_id': pid, 'released_within': {'n': 1, 'unit': 'years'}, 'resolution': ['1080p'], 'source': []}
    return dict(r, id=rid) if rid else r


@pytest.fixture
def three(owner, tv_env):  # noqa: F811
    ids = {n: _add(owner, n, tv_env / d) for n, d in (('Movies', 'Movies'), ('TV', 'TV'), ('Anime', 'Anime'))}
    return ids


def test_tv_cant_select_a_movies_rule_destination(owner, three):
    assert _rules(owner, [_rule(three['Anime'])], three['Movies']).status_code == 200
    before = tv_settings.store_path().read_bytes() if tv_settings.store_path().exists() else None
    r = _put(owner, [three['TV'], three['Anime']])
    assert r.status_code == 409 and r.get_json() == {'errors': {'selected': 'This path is used by Movies'}}
    assert (tv_settings.store_path().read_bytes() if tv_settings.store_path().exists() else None) == before
    assert _put(owner, [three['TV']]).status_code == 200


def test_tv_cant_select_the_explicit_movies_fallback(owner, three):
    assert _rules(owner, [], three['Movies']).status_code == 200
    r = _put(owner, [three['Movies']])
    assert r.status_code == 409 and r.get_json()['errors']['selected'] == 'This path is used by Movies'
    assert tv_settings.selected_ids() == []


def test_tv_cant_select_the_implicit_single_path(owner, tv_env):  # noqa: F811
    # Reviewer F2 / Tester M31: the only path is Movies' implicit fallback, so
    # TV taking it would silently stop Movies; it counts as used by Movies
    only = _add(owner, 'TV', tv_env / 'TV')
    before = fm.store_path().read_bytes()
    r = _put(owner, [only])
    assert r.status_code == 409 and r.get_json()['errors']['selected'] == 'This path is used by Movies'
    assert tv_settings.selected_ids() == [] and fm.store_path().read_bytes() == before


def test_movies_cant_choose_a_tv_path_as_a_rule_destination(owner, three):
    assert _put(owner, [three['TV']]).status_code == 200
    before = fm.store_path().read_bytes()
    r = _rules(owner, [_rule(three['Anime']), _rule(three['TV'])], three['Movies'])
    assert r.status_code == 400 and r.get_json()['errors'] == {'rules.1.path_id': 'This path is used by TV'}
    assert fm.store_path().read_bytes() == before


def test_movies_cant_choose_a_tv_path_as_the_fallback(owner, three):
    assert _put(owner, [three['TV']]).status_code == 200
    r = _rules(owner, [], three['TV'])
    assert r.status_code == 400 and r.get_json()['errors'] == {'fallback_path_id': 'This path is used by TV'}


def test_an_existing_overlap_does_not_lock_the_other_rules(owner, three):
    # a rule already on a path TV selected before M22 (migration, older data):
    # other edits still save; only a new choice of a TV path is refused
    data = fm.load()
    data.update(rules=[dict(_rule(three['TV']), id='r_0000aaaa')], fallback_path_id=three['Movies'])
    fm.save(data)
    tv_settings.save({**tv_settings.load(), 'libraries': [three['TV']]})
    r = _rules(owner, [_rule(three['TV'], 'r_0000aaaa'), _rule(three['Anime'])], three['Movies'])
    assert r.status_code == 200, r.get_json()


def test_deselecting_never_trips_the_check(owner, three):
    assert _put(owner, [three['TV'], three['Anime']]).status_code == 200
    data = fm.load()
    data.update(rules=[dict(_rule(three['Anime']), id='r_0000aaaa')], fallback_path_id=three['Movies'])
    fm.save(data)                                  # older overlap on Anime
    assert _put(owner, [three['Anime']]).status_code == 200
    assert _put(owner, []).status_code == 200


def test_errors_are_short_and_sentence_case():
    from app import library_selection
    for e in (fm.USED_BY_TV, library_selection.USED_BY_MOVIES):
        assert e[0].isupper() and e[1:] == e[1:].replace('Path', 'path') and not e.endswith('.') and len(e) <= 32
