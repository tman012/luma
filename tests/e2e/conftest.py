import os, pytest

# Guard playwright import so default pytest collection doesn't fail
playwright = pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright

BASE = os.environ.get("LUMA_E2E_BASE_URL", "http://127.0.0.1:5199")
MOCK = os.environ.get("LUMA_MOCK_URL", "http://127.0.0.1:5299")
API_KEY = os.environ.get("LUMA_E2E_API_KEY", "qa-key")
USER, PIN = os.environ.get("LUMA_E2E_USER", "qa"), os.environ.get("LUMA_E2E_PIN", "1234")
SHOTS = os.environ.get("LUMA_E2E_SHOTS", "/workspace/luma-qa/pr3")
os.makedirs(SHOTS, exist_ok=True)

@pytest.fixture(scope="session")
def pw():
    with sync_playwright() as p:
        yield p

@pytest.fixture(scope="session")
def browser(pw):
    b = pw.chromium.launch(args=["--no-proxy-server"])
    yield b
    b.close()

@pytest.fixture(scope="session")
def api(pw):
    ctx = pw.request.new_context(base_url=BASE, extra_http_headers={"X-API-Key": API_KEY})
    yield ctx
    ctx.dispose()

@pytest.fixture(scope="session")
def mock(pw):
    ctx = pw.request.new_context(base_url=MOCK)
    yield ctx
    ctx.dispose()

VIEWPORTS = {"phone": {"width": 390, "height": 844}, "desktop": {"width": 1440, "height": 900}}

@pytest.fixture(params=list(VIEWPORTS))
def page(request, browser):
    name = request.param
    ctx = browser.new_context(viewport=VIEWPORTS[name], is_mobile=(name == "phone"),
                              has_touch=(name == "phone"))
    pg = ctx.new_page()
    r = pg.request.post(f"{BASE}/api/auth/login", data={"name": USER, "pin": PIN})
    assert r.ok, r.text()
    pg.viewport_name = name
    yield pg
    ctx.close()
