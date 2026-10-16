"""Unit-test sandbox: point every Luma data/cache path at a private temp dir.

Runs in pytest_configure, before any test module imports app.*, so unit tests
never read or write the host's /app/data, /app/cache or a LUMA_* dir that
happens to be set in the shell. Skipped when only tests/e2e is collected: e2e
talks to a separately started server and must see that server's paths.
"""
import os
import tempfile
from pathlib import Path

_E2E_DIR = (Path(__file__).parent / "e2e").resolve()


def _only_e2e(config) -> bool:
    base = Path(config.invocation_params.dir)
    paths = [(base / str(a).split("::")[0]).resolve() for a in config.args] or [base.resolve()]
    return all(p == _E2E_DIR or _E2E_DIR in p.parents for p in paths)


def pytest_configure(config):
    if _only_e2e(config):
        return
    root = Path(tempfile.mkdtemp(prefix="luma_unit_"))
    for sub in ("data", "cache", "cache/hero"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    os.environ["LUMA_DATA_DIR"] = str(root / "data")
    os.environ["LUMA_CACHE_DIR"] = str(root / "cache")
    os.environ["HERO_CACHE_DIR"] = str(root / "cache" / "hero")
    os.environ["DATABASE_PATH"] = str(root / "data" / "media.db")
    os.environ.setdefault("LUMA_DISABLE_AUTOMATIONS", "1")
    os.environ.setdefault("LUMA_DISABLE_ADB", "1")
