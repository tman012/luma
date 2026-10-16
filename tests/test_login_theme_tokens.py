"""The server-rendered sign-in page mirrors the SPA theme tokens exactly."""

import re
from pathlib import Path

from app.auth_routes import LOGIN_PAGE_HTML

ROOT = Path(__file__).resolve().parent.parent


def _vars(css):
    return {m.group(1): re.sub(r"\s+", " ", m.group(2).strip())
            for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", css)}


def _spa_theme():
    css = (ROOT / 'frontend/src/index.css').read_text()
    block = re.search(r"@theme\s*\{(.*?)\n\}", css, re.S).group(1)
    return _vars(block), css


def _login_root():
    return _vars(re.search(r":root\s*\{(.*?)\}", LOGIN_PAGE_HTML, re.S).group(1))


def test_login_color_tokens_match_spa():
    spa, _ = _spa_theme()
    mine = _login_root()
    colors = {k: v for k, v in mine.items() if k.startswith('--color-')}
    assert colors, 'no color tokens found'
    for name, value in colors.items():
        assert name in spa, name
        assert value == spa[name], (name, value, spa[name])


def test_login_font_matches_spa_auth_pages():
    _, css = _spa_theme()
    page = re.search(r"\.login-page,[^{]*\{(.*?)\}", css, re.S).group(1)
    spa_font = re.search(r"font-family:\s*([^;]+);", page).group(1).strip()
    assert _login_root()['--font-luma'] == spa_font


def test_login_background_is_luma_purple():
    assert _login_root()['--color-kiosk-black'] == '#15102a'
    assert 'background: var(--color-kiosk-black)' in LOGIN_PAGE_HTML


def test_no_hard_coded_colors_outside_tokens():
    style = re.search(r"<style>(.*?)</style>", LOGIN_PAGE_HTML, re.S).group(1)
    rules = re.sub(r":root\s*\{.*?\}", "", style, count=1, flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", rules)


def test_uses_luma_logo_asset():
    assert "images/luma_text.webp" in LOGIN_PAGE_HTML
    assert (ROOT / 'static/images/luma_text.webp').exists()
