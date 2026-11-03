/** wl-images (trial): a Watchlist open uses the server-stored backdrop and logo; a miss or a load error falls back
 *  to the usual TMDb path (logo OR text, one reveal unchanged). */
import { act, render, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const layoutMode = vi.hoisted(() => ({ mode: 'compact' as 'compact' | 'expanded' }));
vi.mock('@/contexts/LayoutModeContext', async (orig) => ({ ...(await orig<typeof import('@/contexts/LayoutModeContext')>()),
  useLayoutMode: () => ({ mode: layoutMode.mode, isCompact: layoutMode.mode === 'compact', isExpanded: layoutMode.mode === 'expanded', layoutMode: layoutMode.mode }) }));
vi.mock('@/contexts/AuthContext', async (orig) => ({ ...(await orig<typeof import('@/contexts/AuthContext')>()), useAuth: () => ({ implicit: false, authenticated: true, user: { id: 1, username: 't', isAdmin: true }, isAdmin: true }) }));
vi.mock('@/contexts/ModalHostContext', async (orig) => ({ ...(await orig<typeof import('@/contexts/ModalHostContext')>()), useModalHost: () => ({ openMovie: vi.fn(), openTv: vi.fn(), openPerson: vi.fn(), registerBackHandler: vi.fn(() => () => {}), closeAll: vi.fn() }) }));
import { MovieDetailModal } from '@/components/media/MovieDetailModal';
import { openWhenReady, _resetModalPrefetch } from '@/services/modalPrefetch';
import { _resetImageLoader } from '@/loading/imageLoader';
import { parseWlImages, setLocalArt, _resetLocalArt } from '@/services/localArt';

const broken = new Set<string>();
/** v350: per-URL load time (ms; default 20) and every URL an Image was asked to load. */
const slow = new Map<string, number>();
const requested: string[] = [];
const delay = (v: string) => { for (const [k2, ms] of slow) if (v.includes(k2)) return ms; return 20; };
class FakeImage {
  private s = ''; fetchPriority = ''; decoding = ''; onload: null | (() => void) = null; onerror: null | (() => void) = null;
  set src(v: string) { this.s = v; if (v) { requested.push(v); setTimeout(() => (broken.has(v) ? this.onerror?.() : this.onload?.()), delay(v)); } }
  get src() { return this.s; }
  removeAttribute() { this.s = ''; }
  decode = () => new Promise<void>((res, rej) => { const v = this.s; setTimeout(() => (broken.has(v) ? rej(new Error('x')) : res()), delay(v)); });
}
Object.defineProperty(HTMLImageElement.prototype, 'complete', { configurable: true, get() { return true; } });
Object.defineProperty(HTMLImageElement.prototype, 'naturalWidth', { configurable: true, get() { return 300; } });
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
beforeEach(() => {
  _resetModalPrefetch(); _resetImageLoader(); _resetLocalArt(); broken.clear(); slow.clear(); requested.length = 0;
  vi.useFakeTimers(); vi.stubGlobal('Image', FakeImage);
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    const url = String(u);
    if (url.includes('/api/clearlogo/')) return json({ clearlogo_url: 'https://image.tmdb.org/t/p/original/tmdb-logo.png' });
    if (url.includes('/api/movie/603')) return json({ id: 603, title: 'The Matrix', backdrop_path: '/tmdb-bd.jpg', videos: { results: [] } });
    return json({});
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

const LOCAL = { backdrop: '/api/wl-image/movie/603/backdrop', logo: '/api/wl-image/movie/603/logo' };
async function openWl(art: typeof LOCAL | null) {
  atReveal = null;
  setLocalArt('movie', 603, art);
  const row = document.createElement('button'); row.setAttribute('aria-label', 'The Matrix'); document.body.appendChild(row);
  void openWhenReady('movie', undefined, 603, row, () => {}, undefined, []);
  render(<MovieDetailModal tmdbId={603} onClose={() => {}} />);
  for (let i = 0; i < 80; i++) {
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    const st = document.querySelector('[data-hero-title]')?.getAttribute('data-hero-title')
      ?? ((document.querySelector('[data-reveal]') as HTMLElement | null)?.style.opacity === '1' ? 'shown' : null);
    if (st && st !== 'hold' && atReveal === null) atReveal = srcs().filter((u) => /backdrop|tmdb-bd/.test(u));
  }
}
let atReveal: string[] | null = null;
const srcs = () => Array.from(document.querySelectorAll('img')).map((i) => i.getAttribute('src') || '');

describe('wl-images: Watchlist sheet opens on the local images', () => {
  it('parses the payload flag (only /api/wl-image/ URLs)', () => {
    expect(parseWlImages({ backdrop: LOCAL.backdrop, logo: null, backdrop_textless: true })).toEqual({ backdrop: LOCAL.backdrop, logo: null, backdropTextless: true });
    expect(parseWlImages({ backdrop: null, logo: null })).toBeNull();
    expect(parseWlImages({ backdrop: 'https://evil/x.jpg' })).toBeNull();
    expect(parseWlImages(undefined)).toBeNull();
  });
  for (const mode of ['compact', 'expanded'] as const) {
    it(`${mode}: present -> the local backdrop and logo`, async () => {
      layoutMode.mode = mode;
      await openWl(LOCAL);
      const s = srcs();
      expect(s.some((u) => u.includes('/api/wl-image/movie/603/backdrop'))).toBe(true);
      expect(s.some((u) => u.includes('/api/wl-image/movie/603/logo'))).toBe(true);
      expect(s.some((u) => u.includes('tmdb-bd.jpg'))).toBe(false);
    });
    it(`${mode}: local images fail to load -> the TMDb backdrop and logo`, async () => {
      layoutMode.mode = mode;
      broken.add(LOCAL.backdrop); broken.add(LOCAL.logo);
      await openWl(LOCAL);
      const s = srcs();
      expect(s.some((u) => u.includes('/api/wl-image/'))).toBe(false);
      expect(s.some((u) => u.includes('tmdb-bd.jpg'))).toBe(true);
      expect(s.some((u) => u.includes('tmdb-logo.png'))).toBe(true);
    });
    it(`${mode}: not stored (miss) -> the usual path`, async () => {
      layoutMode.mode = mode;
      await openWl(null);
      const s = srcs();
      expect(s.some((u) => u.includes('/api/wl-image/'))).toBe(false);
      expect(s.some((u) => u.includes('tmdb-bd.jpg'))).toBe(true);
    });
  }
});

import { withLocalArt, localArt } from '@/services/localArt';
describe('v348 (Designer): local art rules', () => {
  const art = { backdrop: '/api/wl-image/tv/5/backdrop', logo: '/api/wl-image/tv/5/logo', backdropTextless: true };
  it('owned or Plex art: the backdrop stays Plex (only the logo is local)', () => {
    expect(withLocalArt({ backdropUrl: '/plex.jpg', plexArtUrl: '/plex.jpg', clearLogoUrl: null }, art))
      .toMatchObject({ backdropUrl: '/plex.jpg', clearLogoUrl: art.logo, logoSettled: true });
    expect(withLocalArt({ backdropUrl: '/plex.jpg', isOwned: true }, art)!.backdropUrl).toBe('/plex.jpg');
  });
  it('a textless local backdrop fills the textless field (TV); one with text never replaces textless art', () => {
    expect(withLocalArt({ backdropUrl: '/t.jpg', backdropTextlessUrl: '/tl.jpg' }, art))
      .toMatchObject({ backdropUrl: '/t.jpg', backdropTextlessUrl: art.backdrop });
    expect(withLocalArt({ backdropUrl: '/t.jpg', backdropTextlessUrl: '/tl.jpg' }, { ...art, backdropTextless: false }))
      .toMatchObject({ backdropUrl: '/t.jpg', backdropTextlessUrl: '/tl.jpg' });
    expect(withLocalArt({ backdropUrl: '/t.jpg' }, { ...art, backdropTextless: false })!.backdropUrl).toBe(art.backdrop);
    expect(withLocalArt({ backdropUrl: '/m.jpg' }, art)!.backdropUrl).toBe(art.backdrop);   // movie: the backdrop field
  });
  it('v349 phones (minimal) get the server\'s phone copies: ?w=1280 backdrop, ?w=300 logo, in the sheet', async () => {
    const win = window as unknown as { __LUMA_MINIMAL__?: boolean };
    const was = win.__LUMA_MINIMAL__; win.__LUMA_MINIMAL__ = true;
    const w = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
    try {
      setLocalArt('movie', 777, LOCAL);
      expect(localArt('movie', 777)).toMatchObject({ backdrop: expect.stringMatching(/\/api\/wl-image\/movie\/603\/backdrop\?w=1280$/),
        logo: expect.stringMatching(/\/logo\?w=300$/) });
      layoutMode.mode = 'compact';
      await openWl(LOCAL);
      expect(srcs().some((u) => u.includes('/api/wl-image/movie/603/backdrop?w=1280'))).toBe(true);
      expect(srcs().some((u) => u.includes('tmdb-bd.jpg'))).toBe(false);
    } finally {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: w });
      win.__LUMA_MINIMAL__ = was;
    }
  });
  for (const mode of ['compact', 'expanded'] as const) {
    it(`v350 ${mode}: stored art present -> no TMDb backdrop (or logo) is ever requested`, async () => {
      layoutMode.mode = mode;
      await openWl(LOCAL);
      expect(requested.some((u) => u.includes('/api/wl-image/movie/603/backdrop'))).toBe(true);
      expect(requested.filter((u) => u.includes('tmdb-bd'))).toEqual([]);
      expect(requested.filter((u) => u.includes('tmdb-logo'))).toEqual([]);
    });
    it(`v350 ${mode}: the stored backdrop decodes late -> dropped before the reveal; TMDb's is there at the reveal, no late swap`, async () => {
      layoutMode.mode = mode;
      slow.set('/api/wl-image/movie/603/backdrop', 2500);
      await openWl(LOCAL);
      expect(atReveal, 'revealed').not.toBeNull();
      expect(atReveal!.some((u) => u.includes('tmdb-bd'))).toBe(true);
      expect(atReveal!.some((u) => u.includes('/api/wl-image/movie/603/backdrop'))).toBe(false);
      const end = srcs();
      expect(end.some((u) => u.includes('tmdb-bd'))).toBe(true);
      expect(end.some((u) => u.includes('/api/wl-image/movie/603/backdrop')), 'never swapped in after').toBe(false);
    });
    it(`v350 ${mode}: the stored backdrop within the wait -> held for it, on screen at the reveal`, async () => {
      layoutMode.mode = mode;
      slow.set('/api/wl-image/movie/603/backdrop', 500);
      await openWl(LOCAL);
      expect(atReveal!.some((u) => u.includes('/api/wl-image/movie/603/backdrop'))).toBe(true);
      expect(requested.filter((u) => u.includes('tmdb-bd'))).toEqual([]);
    });
  }
  it('v350 the Home payload notes stored art: a warm-up before any tap preloads it, not TMDb\'s', async () => {
    const { noteLocalArt, localBackdropFor } = await import('@/services/localArt');
    noteLocalArt('movie', 603, LOCAL);
    expect(localBackdropFor('movie', 603)).toMatch(/\/api\/wl-image\/movie\/603\/backdrop$/);
    const { warmDetail } = await import('@/services/modalPrefetch');
    void warmDetail('movie', undefined, 603);
    for (let i = 0; i < 20; i++) await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(requested.some((u) => u.includes('/api/wl-image/movie/603/backdrop'))).toBe(true);
    expect(requested.filter((u) => u.includes('tmdb-bd'))).toEqual([]);
  });
  it('v349 minimal Home Watchlist rail payload: wl_images parses for the open; a desktop-width minimal open stays full size', () => {
    const card = { key: 'watchlist-603', type: 'movie', tmdb_id: 603,
      wl_images: { backdrop: '/api/wl-image/movie/603/backdrop', logo: null, backdrop_textless: true } };
    setLocalArt('movie', 603, parseWlImages(card.wl_images));
    expect(localArt('movie', 603)).toMatchObject({ backdrop: expect.stringMatching(/backdrop$/), logo: null, backdropTextless: true });
  });
  it('the same title from another entry point after the Watchlist sheet closed: its usual art', async () => {
    layoutMode.mode = 'expanded';
    await openWl(LOCAL);
    expect(srcs().some((u) => u.includes('/api/wl-image/'))).toBe(true);
    cleanup(); _resetModalPrefetch();
    expect(localArt('movie', 603)).toBeNull();
    await openWl(null);   // e.g. search: nothing registered by this open
    expect(srcs().some((u) => u.includes('/api/wl-image/'))).toBe(false);
    expect(srcs().some((u) => u.includes('tmdb-bd.jpg'))).toBe(true);
  });
});
