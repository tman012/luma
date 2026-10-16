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

**IMPORTANT**: Always use a private app directory and spare ports. **Never run against a shared /app** that other processes or users depend on.

If you need to run tests with custom mock servers:

    # Use private temp directories and spare ports (NOT 5199/5299)
    MOCK_SEED_ITEMS=10000 python tests/e2e/mock_upstreams.py 6399 &
    
    # Private app dir with spare port
    mkdir -p /tmp/luma-e2e-test/data /tmp/luma-e2e-test/static/hero_cache
    DATABASE_PATH=/tmp/luma-e2e-test/data/media.db \
      SERVICE_API_KEY=qa-key \
      LUMA_DISABLE_AUTOMATIONS=1 \
      LUMA_DISABLE_ADB=1 \
      LUMA_E2E_BASE_URL=http://127.0.0.1:6299 \
      LUMA_MOCK_URL=http://127.0.0.1:6399 \
      python tests/e2e/run_luma_mock.py &      # listens on 6299
    
    # create a login: 
    # DATABASE_PATH=/tmp/luma-e2e-test/data/media.db python -c "from app import user_auth as u; u.init_db(); u.create_user('qa','1234',is_admin=True)"
    
    cd tests/e2e && pytest -q

Env: LUMA_E2E_BASE_URL and LUMA_MOCK_URL are required (no defaults; the run exits if unset); run_luma_mock.py also requires LUMA_E2E_PORT; set HERO_CACHE_DIR to the same value for Luma and pytest; LUMA_E2E_API_KEY, LUMA_E2E_USER/PIN, LUMA_E2E_SHOTS.

**Never use port 5199 or shared /app during testing** - use private directories and spare ports to avoid conflicts.

### Serve this branch's SPA

Build the frontend into a private static dir and point Luma at it, so e2e
never runs against an old shared build:

    STATIC=$(mktemp -d)/static && mkdir -p $STATIC && cp -r static/. $STATIC/
    (cd frontend && npm ci && npm run build -- --outDir $STATIC/spa --emptyOutDir)
    export FLASK_STATIC_FOLDER=$STATIC
    export LUMA_DATA_DIR=$(mktemp -d) LUMA_CACHE_DIR=$(mktemp -d)   # hero images go to $LUMA_CACHE_DIR/hero
