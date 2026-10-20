"""v217: prod 1b4ea74's reviews endpoints answer in minimal builds (GET only); OMDb ratings stay closed."""
from app import minimal


def test_reviews_routes_allowed():
    assert minimal.path_allowed('/api/reviews/top', 'GET')
    assert minimal.path_allowed('/api/rt/reviews', 'GET')
    assert not minimal.path_allowed('/api/reviews/top', 'POST')


def test_omdb_ratings_stay_closed():
    assert not minimal.path_allowed('/api/ratings/Heat', 'GET')


def test_reviews_route_answers_in_minimal(monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    import app.rt_top_reviews as rtr
    monkeypatch.setattr(rtr, 'get_top_reviews', lambda t, y, m: {'source': 'critic', 'reviews': [{'critic': 'A'}], 'url': None})
    from app.main import app
    app.config['TESTING'] = True
    with app.test_request_context('/api/reviews/top?title=Heat&year=1995'):
        from app.main import top_reviews
        r = top_reviews()
    assert r.get_json()['reviews'] == [{'critic': 'A'}]
