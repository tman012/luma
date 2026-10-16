# Luma e2e (Playwright, Python)

Mocked upstreams, no TMDb key or Plex needed.

    pip install -r requirements.txt playwright pytest && python -m playwright install chromium
    MOCK_SEED_ITEMS=10000 python tests/e2e/mock_upstreams.py 5299 &
    # Luma at /app paths (static_folder is hard-coded to /app/static); settings point at the mock
    DATABASE_PATH=/app/data/media.db SERVICE_API_KEY=qa-key LUMA_DISABLE_AUTOMATIONS=1 LUMA_DISABLE_ADB=1 \
      python tests/e2e/run_luma_mock.py &      # listens on 5199
    # create a login: python -c "from app import user_auth as u; u.init_db(); u.create_user('qa','1234',is_admin=True)"
    cd tests/e2e && pytest -q

Env: LUMA_E2E_BASE_URL, LUMA_MOCK_URL, LUMA_E2E_API_KEY, LUMA_E2E_USER/PIN, LUMA_E2E_SHOTS, LUMA_E2E_APP_ROOT.
