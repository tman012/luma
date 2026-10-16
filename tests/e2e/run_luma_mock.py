"""Launch Luma locally against mock upstreams (tests/e2e/mock_upstreams.py).

Rewrites any https://api.themoviedb.org/3 request to the mock, because
TMDBClient.BASE_URL is hard-coded. Writes a throwaway settings.json.
"""
import json, os, runpy, sys
from pathlib import Path
MOCK = os.environ.get("LUMA_MOCK_URL", "http://127.0.0.1:5299")
data = Path(os.environ["DATABASE_PATH"]).parent
data.mkdir(parents=True, exist_ok=True)
import requests
_orig = requests.Session.request
def _req(self, method, url, *a, **k):
    if isinstance(url, str) and url.startswith("https://api.themoviedb.org/3"):
        url = MOCK + "/tmdb" + url[len("https://api.themoviedb.org/3"):]
    return _orig(self, method, url, *a, **k)
requests.Session.request = _req
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.settings_manager import SettingsManager
s = SettingsManager()
ok, errs = s.update({"plex": {"url": MOCK, "token": "mock-token"},
                     "network": {"flask_port": int(os.environ.get("LUMA_E2E_PORT", "5199"))},
                     "apis": {"tmdb": {"api_key": "mock-key", "base_url": MOCK + "/tmdb"}}})
print("settings update:", ok, errs, flush=True)
runpy.run_module("app.main", run_name="__main__", alter_sys=True)
