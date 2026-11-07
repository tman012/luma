import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { renderHook, act, cleanup } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { useSheetHero, BACKDROP_CAP_MS, BACKDROP_ART_DEADLINE_MS } from '@/hooks/useSheetBackdrop';

const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
const decodes = new Map<string, () => void>();
class FakeImage {
  private s = ''; onload: null | (() => void) = null; onerror: null | (() => void) = null;
  set src(v: string) { this.s = v; } get src() { return this.s; }
  decode = () => new Promise<void>((r) => { decodes.set(this.s, r); });
}
beforeEach(() => { decodes.clear(); vi.useFakeTimers(); vi.stubGlobal('Image', FakeImage); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const decode = async (u: string) => { await act(async () => { decodes.get(u)!(); await Promise.resolve(); await Promise.resolve(); }); };
type P = { a: string | null; s: boolean; p: boolean };
// TV: art = the Plex art while TMDb is pending (backdropPending: not settled), the details present.
const mount = (x: P) => renderHook(({ a, s, p }: P) => useSheetHero(a, s, null, true, 'k', p)  /* v345: no logo for the title (a pending lookup holds to the art deadline) */, { initialProps: x });
const past = () => { act(() => { vi.advanceTimersByTime(BACKDROP_CAP_MS); }); act(() => { vi.advanceTimersByTime(BACKDROP_ART_DEADLINE_MS - BACKDROP_CAP_MS); }); };

describe('v341c: the TV hero is capped while TMDb is pending', () => {
  it('TMDb still pending past the old deadline: decoded Plex art does not latch, so a later settled backdrop is the first frame', async () => {
    const h = mount({ a: '/plex-art.jpg', s: false, p: true });
    await decode('/plex-art.jpg');
    act(() => { vi.advanceTimersByTime(BACKDROP_ART_DEADLINE_MS); });
    expect(h.result.current.state).toBe('hold');
    h.rerender({ a: '/tmdb-textless.jpg', s: true, p: true });
    expect(h.result.current.state).toBe('hold');
    await decode('/tmdb-textless.jpg');
    expect(h.result.current).toMatchObject({ state: 'text', art: '/tmdb-textless.jpg' });
  });

  it('no settled art by the old cap stays held (the open bound closes it; the hook does not invent a title)', () => {
    const h = mount({ a: '/plex-art.jpg', s: false, p: true });
    past();
    expect(h.result.current.state).toBe('hold');
    const g = mount({ a: null, s: false, p: true });
    past();
    expect(g.result.current.state).toBe('hold');
  });

  it('a TMDb error reveals on every layout: Plex fallback in the hook, error state shown on desktop', () => {
    const tv = src('hooks/useTvDetail.ts');
    expect(tv).toMatch(/A TMDb network error falls back to the Plex details/);
    expect(tv).toMatch(/if \(tmdbResp\?\.ok\)/);
    for (const k of ['MovieDetailModal', 'TvDetailModal']) {
      expect(src(`components/media/${k}/ExpandedLayout.tsx`), k).toMatch(/\.\.\.\(\(loading && !\w+\) \|\| error \? revealStyle\('text'\) : reveal\.revealed/);
    }
    for (const k of ['CompactLayout', 'ExpandedLayout']) {
      expect(src(`components/media/TvDetailModal/${k}.tsx`), k).toMatch(/!!liveShow && !liveShow\.backdropPending, [^\n]*props\.openKey, !!liveShow\);/);
    }
  });
});
