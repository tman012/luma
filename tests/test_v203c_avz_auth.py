from app import instance


def test_avistaz_token_login_is_not_an_arr_write():
    assert instance.write_kind('POST', 'https://privatehd.to/api/v1/jackett/auth') is None
    assert instance.write_kind('POST', 'http://radarr:7878/api/v3/movie') == 'arr'
    assert instance.write_kind('POST', 'http://x/api/v1/jackett/auth/extra') == 'arr'
