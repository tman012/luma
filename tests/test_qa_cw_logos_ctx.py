"""v344: scripts/qa_cw_logos.py runs its work in an app context (home_rails._img reads current_app.config) and runs
from a copy outside the app folder (--app-dir)."""
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def test_copied_script_builds_card_urls_in_an_app_context(tmp_path):
    dst = tmp_path / 'qa_cw_logos.py'
    shutil.copy(os.path.join(ROOT, 'scripts', 'qa_cw_logos.py'), dst)
    code = ("import sys; sys.argv=['x','--app-dir',%r]; sys.path.insert(0,%r); import qa_cw_logos as q\n"
            "with q._qa_app(None).app_context(): print(q.home_rails._img('/library/metadata/1/thumb/2', 300))"
            % (ROOT, str(tmp_path)))
    out = subprocess.run([sys.executable, '-c', code], cwd=str(tmp_path), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert '/api/img/plex?url=' in out.stdout


def test_main_wraps_the_run_in_an_app_context():
    src = open(os.path.join(ROOT, 'scripts', 'qa_cw_logos.py'), encoding='utf-8').read()
    assert '.app_context():' in src and 'return _run(args)' in src
