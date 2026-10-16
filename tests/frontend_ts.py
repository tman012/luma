"""Run small TypeScript snippets against the SPA sources (esbuild + node)."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / 'frontend'
ESBUILD = FRONTEND / 'node_modules' / '.bin' / 'esbuild'
NODE = shutil.which('node')

needs_ts = pytest.mark.skipif(NODE is None or not ESBUILD.exists(),
                              reason='node and frontend/node_modules (npm ci) are needed')


def run_ts(code: str):
    """Bundle *code* (may import from '@src/...') and return the JSON it prints."""
    with tempfile.TemporaryDirectory() as tmp:
        entry = Path(tmp) / 'entry.ts'
        entry.write_text(code.replace('@src/', str(FRONTEND / 'src') + '/')
                         .replace('@fake_dom', str(ROOT / 'tests' / 'js' / 'fake_dom.js')))
        out = Path(tmp) / 'out.cjs'
        subprocess.run([str(ESBUILD), str(entry), '--bundle', '--platform=node', '--format=cjs',
                        '--log-level=error', '--loader:.svg=text', f'--outfile={out}'],
                       check=True, cwd=str(FRONTEND))
        res = subprocess.run([NODE, str(out)], capture_output=True, text=True, timeout=60)
        assert res.returncode == 0, res.stderr
        return json.loads(res.stdout.strip().splitlines()[-1])
