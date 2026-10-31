import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { renderHook, act, cleanup, render } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { useSheetBackdrop, BACKDROP_CAP_MS } from '@/hooks/useSheetBackdrop';
import type { BackdropEffects } from '@/hooks/useBackdropEffects';
import { BackdropHero } from '@/components/media/BackdropHero';

const fx = { headerImgStyle: {}, headerContainerMask: {} } as unknown as BackdropEffects;
const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
const decodes = new Map<string, () => void>();
class FakeImage {
  private s = '';
  onload: null | (() => void) = null; onerror: null | (() => void) = null;
  set src(v: string) { this.s = v; }
  get src() { return this.s; }
  decode = () => new Promise<void>(r => { decodes.set(this.s, r); });
}
beforeEach(() => { decodes.clear(); vi.useFakeTimers(); vi.stubGlobal('Image', FakeImage); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const decode = async (u: string) => { await act(async () => { decodes.get(u)!(); await Promise.resolve(); await Promise.resolve(); }); };

type P = { c: string | null; s: boolean; k: string };
const mount = (p: P) => renderHook(({ c, s, k }: P) => useSheetBackdrop(c, s, k), { initialProps: p });

describe('one backdrop per sheet open', () => {
  it('single reveal: nothing until the final image decodes, then that image, and it never changes', async () => {
    const h = mount({ c: '/final-1280.jpg', s: true, k: 'a' });
    expect(h.result.current).toBeNull();
    await decode('/final-1280.jpg');
    expect(h.result.current).toBe('/final-1280.jpg');
    h.rerender({ c: '/other.jpg', s: true, k: 'a' });
    act(() => { vi.advanceTimersByTime(5000); });
    expect(h.result.current).toBe('/final-1280.jpg');
  });

  it('waits for a better pick still on its way (not settled) until it arrives', async () => {
    const h = mount({ c: '/plex-art.jpg', s: false, k: 'a' });
    await decode('/plex-art.jpg');
    expect(h.result.current).toBeNull();
    h.rerender({ c: '/tmdb-textless.jpg', s: true, k: 'a' });
    expect(h.result.current).toBeNull();
    await decode('/tmdb-textless.jpg');
    expect(h.result.current).toBe('/tmdb-textless.jpg');
  });

  it('cap fallback: at 1.2s the best available image shows, and nothing swaps afterward', async () => {
    const h = mount({ c: '/slow.jpg', s: true, k: 'a' });
    act(() => { vi.advanceTimersByTime(BACKDROP_CAP_MS - 1); });
    expect(h.result.current).toBeNull();
    act(() => { vi.advanceTimersByTime(1); });
    expect(h.result.current).toBe('/slow.jpg');
    h.rerender({ c: '/later-better.jpg', s: true, k: 'a' });
    expect(h.result.current).toBe('/slow.jpg');
    expect(decodes.has('/later-better.jpg')).toBe(false);
  });

  it('cap with nothing known yet: the first image that arrives is the only one', async () => {
    const h = mount({ c: null, s: false, k: 'a' });
    act(() => { vi.advanceTimersByTime(BACKDROP_CAP_MS); });
    h.rerender({ c: '/first.jpg', s: false, k: 'a' });
    expect(h.result.current).toBe('/first.jpg');
    h.rerender({ c: '/second.jpg', s: true, k: 'a' });
    expect(h.result.current).toBe('/first.jpg');
  });

  it('a new open (another title) starts fresh', async () => {
    const h = mount({ c: '/a.jpg', s: true, k: 'a' });
    await decode('/a.jpg');
    h.rerender({ c: '/b.jpg', s: true, k: 'b' });
    expect(h.result.current).toBeNull();
    await decode('/b.jpg');
    expect(h.result.current).toBe('/b.jpg');
  });

  it('preload at tap: the warm-up started by the tap downloads the same final image', () => {
    const s = src('services/modalPrefetch.ts');
    expect(s).toMatch(/preloadImage\(finalBackdropUrl\(path \? tmdbBackdrop\(path\) : plexBackdrop\)\)/);
    expect(s).toMatch(/tapAt = now\(\);[\s\S]*warmDetail\(kind, ratingKey, tmdbId\)/);
  });

  it('no seed frame: the sheets never hand the tapped poster or a second layer to the backdrop', () => {
    for (const k of ['MovieDetailModal', 'TvDetailModal']) {
      const c = src(`components/media/${k}/CompactLayout.tsx`);
      expect(c).not.toMatch(/backdropUrl=\{hero\.art\}|hero\.seeded \? hero\.art/);
      expect(c).not.toMatch(/upgradeUrl=/);
      expect(c).toMatch(/useSheetBackdrop\(/);
      expect(src(`components/media/${k}/ExpandedLayout.tsx`)).not.toMatch(/BackdropSwap/);
    }
    const { container } = render(<BackdropHero backdropUrl={null} posterUrl={null} effects={fx} phase="visible" instant />);
    expect(container.querySelectorAll('img')).toHaveLength(0);
    const r = render(<BackdropHero backdropUrl="/final.jpg" posterUrl={null} effects={fx} phase="visible" instant />);
    expect(r.container.querySelectorAll('img').length).toBe(1);
  });
});
