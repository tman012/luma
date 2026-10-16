import os, pytest

# Guard playwright import so default pytest collection doesn't fail
playwright = pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright

# No shared defaults (5199/5299 may be a live instance): both URLs are required.
from e2e_env import BASE, MOCK, SHOTS
if not BASE or not MOCK:
    pytest.exit("Set LUMA_E2E_BASE_URL and LUMA_MOCK_URL to a private Luma + mock on spare ports "
                "(see tests/e2e/README.md); refusing to default to shared 127.0.0.1:5199/5299.", returncode=4)


# Must run last: it leaves TMDb backed off for the mock's 30s Retry-After.
_RUN_LAST = ("test_tmdb_rate_limited_graceful",)


@pytest.hookimpl(trylast=True)  # after pytest-randomly's shuffle
def pytest_collection_modifyitems(session, config, items):
    items.sort(key=lambda it: it.originalname in _RUN_LAST if hasattr(it, "originalname") else False)
API_KEY = os.environ.get("LUMA_E2E_API_KEY", "qa-key")
USER, PIN = os.environ.get("LUMA_E2E_USER", "qa"), os.environ.get("LUMA_E2E_PIN", "1234")
# LUMA_E2E_SHOTS must be set in environment; no hard-coded default
if SHOTS:
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
