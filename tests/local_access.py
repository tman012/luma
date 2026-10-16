"""Test helpers: a Flask test client that counts as a local (LAN) caller."""


def local_client(flask_app):
    """Owner exists, LAN needs no sign-in, requests come from 127.0.0.1."""
    import app.main
    import app.user_auth as user_auth
    user_auth.init_db()
    try:
        user_auth.create_user('owner', '1234', is_admin=True)
    except Exception:
        pass  # already there
    try:
        app.main.settings.update({'security': {'auth_required': 'disabled_local'}})
    except Exception:
        pass  # settings mocked by the caller
    client = flask_app.test_client()
    client.environ_base['REMOTE_ADDR'] = '127.0.0.1'
    return client


def api_key_settings(value='test_api_key'):
    """side_effect for a mocked settings.get: every key reads as *value*,
    except the access settings, which keep LAN access open."""
    def get(key, default=None):
        if key == 'security.auth_required':
            return 'disabled_local'
        if key.startswith(('security.', 'request_portal.', 'general.')):
            return default
        return value
    return get
