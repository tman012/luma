"""app.settings_read: settings are readable without importing app.main or starting anything."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_import_starts_no_threads_and_never_imports_main(tmp_path):
    p = tmp_path / 'settings.json'
    p.write_text(json.dumps({'qbittorrent': {'url': 'http://q:8080'}}))
    before = p.read_bytes()
    code = (
        'import sys, threading\n'
        'n = threading.active_count()\n'
        'from app import settings_read\n'
        f'v = settings_read.get("qbittorrent.url", settings_path={str(p)!r})\n'
        'assert v == "http://q:8080", v\n'
        'assert settings_read.get("nope.key", 7, settings_path=' + repr(str(p)) + ') == 7\n'
        'assert threading.active_count() == n, threading.enumerate()\n'
        'assert "app.main" not in sys.modules\n'
        'bad = [m for m in ("app.library_mover", "app.import_reconciler", "app.tv_downloads", "app.movie_import") if m in sys.modules]\n'
        'assert not bad, bad\n'
        'print("ok")\n')
    env = {**os.environ, 'LUMA_DATA_DIR': str(tmp_path / 'data')}
    r = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip() == 'ok', r.stderr
    assert p.read_bytes() == before and not (tmp_path / 'data').exists()     # nothing written or created


def test_missing_settings_read_as_empty(tmp_path):
    from app import settings_read
    assert settings_read.get('qbittorrent.url', 'd', settings_path=str(tmp_path / 'none.json')) == 'd'
