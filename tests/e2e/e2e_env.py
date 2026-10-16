"""Shared e2e settings. Test modules import these from here, not from
conftest: a bare `import conftest` can resolve to the repo-root conftest."""
import os

BASE = os.environ.get("LUMA_E2E_BASE_URL")
MOCK = os.environ.get("LUMA_MOCK_URL")
# LUMA_E2E_SHOTS must be set in environment; no hard-coded default
SHOTS = os.environ.get("LUMA_E2E_SHOTS")
