import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { useOneReveal } from '@/hooks/useOneReveal';

const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
type P = { owned: boolean; known: boolean; art: boolean };
const mount = (p: P) => renderHook(({ owned, known, art }: P) => useOneReveal('a', true, true, true, true, false, owned, known, art), { initialProps: p });

describe('v341b: desktop one reveal and the button set', () => {
  it('ownership is decided before the reveal: the reveal waits for it (up to the backdrop deadline), then the set is frozen', () => {
    const h = mount({ owned: false, known: false, art: false });
    expect(h.result.current.revealed).toBe(false);          // a cold search open: ownership lookup still out
    h.rerender({ owned: true, known: true, art: false });
    expect(h.result.current).toEqual({ revealed: true, trailer: false, owned: true });
    h.rerender({ owned: false, known: true, art: true });
    expect(h.result.current.owned).toBe(true);              // frozen for the open: the Watch set never re-flows
    const g = mount({ owned: false, known: false, art: false });
    g.rerender({ owned: false, known: false, art: true });
    expect(g.result.current).toEqual({ revealed: true, trailer: false, owned: false });
    g.rerender({ owned: true, known: true, art: true });
    expect(g.result.current.owned).toBe(false);
  });

  it('movie and TV expanded layouts: backdrop, logo/title and the content block share the one gate and fade', () => {
    for (const k of ['MovieDetailModal', 'TvDetailModal']) {
      const e = src(`components/media/${k}/ExpandedLayout.tsx`);
      expect(e, k).toMatch(/const reveal = useOneReveal\(props\.openKey, !loading && !!(liveDetail|liveShow), sheetHero\.state !== 'hold'( && artInFrame)?, ratingsSettle\.shown,\s*sheetHero\.deadline/);
      expect(e, k).toMatch(/revealStyle\(reveal\.revealed \? sheetHero\.state : 'hold'\), animation: reveal\.revealed \? HERO_ANIM : 'none'/); // backdrop: decoded, mounted, fades at the reveal
      expect(e, k).toMatch(/opacity: sheetHero\.state === 'hold' \|\| !reveal\.revealed \? 0 : 1/);  // logo/title
      expect(e, k).toMatch(/\.\.\.\(loading \|\| error \|\| reveal\.revealed \? revealStyle\('text'\) : \{ opacity: 0, transition: 'none' \}\)/); // meta, ratings, pills
    }
    const m = src('components/media/MovieDetailModal/ExpandedLayout.tsx');
    expect(m).toMatch(/const isOwned = reveal\.revealed \? reveal\.owned : \(detail\?\.isOwned \?\? false\);/);
    expect(m).toMatch(/!!liveDetail\.ownershipSettled/);
    expect(src('hooks/useMovieDetail.ts')).toMatch(/ownershipSettled: true/);
  });
});
