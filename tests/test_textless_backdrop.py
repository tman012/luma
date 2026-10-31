"""Detail backdrop vs clearlogo: with a logo, the sheet backdrop is TMDb's best no-language (text-free) one."""
from unittest import mock

import app.main as m


def _bd(path, lang, vote=5.0, count=10, width=3840):
    return {'file_path': path, 'iso_639_1': lang, 'vote_average': vote, 'vote_count': count, 'width': width}


def test_logo_picks_best_textless_backdrop():
    d = {'backdrop_path': '/lang.jpg', 'images': {
        'logos': [{'file_path': '/logo.png', 'iso_639_1': 'en'}],
        'backdrops': [_bd('/lang.jpg', 'en', 9), _bd('/nolang-low.jpg', None, 4), _bd('/nolang-best.jpg', None, 6)]}}
    out = m.choose_backdrop(d)
    assert out['backdrop_path'] == '/nolang-best.jpg'
    assert out['backdrop_path_language'] == '/lang.jpg'
    assert out['backdrop_textless'] is True and out['has_logo'] is True
    assert 'images' not in out


def test_no_logo_keeps_language_backdrop():
    d = {'backdrop_path': '/lang.jpg', 'images': {'logos': [], 'backdrops': [_bd('/lang.jpg', 'en'), _bd('/nolang.jpg', None)]}}
    out = m.choose_backdrop(d)
    assert out['backdrop_path'] == '/lang.jpg' and out['has_logo'] is False and out['backdrop_textless'] is False
    assert 'backdrop_path_language' not in out


def test_logo_but_no_textless_backdrop_falls_back():
    d = {'backdrop_path': '/lang.jpg', 'images': {'logos': [{'file_path': '/l.png', 'iso_639_1': None}], 'backdrops': [_bd('/lang.jpg', 'en')]}}
    out = m.choose_backdrop(d)
    assert out['backdrop_path'] == '/lang.jpg' and out['backdrop_textless'] is False and out['has_logo'] is True


def test_already_textless_unchanged():
    d = {'backdrop_path': '/nolang.jpg', 'images': {'logos': [{'file_path': '/l.png', 'iso_639_1': 'en'}], 'backdrops': [_bd('/nolang.jpg', None, 3), _bd('/other.jpg', None, 9)]}}
    out = m.choose_backdrop(d)
    assert out['backdrop_path'] == '/nolang.jpg' and out['backdrop_textless'] is True and 'backdrop_path_language' not in out


def test_foreign_logo_only_does_not_count():
    d = {'backdrop_path': '/lang.jpg', 'images': {'logos': [{'file_path': '/de.png', 'iso_639_1': 'de'}], 'backdrops': [_bd('/nolang.jpg', None)]}}
    assert m.choose_backdrop(d)['backdrop_path'] == '/lang.jpg'


def test_no_images_and_non_dict():
    assert m.choose_backdrop(None) is None
    out = m.choose_backdrop({'backdrop_path': '/x.jpg'})
    assert out['backdrop_path'] == '/x.jpg' and out['backdrop_textless'] is False


def _resp(payload):
    r = mock.Mock(); r.json.return_value = payload; r.raise_for_status.return_value = None; return r


def test_movie_and_tv_details_request_images_and_choose():
    payload = {'backdrop_path': '/lang.jpg', 'images': {'logos': [{'file_path': '/l.png', 'iso_639_1': 'en'}], 'backdrops': [_bd('/nolang.jpg', None)]}}
    c = m.TMDBClient('k')
    for fn in (c.get_movie_details, c.get_tv_details):
        with mock.patch.object(m.requests, 'get', return_value=_resp(dict(payload, images=dict(payload['images'])))) as g:
            out = fn(1)
        params = g.call_args.kwargs['params']
        assert 'images' in params['append_to_response'].split(',') and params['include_image_language'] == 'en,null'
        assert out['backdrop_path'] == '/nolang.jpg'
