# Luma e2e (Playwright, Python)

Mocked upstreams, no TMDb key or Plex needed.

## Setup

    pip install playwright pytest pytest-asyncio
    python -m playwright install chromium

## Running

E2E tests are excluded from default `pytest` runs. To run them:

    pytest tests/e2e

Or with verbose output:

    pytest tests/e2e -v

### Manual test environment setup

If you need to run tests with custom mock servers:

    MOCK_SEED_ITEMS=10000 python tests/e2e/mock_upstreams.py 5399 &
    # Luma at /app paths (static_folder is hard-coded to /app/static); settings point at the mock
    DATABASE_PATH=/app/data/media.db SERVICE_API_KEY=qa-key LUMA_DISABLE_AUTOMATIONS=1 LUMA_DISABLE_ADB=1 \
      LUMA_E2E_BASE_URL=http://127.0.0.1:5399 LUMA_MOCK_URL=http://127.0.0.1:5499 \
      python tests/e2e/run_luma_mock.py &      # listens on 5399
    # create a login: python -c "from app import user_auth as u; u.init_db(); u.create_user('qa','1234',is_admin=True)"
    cd tests/e2e && pytest -q

Env: LUMA_E2E_BASE_URL (default: http://127.0.0.1:5199), LUMA_MOCK_URL (default: http://127.0.0.1:5299), LUMA_E2E_API_KEY, LUMA_E2E_USER/PIN, LUMA_E2E_SHOTS.
