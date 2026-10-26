"""iOS PWA resume fixes (port list item 9)."""

from pathlib import Path

from tests.frontend_ts import FRONTEND, needs_ts, run_ts


@needs_ts
def test_stale_lock_cleared_when_no_overlay():
    out = run_ts('''
import { clearStaleModalLock } from '@src/hooks/usePullToRefresh';
const { makeDoc } = require('@fake_dom');
const d = makeDoc({ rootAttrs: { 'data-modal-open': '' } });
d.body.style.overflow = 'hidden';
const cleared = clearStaleModalLock(d as any);
console.log(JSON.stringify({ cleared, attr: d.root.hasAttribute('data-modal-open'), body: d.body.style.overflow }));
''')
    assert out == {'cleared': True, 'attr': False, 'body': 'auto'}


@needs_ts
def test_lock_kept_while_overlay_open():
    out = run_ts('''
import { clearStaleModalLock } from '@src/hooks/usePullToRefresh';
const { makeDoc } = require('@fake_dom');
const d = makeDoc({ overlays: ['[data-sheet]'], rootAttrs: { 'data-modal-open': '' } });
d.body.style.overflow = 'hidden';
const cleared = clearStaleModalLock(d as any);
console.log(JSON.stringify({ cleared, attr: d.root.hasAttribute('data-modal-open'), body: d.body.style.overflow }));
''')
    assert out == {'cleared': False, 'attr': True, 'body': 'hidden'}


def test_detail_modals_use_large_viewport():
    css = (FRONTEND / 'src' / 'index.css').read_text()
    assert '@supports (height: 100lvh)' in css
    assert '[data-modal="MovieDetailModal"], [data-modal="TvDetailModal"]' in css


def test_resume_nudge_registered():
    main = (FRONTEND / 'src' / 'main.tsx').read_text()
    assert "addEventListener('visibilitychange'" in main
    assert '_appdiag' not in main
