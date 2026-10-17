import { readFileSync } from 'fs';
import { resolve } from 'path';
import { describe, expect, it } from 'vitest';

const read = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');

describe('detail popup is display-only in minimal mode', () => {
  it('hides every marked action area under html.luma-minimal', () => {
    expect(read('index.css')).toMatch(/html\.luma-minimal \[data-modal-actions\]\s*\{\s*display: none !important;/);
  });
  it('marks the play/request/watchlist/sources controls in each layout', () => {
    for (const f of ['components/media/MovieDetailModal/CompactLayout.tsx', 'components/media/MovieDetailModal/ExpandedLayout.tsx',
      'components/media/TvDetailModal/CompactLayout.tsx', 'components/media/TvDetailModal/ExpandedLayout.tsx']) {
      expect(read(f)).toContain('data-modal-actions');
    }
  });
  it('mounts main\'s popup host in the minimal app', () => {
    const app = read('App.tsx');
    const minimal = app.slice(app.indexOf('function MinimalApp'), app.indexOf('export default function App'));
    expect(minimal).toContain('<AppModalHost>');
    expect(app).toContain("classList.add('luma-minimal')");
  });
});
