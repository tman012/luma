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
import { parseWlImages, setLocalArt, _resetLocalArt, LOCAL_BACKDROP_WAIT_MS, WL_IMAGE_REV } from '@/services/localArt';
import { BACKDROP_ART_DEADLINE_MS } from '@/hooks/useSheetBackdrop';
import { SURFACE_DEADLINE_MS } from '@/loading/revealGate';

const broken = new Set<string>();
/** v350: per-URL load time (ms; default 20) and every URL an Image was asked to load. */
const slow = new Map<string, number>();
const requested: string[] = [];
const delay = (v: string) => { for (const [k2, ms] of slow) if (v.includes(k2)) return ms; return 20; };
const isBroken = (v: string) => { for (const b of broken) if (v.includes(b)) return true; return false; };
class FakeImage {
  private s = ''; fetchPriority = ''; decoding = ''; onload: null | (() => void) = null; onerror: null | (() => void) = null;
  set src(v: string) { this.s = v; if (v) { requested.push(v); setTimeout(() => (isBroken(v) ? this.onerror?.() : this.onload?.()), delay(v)); } }
  get src() { return this.s; }
  removeAttribute() { this.s = ''; }
  decode = () => new Promise<void>((res, rej) => { const v = this.s; setTimeout(() => (isBroken(v) ? rej(new Error('x')) : res()), delay(v)); });
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
      expect(localArt('movie', 777)).toMatchObject({ backdrop: expect.stringMatching(/\/api\/wl-image\/movie\/603\/backdrop\?r=2&w=1280$/),
        logo: expect.stringMatching(/\/logo\?r=2&w=300$/) });
      layoutMode.mode = 'compact';
      await openWl(LOCAL);
      expect(srcs().some((u) => u.includes('/api/wl-image/movie/603/backdrop?r=2&w=1280'))).toBe(true);
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
    expect(localBackdropFor('movie', 603)).toMatch(/\/api\/wl-image\/movie\/603\/backdrop\?r=2$/);
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
    expect(localArt('movie', 603)).toMatchObject({ backdrop: expect.stringMatching(/backdrop\?r=2$/), logo: null, backdropTextless: true });
  });
  it('deadlines stay 1.0s text and 1.6s art, and the local backdrop wait stays inside the art deadline', () => {
    expect(SURFACE_DEADLINE_MS).toBe(1000);
    expect(BACKDROP_ART_DEADLINE_MS).toBe(1600);
    expect(LOCAL_BACKDROP_WAIT_MS).toBe(700);
    expect(LOCAL_BACKDROP_WAIT_MS).toBeLessThan(BACKDROP_ART_DEADLINE_MS);
  });
  function setWidth(width: number, dpr = 1) {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
    Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: dpr });
  }
  it('below 1024, minimal or not, gets 1280 and 300 and never the unsized original', () => {
    const win = window as unknown as { __LUMA_MINIMAL__?: boolean };
    const was = win.__LUMA_MINIMAL__;
    const w = window.innerWidth;
    const dpr = window.devicePixelRatio;
    try {
      win.__LUMA_MINIMAL__ = false;
      setWidth(390, 3);
      setLocalArt('movie', 390, LOCAL);
      expect(localArt('movie', 390)).toMatchObject({
        backdrop: expect.stringMatching(/\/backdrop\?r=2&w=1280$/),
        logo: expect.stringMatching(/\/logo\?r=2&w=300$/),
      });
      setWidth(932, 3);   // a phone in landscape, still under the desktop breakpoint
      setLocalArt('movie', 932, LOCAL);
      expect(localArt('movie', 932)?.backdrop).toMatch(/\/backdrop\?r=2&w=1280$/);
      expect(localArt('movie', 932)?.backdrop).not.toMatch(/\/backdrop$/);
      win.__LUMA_MINIMAL__ = true;
      setWidth(800, 2);
      setLocalArt('movie', 800, LOCAL);
      expect(localArt('movie', 800)?.backdrop).toMatch(/\/backdrop\?r=2&w=1280$/);
    } finally {
      win.__LUMA_MINIMAL__ = was;
      setWidth(w, dpr);
    }
  });
  it('desktop, including high-DPI, requests the stored original with no width cap', () => {
    const win = window as unknown as { __LUMA_MINIMAL__?: boolean };
    const was = win.__LUMA_MINIMAL__;
    const w = window.innerWidth;
    const dpr = window.devicePixelRatio;
    try {
      win.__LUMA_MINIMAL__ = true;
      setWidth(1440, 2);
      setLocalArt('movie', 1440, LOCAL);
      expect(localArt('movie', 1440)).toMatchObject({
        backdrop: expect.stringMatching(/\/api\/wl-image\/movie\/603\/backdrop\?r=2$/),
        logo: expect.stringMatching(/\/api\/wl-image\/movie\/603\/logo\?r=2$/),
      });
      expect(localArt('movie', 1440)?.backdrop).not.toContain('w=1920');
      expect(localArt('movie', 1440)?.backdrop).not.toContain('w=1280');
    } finally {
      win.__LUMA_MINIMAL__ = was;
      setWidth(w, dpr);
    }
  });
  it('versioned URLs replace the immutable v348–v350 ones, on desktop and on a phone, including a warm-up', async () => {
    const legacy = {
      backdrop: '/api/wl-image/movie/603/backdrop',
      phone: '/api/wl-image/movie/603/backdrop?w=1280',
      logo: '/api/wl-image/movie/603/logo',
      logoPhone: '/api/wl-image/movie/603/logo?w=300',
    };
    expect(WL_IMAGE_REV).toBe('2');
    const w0 = window.innerWidth;
    const dpr = window.devicePixelRatio;
    try {
      setWidth(1440, 2);
      setLocalArt('movie', 603, LOCAL);   // payload is the old unversioned path
      const desk = localArt('movie', 603)!;
      expect(desk.backdrop).toBe(`${legacy.backdrop}?r=${WL_IMAGE_REV}`);
      expect(desk.logo).toBe(`${legacy.logo}?r=${WL_IMAGE_REV}`);
      expect(desk.backdrop).not.toBe(legacy.backdrop);
      expect(desk.logo).not.toBe(legacy.logo);
      expect(desk.backdrop).not.toContain('w=');
      const { noteLocalArt, localBackdropFor, localLogoFor } = await import('@/services/localArt');
      noteLocalArt('tv', 9, { backdrop: '/api/wl-image/tv/9/backdrop', logo: '/api/wl-image/tv/9/logo?r=1' });
      expect(localBackdropFor('tv', 9)).toBe('/api/wl-image/tv/9/backdrop?r=2');
      expect(localLogoFor('tv', 9)).toBe('/api/wl-image/tv/9/logo?r=2');
      expect(localBackdropFor('tv', 9)).not.toBe('/api/wl-image/tv/9/backdrop');
      setWidth(390, 3);
      setLocalArt('movie', 391, LOCAL);
      const phone = localArt('movie', 391)!;
      expect(phone.backdrop).toBe(`${legacy.backdrop}?r=${WL_IMAGE_REV}&w=1280`);
      expect(phone.logo).toBe(`${legacy.logo}?r=${WL_IMAGE_REV}&w=300`);
      expect(phone.backdrop).not.toBe(legacy.phone);
      expect(phone.logo).not.toBe(legacy.logoPhone);
      for (const u of requested) {
        if (!u.includes('/api/wl-image/')) continue;
        const path = u.replace(/^https?:\/\/[^/]+/, '');
        expect([legacy.backdrop, legacy.phone, legacy.logo, legacy.logoPhone]).not.toContain(path);
        expect(path).toContain(`r=${WL_IMAGE_REV}`);
      }
    } finally {
      setWidth(w0, dpr);
    }
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
