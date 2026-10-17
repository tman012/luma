"""/login error states (Designer spec, cursor/login-error-states).

Full mode: the error is plain #ef4444 13px text between Remember me and
Sign in, on a line whose height is always reserved (no box, no jump), and
the username comes back after a wrong password. Minimal mode: a stale form
post lands on the SPA form with ?expired=1, which says the session expired.
"""

import re

import pytest

import app.user_auth as user_auth

IP = {'REMOTE_ADDR': '203.0.113.9'}


def _token(c):
    page = c.get('/login', environ_base=IP).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def _post(c, username, password, token=None):
    return c.post('/login', data={'username': username, 'password': password,
                                  'csrf_token': _token(c) if token is None else token},
                  environ_base=IP)


def _error_line(html):
    m = re.search(r'<div class="error[^"]*" role="alert" aria-live="polite">([^<]*)</div>', html)
    assert m, 'error line missing'
    return m.group(1)


def _css(html, sel):
    return re.search(re.escape(sel) + r' \{([^}]*)\}', html).group(1)


@pytest.fixture
def full(test_app, monkeypatch):
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    user_auth.create_user('owner', '1234', is_admin=True)
    return test_app.test_client()


def test_clean_form_reserves_an_empty_error_line(full):
    html = full.get('/login', environ_base=IP).get_data(as_text=True)
    assert _error_line(html) == ''
    # between Remember me and Sign in
    assert html.index('Remember me') < html.index('class="error') < html.index('<button type="submit">Sign in')
    assert html.index('class="error') > html.index('name="password"')


def test_error_line_style_is_plain_red_13px_with_reserved_height(full):
    html = full.get('/login', environ_base=IP).get_data(as_text=True)
    css = _css(html, '.error')
    assert 'color: var(--color-danger)' in css and 'font-size: 13px' in css
    assert re.search(r'--color-danger: #ef4444;', html)
    assert 'min-height: 18px' in css and 'line-height: 18px' in css
    for boxy in ('background', 'border', 'padding', 'border-radius'):
        assert boxy not in css, boxy


def test_wrong_password_shows_the_line_and_keeps_the_username(full):
    r = _post(full, 'owner', 'wrong')
    assert r.status_code == 302
    html = full.get(r.headers['Location'], environ_base=IP).get_data(as_text=True)
    assert _error_line(html) == 'Invalid email, username, or password'
    assert 'name="username" value="owner"' in html
    # same markup as the clean form, so nothing moves
    assert html.count('class="error') == 1
    # the name is read once: a plain reload of /login starts clean
    html = full.get('/login', environ_base=IP).get_data(as_text=True)
    assert _error_line(html) == '' and 'name="username" value=""' in html


def test_a_crafted_error_link_does_not_prefill_a_name(full):
    html = full.get('/login?error=x', environ_base=IP).get_data(as_text=True)
    assert 'name="username" value=""' in html


def test_expired_session_keeps_the_name_on_the_same_line(full):
    r = _post(full, 'owner', 'whatever', token='stale')
    assert r.status_code == 400
    html = r.get_data(as_text=True)
    assert _error_line(html) == 'Your session expired. Sign in again.'
    assert 'name="username" value="owner"' in html


def test_success_clears_the_remembered_name(full):
    _post(full, 'owner', 'wrong')
    r = _post(full, 'owner', '1234')
    assert r.status_code == 302 and '/login' not in r.headers['Location']
    with full.session_transaction() as s:
        assert 'login_username' not in s


def test_the_name_is_escaped(full):
    r = _post(full, '"><script>x</script>', 'wrong')
    html = full.get(r.headers['Location'], environ_base=IP).get_data(as_text=True)
    assert '<script>x</script>' not in html


@pytest.fixture
def minimal_on(test_app, monkeypatch, tmp_path):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    (tmp_path / 'index.html').write_text('<!doctype html><head></head><div id="root"></div>')
    monkeypatch.setitem(test_app.config, 'SPA_DIR', str(tmp_path))


def test_minimal_stale_post_lands_on_the_form_marked_expired(test_app, minimal_on):
    c = test_app.test_client()
    r = c.post('/login?next=/x', data={'username': 'a', 'password': 'b'}, environ_base=IP)
    assert r.status_code == 302
    loc = r.headers['Location']
    assert '/login' in loc and 'expired=1' in loc and 'next=/x' in loc
    assert c.get(loc, environ_base=IP).status_code == 200
