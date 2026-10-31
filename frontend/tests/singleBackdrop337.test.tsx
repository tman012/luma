import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { renderHook, act, cleanup, render } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { useSheetHero, BACKDROP_CAP_MS, HERO_FADE } from '@/hooks/useSheetBackdrop';
import type { BackdropEffects } from '@/hooks/useBackdropEffects';
import { BackdropHero } from '@/components/media/BackdropHero';

const fx = { headerImgStyle: {}, headerContainerMask: {} } as unknown as BackdropEffects;
const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
const decodes = new Map<string, () => void>();
const fails = new Map<string, () => void>();
class FakeImage {
  private s = '';
  onload: null | (() => void) = null; onerror: null | (() => void) = null;
  set src(v: string) { this.s = v; }
  get src() { return this.s; }
  decode = () => new Promise<void>((r, j) => { decodes.set(this.s, r); fails.set(this.s, j); });
}
beforeEach(() => { decodes.clear(); fails.clear(); vi.useFakeTimers(); vi.stubGlobal('Image', FakeImage); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const decode = async (u: string) => { await act(async () => { decodes.get(u)!(); await Promise.resolve(); await Promise.resolve(); }); };

type P = { c: string | null; s: boolean; k: string };
// No logo (settled): the hero is the backdrop alone; `.art` is the one backdrop.
const mount = (p: P) => renderHook(({ c, s, k }: P) => useSheetHero(c, s, null, true, k).art, { initialProps: p });

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
      expect(c).toMatch(/useSheetHero\(/);
      expect(c).not.toMatch(/SeededLogo|useLogoSwap/);
      expect(src(`components/media/${k}/ExpandedLayout.tsx`)).not.toMatch(/BackdropSwap/);
    }
    const { container } = render(<BackdropHero backdropUrl={null} posterUrl={null} effects={fx} phase="visible" instant />);
    expect(container.querySelectorAll('img')).toHaveLength(0);
    const r = render(<BackdropHero backdropUrl="/final.jpg" posterUrl={null} effects={fx} phase="visible" instant />);
    expect(r.container.querySelectorAll('img').length).toBe(1);
  });

  type Q = { a: string | null; l: string | null; ls: boolean; k: string };
  const hero = (p: Q) => renderHook(({ a, l, ls, k }: Q) => useSheetHero(a, true, l, ls, k), { initialProps: p });

  it('logo in time: backdrop and logo are decided and revealed together, as the logo', async () => {
    const h = hero({ a: '/art.jpg', l: '/logo.png', ls: true, k: 'a' });
    await decode('/art.jpg');
    expect(h.result.current.state).toBe('hold');
    await decode('/logo.png');
    expect(h.result.current).toMatchObject({ state: 'logo', art: '/art.jpg', logo: '/logo.png' });
  });

  it('logo late: the text title at the cap, and no later swap to the logo', async () => {
    const h = hero({ a: '/art.jpg', l: '/logo.png', ls: true, k: 'a' });
    await decode('/art.jpg');
    act(() => { vi.advanceTimersByTime(BACKDROP_CAP_MS); });
    expect(h.result.current).toMatchObject({ state: 'text', art: '/art.jpg', logo: null });
    expect(decodes.has('/logo.png')).toBe(true);
    h.rerender({ a: '/art.jpg', l: '/logo.png', ls: true, k: 'a' });
    expect(h.result.current.state).toBe('text');
    h.rerender({ a: '/art.jpg', l: '/logo2.png', ls: true, k: 'a' });
    act(() => { vi.advanceTimersByTime(3000); });
    expect(h.result.current).toMatchObject({ state: 'text', art: '/art.jpg', logo: null });
  });

  it('logo lookup still pending holds the hero (no text first) until the cap', async () => {
    const h = hero({ a: '/art.jpg', l: null, ls: false, k: 'a' });
    await decode('/art.jpg');
    expect(h.result.current.state).toBe('hold');
    h.rerender({ a: '/art.jpg', l: null, ls: true, k: 'a' });
    expect(h.result.current.state).toBe('text');
  });

  it('a single combined fade: the title box and the backdrop share the one soft fade', () => {
    expect(HERO_FADE).toBe('opacity 0.45s ease-out');
    for (const k of ['MovieDetailModal', 'TvDetailModal']) {
      const c = src(`components/media/${k}/CompactLayout.tsx`);
      expect(c).toMatch(/data-hero-title=\{sheetHero\.state\}[\s\S]{0,200}opacity: sheetHero\.state === 'hold' \? 0 : 1, transition: reducedMotion\(\) \? 'none' : HERO_FADE/);
      const e = src(`components/media/${k}/ExpandedLayout.tsx`);
      expect(e).toMatch(/revealStyle\(sheetHero\.state\), animation: HERO_ANIM/);
      expect(e).toMatch(/opacity: sheetHero\.state === 'hold' \? 0 : 1,\s*transition: `transform [^`]*\$\{HERO_FADE\}`/);
    }
    expect(src('components/media/BackdropHero.tsx')).toMatch(/transition: reducedMotion\(\) \? 'none' : 'opacity 0\.45s ease-out'/);
  });

  it('empty hero is charcoal APP_BG until its image has loaded', () => {
    const r = render(<BackdropHero backdropUrl={null} posterUrl={null} effects={fx} phase="visible" instant />);
    expect((r.container.firstChild as HTMLElement).style.background).toMatch(/rgb\(17, 19, 24\)|#111318/);
  });

  it('pending decode: cleanup clears the image src', () => {
    const h = mount({ c: '/x.jpg', s: true, k: 'a' });
    h.unmount();
    expect(decodes.has('/x.jpg')).toBe(true);
    expect(src('hooks/useSheetBackdrop.ts')).toMatch(/im\.src = ''/);
  });

  it('the seed shows the tapped year in the meta row; the other rows stay reserved', async () => {
    const { ReservedRows } = await import('@/components/media/sheetRows');
    const r = render(<ReservedRows actions={null} meta="2021" />);
    const m = r.container.querySelector('[data-seed-meta]') as HTMLElement;
    expect(m.textContent).toBe('2021');
    expect(m.style.opacity).toBe('');
    expect(r.container.querySelector('[data-box="overview"]')!.closest('div[style*="opacity: 0"]')).not.toBeNull();
  });

  it('logo decode error (404 / corrupt): the text title, decided before the reveal, no later swap', async () => {
    const h = hero({ a: '/art.jpg', l: '/broken-logo.png', ls: true, k: 'a' });
    await decode('/art.jpg');
    expect(h.result.current.state).toBe('hold');
    await act(async () => { fails.get('/broken-logo.png')!(); await Promise.resolve(); await Promise.resolve(); });
    expect(h.result.current).toMatchObject({ state: 'text', art: '/art.jpg', logo: null });
    act(() => { vi.advanceTimersByTime(5000); });
    expect(h.result.current.state).toBe('text');
  });

  it('a chosen logo that fails to paint falls back to the text title (onError), never back to the logo', async () => {
    const h = hero({ a: '/art.jpg', l: '/logo.png', ls: true, k: 'a' });
    await decode('/art.jpg'); await decode('/logo.png');
    expect(h.result.current.state).toBe('logo');
    act(() => { h.result.current.logoFailed(); });
    expect(h.result.current).toMatchObject({ state: 'text', logo: null });
    h.rerender({ a: '/art.jpg', l: '/logo.png', ls: true, k: 'a' });
    expect(h.result.current.state).toBe('text');
    for (const k of ['MovieDetailModal', 'TvDetailModal']) {
      expect(src(`components/media/${k}/CompactLayout.tsx`)).toMatch(/src=\{sheetHero\.logo\} onError=\{sheetHero\.logoFailed\}/);
      expect(src(`components/media/${k}/ExpandedLayout.tsx`)).toMatch(/onError=\{sheetHero\.logoFailed\}/);
    }
  });
});
