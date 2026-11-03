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
class FakeImage {
  private s = ''; fetchPriority = ''; decoding = ''; onload: null | (() => void) = null; onerror: null | (() => void) = null;
  set src(v: string) { this.s = v; if (v) setTimeout(() => (broken.has(v) ? this.onerror?.() : this.onload?.()), 20); }
  get src() { return this.s; }
  removeAttribute() { this.s = ''; }
  decode = () => new Promise<void>((res, rej) => { const v = this.s; setTimeout(() => (broken.has(v) ? rej(new Error('x')) : res()), 20); });
}
Object.defineProperty(HTMLImageElement.prototype, 'complete', { configurable: true, get() { return true; } });
Object.defineProperty(HTMLImageElement.prototype, 'naturalWidth', { configurable: true, get() { return 300; } });
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
beforeEach(() => {
  _resetModalPrefetch(); _resetImageLoader(); _resetLocalArt(); broken.clear();
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
  setLocalArt('movie', 603, art);
  const row = document.createElement('button'); row.setAttribute('aria-label', 'The Matrix'); document.body.appendChild(row);
  void openWhenReady('movie', undefined, 603, row, () => {}, undefined, []);
  render(<MovieDetailModal tmdbId={603} onClose={() => {}} />);
  for (let i = 0; i < 60; i++) await act(async () => { await vi.advanceTimersByTimeAsync(50); });
}
const srcs = () => Array.from(document.querySelectorAll('img')).map((i) => i.getAttribute('src') || '');

describe('wl-images: Watchlist sheet opens on the local images', () => {
  it('parses the payload flag (only /api/wl-image/ URLs)', () => {
    expect(parseWlImages({ backdrop: LOCAL.backdrop, logo: null })).toEqual({ backdrop: LOCAL.backdrop, logo: null });
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
