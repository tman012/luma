"""v301: search ranking tiers (app/search_rank.py) and /api/library/search ordering."""
from app.search_rank import match_tier, order_by_match


def test_tiers():
    assert match_tier('the box', 'The Box') == 0
    assert match_tier('box', 'The Box') == 0
    assert match_tier('spider man', 'Spider-Man') == 0
    assert match_tier('the box', 'The Box of Delights') == 1
    assert match_tier('the box', 'Mystery in the Box') == 2
    assert match_tier('knight dark', 'The Dark Knight') == 3
    assert match_tier('star wa', 'Star Wars: A New Hope') == 3
    assert match_tier('the box', 'The Boxer') == 3
    assert match_tier('the box', 'The Boy') == 4
    assert match_tier('the box', 'A Bronx Tale') == 4


def test_order_is_stable_and_tagged():
    items = [{'title': 'The Boy'}, {'title': 'The Boxer'}, {'title': 'Boy A'}, {'title': 'The Box'}]
    out = order_by_match('the box', items)
    assert [i['title'] for i in out] == ['The Box', 'The Boxer', 'The Boy', 'Boy A']
    assert [i['match_tier'] for i in out] == [0, 3, 4, 4]
