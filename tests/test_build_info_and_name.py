"""App name is Luma everywhere users see it; /api/version reports the build's git SHA."""

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40
SHA2 = 'b' * 40


def test_index_title_and_apple_title():
    html = (ROOT / 'frontend' / 'index.html').read_text()
    assert '<title>Luma</title>' in html
    assert '<meta name="apple-mobile-web-app-title" content="Luma">' in html
    assert 'Media Kiosk' not in html


def test_manifest_names(test_app):
    data = test_app.test_client().get('/manifest.json').get_json()
    assert (data['name'], data['short_name']) == ('Luma', 'Luma')
    static = json.loads((ROOT / 'static' / 'manifest.json').read_text())
    assert (static['name'], static['short_name']) == ('Luma', 'Luma')


def test_login_page_title(test_app):
    html = test_app.test_client().get('/login').get_data(as_text=True)
    assert 'Media Kiosk' not in html
    assert 'apple-mobile-web-app-title" content="Luma"' in html


def test_no_user_visible_media_kiosk():
    for p in [ROOT / 'frontend' / 'index.html', ROOT / 'static' / 'manifest.json', ROOT / 'app' / 'auth_routes.py']:
        assert 'Media Kiosk' not in p.read_text(), p


# -- build SHA -----------------------------------------------------------------

def test_env_wins(tmp_path, monkeypatch):
    from app import build_info
    (tmp_path / 'GIT_SHA').write_text(SHA2)
    monkeypatch.setenv('LUMA_GIT_SHA', SHA)
    assert build_info.resolve(tmp_path) == SHA


def test_baked_file(tmp_path, monkeypatch):
    from app import build_info
    monkeypatch.delenv('LUMA_GIT_SHA', raising=False)
    (tmp_path / 'GIT_SHA').write_text(SHA2 + '\n')
    assert build_info.resolve(tmp_path) == SHA2
    (tmp_path / 'GIT_SHA').write_text('unknown\n')
    assert build_info.resolve(tmp_path) == 'unknown'


def test_git_dir_loose_and_packed(tmp_path, monkeypatch):
    from app import build_info
    monkeypatch.delenv('LUMA_GIT_SHA', raising=False)
    g = tmp_path / '.git'
    (g / 'refs' / 'heads').mkdir(parents=True)
    (g / 'HEAD').write_text('ref: refs/heads/main\n')
    (g / 'refs' / 'heads' / 'main').write_text(SHA + '\n')
    assert build_info.resolve(tmp_path) == SHA
    (g / 'refs' / 'heads' / 'main').unlink()
    (g / 'packed-refs').write_text(f'# pack-refs\n{SHA2} refs/heads/main\n')
    assert build_info.resolve(tmp_path) == SHA2
    (g / 'HEAD').write_text(SHA + '\n')  # detached
    assert build_info.resolve(tmp_path) == SHA


def test_worktree_gitdir_file(tmp_path, monkeypatch):
    from app import build_info
    monkeypatch.delenv('LUMA_GIT_SHA', raising=False)
    main = tmp_path / 'main.git'
    wt = main / 'worktrees' / 'w'
    wt.mkdir(parents=True)
    (main / 'refs' / 'heads').mkdir(parents=True)
    (main / 'refs' / 'heads' / 'feat').write_text(SHA + '\n')
    (wt / 'HEAD').write_text('ref: refs/heads/feat\n')
    (wt / 'commondir').write_text('../..\n')
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    (checkout / '.git').write_text(f'gitdir: {wt}\n')
    assert build_info.resolve(checkout) == SHA


def test_unknown_without_anything(tmp_path, monkeypatch):
    from app import build_info
    monkeypatch.delenv('LUMA_GIT_SHA', raising=False)
    assert build_info.resolve(tmp_path) == 'unknown'


def test_real_checkout_matches_git():
    from app import build_info
    try:
        expected = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    except Exception:
        pytest.skip('git not available')
    assert build_info._from_git(ROOT) == expected


def test_api_version_reports_sha_without_subprocess(test_app, monkeypatch):
    import app.main
    from app import build_info
    monkeypatch.setattr(build_info, '_cached', None)
    monkeypatch.setattr(app.main, '_app_version', None)
    monkeypatch.setenv('LUMA_GIT_SHA', SHA)
    with mock.patch('subprocess.check_output', side_effect=AssertionError('no subprocess')), \
            mock.patch('subprocess.run', side_effect=AssertionError('no subprocess')):
        c = test_app.test_client()
        for _ in range(3):
            body = c.get('/api/version').get_json()
            assert body['git'] == SHA and body['version'] == SHA[:7]
    monkeypatch.setattr(build_info, '_cached', None)
    monkeypatch.setattr(app.main, '_app_version', None)


def test_dockerfile_and_compose_pass_git_sha():
    docker = (ROOT / 'Dockerfile').read_text()
    assert 'ARG GIT_SHA=unknown' in docker and '/app/GIT_SHA' in docker
    compose = (ROOT / 'docker-compose.test.yml').read_text()
    assert 'GIT_SHA: ${GIT_SHA:-unknown}' in compose
    assert 'git rev-parse HEAD' in (ROOT / 'DEPLOY.md').read_text()
