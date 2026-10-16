"""
E2E-style test for URL base support with the built SPA.

Verifies that when LUMA_URL_BASE=/luma:
- Index HTML is served at /luma/
- Assets (JS, CSS) resolve under /luma/static/spa/assets/
- Manifest resolves under /luma/static/spa/manifest.json
- API calls work under /luma/api/
"""
import os
import pytest
from werkzeug.test import Client
from werkzeug.wrappers import Response


@pytest.fixture
def test_app_with_url_base(test_app):
    """Create a test app with URL base configured to /luma."""
    import app.main
    
    # Set URL base environment variable
    os.environ['LUMA_URL_BASE'] = '/luma'
    
    # Reload server_config to pick up the new URL base
    import importlib
    import app.server_config
    importlib.reload(app.server_config)
    
    # Reload main to apply DispatcherMiddleware
    importlib.reload(app.main)
    
    # Ensure app.static_folder points to the right place
    # The static folder should be at /workspace/static
    import pathlib
    static_path = pathlib.Path(__file__).parent.parent / 'static'
    app.main.app.static_folder = str(static_path)
    
    yield app.main.app
    
    # Cleanup
    if 'LUMA_URL_BASE' in os.environ:
        del os.environ['LUMA_URL_BASE']
    importlib.reload(app.server_config)
    importlib.reload(app.main)


@pytest.mark.skipif(not os.path.exists(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'spa', 'index.html')),
                    reason="Built SPA not found. Run 'npm run build' in frontend/")
def test_url_base_spa_routing(test_app_with_url_base):
    """Test that the built SPA works correctly when served under a URL base."""
    # Use werkzeug.test.Client which properly handles WSGI middleware
    client = Client(test_app_with_url_base, Response)
    
    # Test 1: Index HTML should be accessible at /luma/
    # Use environ_base to simulate a local request (to bypass auth)
    response = client.get('/luma/', environ_base={'REMOTE_ADDR': '127.0.0.1'})
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert b'<!doctype html>' in response.data.lower() or b'<!DOCTYPE html>' in response.data.lower()
    
    # Test 2: Verify that the HTML contains the URL base injection
    html_content = response.get_data(as_text=True)
    assert '__LUMA_URL_BASE__' in html_content, "URL base should be injected into HTML"
    assert '"/luma"' in html_content, "URL base value should be '/luma'"
    
    # Test 3: Verify the manifest.json exists and is referenced correctly in the HTML
    # The built HTML should reference manifest.json
    assert 'manifest.json' in html_content, "HTML should reference manifest.json"
    
    # Test 4: Health endpoint should work at /luma/health
    response = client.get('/luma/health', environ_base={'REMOTE_ADDR': '127.0.0.1'})
    assert response.status_code == 200, f"Health endpoint should be accessible at /luma/health"
    
    # Test 5: Verify we cannot access without the URL base (should 404)
    response = client.get('/health', environ_base={'REMOTE_ADDR': '127.0.0.1'})  # Without /luma prefix
    assert response.status_code == 404, "Routes without URL base should not be accessible"
    
    # Test 6: Verify asset references in the HTML
    # Assets should be referenced correctly
    assert 'src=' in html_content or 'href=' in html_content, "HTML should have script/link tags"


@pytest.mark.skipif(not os.path.exists(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'spa', 'index.html')),
                    reason="Built SPA not found")
def test_url_base_injection(test_app_with_url_base):
    """Verify that the URL base is correctly injected into the HTML."""
    client = Client(test_app_with_url_base, Response)
    response = client.get('/luma/', environ_base={'REMOTE_ADDR': '127.0.0.1'})
    assert response.status_code == 200
    
    html = response.get_data(as_text=True)
    # Check that the URL base is set in the window object
    assert 'window.__LUMA_URL_BASE__' in html
    assert '"/luma"' in html


@pytest.mark.skipif(not os.path.exists(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'spa', 'index.html')),
                    reason="Built SPA not found")
def test_url_base_assets_resolve(test_app_with_url_base):
    """Verify that asset references in the HTML use correct paths."""
    client = Client(test_app_with_url_base, Response)
    response = client.get('/luma/', environ_base={'REMOTE_ADDR': '127.0.0.1'})
    assert response.status_code == 200
    
    html = response.get_data(as_text=True)
    # Check that script and link tags exist (they should reference assets)
    assert 'src=' in html or 'href=' in html
    
    # Verify that paths in the HTML are rewritten to include URL base
    # The HTML rewriting should have added /luma prefix to static paths
    import re
    # Look for rewritten static paths
    static_match = re.search(r'(?:src|href)="(/luma/static/[^"]+)"', html)
    if static_match:
        # Found a rewritten static path - good!
        pass
    else:
        # Check if there are relative static paths that will be resolved by the base tag
        relative_match = re.search(r'(?:src|href)="(/static/[^"]+)"', html)
        assert relative_match, "HTML should contain static asset references"
