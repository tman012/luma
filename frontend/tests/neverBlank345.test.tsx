/** v345 prod port: the logo lookup and its high-priority load start at the tap (never behind the sheet hold); a logo
 *  known by the 1.6s art deadline is shown (no swap); and on all four layouts the sheet shows the logo or the real
 *  text title, never neither (logo fail, stall, no logo, empty seed, slow details). */
import { act, render, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const layoutMode = vi.hoisted(() => ({ mode: 'compact' as 'compact' | 'expanded' }));
vi.mock('@/contexts/LayoutModeContext', async (orig) => ({ ...(await orig<typeof import('@/contexts/LayoutModeContext')>()),
  useLayoutMode: () => ({ mode: layoutMode.mode, isCompact: layoutMode.mode === 'compact', isExpanded: layoutMode.mode === 'expanded', layoutMode: layoutMode.mode }) }));
vi.mock('@/contexts/AuthContext', async (orig) => ({ ...(await orig<typeof import('@/contexts/AuthContext')>()), useAuth: () => ({ implicit: false, authenticated: true, user: { id: 1, username: 't', isAdmin: true }, isAdmin: true }) }));
vi.mock('@/contexts/ModalHostContext', async (orig) => ({ ...(await orig<typeof import('@/contexts/ModalHostContext')>()), useModalHost: () => ({ openMovie: vi.fn(), openTv: vi.fn(), openPerson: vi.fn(), registerBackHandler: vi.fn(() => () => {}), closeAll: vi.fn() }) }));
import { MovieDetailModal } from '@/components/media/MovieDetailModal';
import { TvDetailModal } from '@/components/media/TvDetailModal';
import { openWhenReady, _resetModalPrefetch } from '@/services/modalPrefetch';
import { _resetImageLoader } from '@/loading/imageLoader';

type ImgMode = 'ok' | 'fail' | 'stall';
const cfg = { img: 'ok' as ImgMode, domStall: false, logo: true, logoMs: 0, detailMs: 0, artMs: 30 };
const isLogo = (u: string) => u.includes('logo.png');
const made: { src: string; pri: string; at: number }[] = [];
let t0 = 0;
class FakeImage {
  private s = ''; fetchPriority = ''; decoding = ''; onload: null | (() => void) = null; onerror: null | (() => void) = null;
  set src(v: string) {
    this.s = v; if (!v) return;
    made.push({ src: v, pri: this.fetchPriority, at: Date.now() - t0 });
    const m = isLogo(v) ? cfg.img : 'ok';
    if (m === 'ok') setTimeout(() => this.onload?.(), 30); else if (m === 'fail') setTimeout(() => this.onerror?.(), 30);
  }
  get src() { return this.s; }
  removeAttribute() { this.s = ''; }
  decode = () => new Promise<void>((res, rej) => {
    const m = isLogo(this.s) ? cfg.img : 'ok';
    if (m === 'ok') setTimeout(res, isLogo(this.s) ? 30 : cfg.artMs); else if (m === 'fail') setTimeout(() => rej(new Error('broken')), 30);
  });
  naturalWidth = 10; complete = true;
}
// jsdom never loads images: an <img> is complete unless the test stalls it (a logo that never finishes in the DOM).
Object.defineProperty(HTMLImageElement.prototype, 'complete', { configurable: true,
  get(this: HTMLImageElement) { return !(cfg.domStall && isLogo(this.getAttribute('src') || '')); } });
Object.defineProperty(HTMLImageElement.prototype, 'naturalWidth', { configurable: true,
  get(this: HTMLImageElement) { return cfg.domStall && isLogo(this.getAttribute('src') || '') ? 0 : 300; } });

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
const wait = (ms: number) => (ms ? new Promise((r) => setTimeout(r, ms)) : Promise.resolve());
const fetchCalls: { url: string; at: number }[] = [];
beforeEach(() => {
  _resetModalPrefetch(); _resetImageLoader(); made.length = 0; fetchCalls.length = 0;
  Object.assign(cfg, { img: 'ok', domStall: false, logo: true, logoMs: 0, detailMs: 0, artMs: 30 });
  vi.useFakeTimers(); t0 = Date.now(); vi.stubGlobal('Image', FakeImage);
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    const url = String(u); fetchCalls.push({ url, at: Date.now() - t0 });
    if (url.includes('/api/clearlogo/')) { await wait(cfg.logoMs); return json({ clearlogo_url: cfg.logo ? 'https://image.tmdb.org/t/p/original/logo.png' : null }); }
    if (url.includes('/api/plex/movie/5003')) { await wait(cfg.detailMs); return json({ title: 'Jaws', year: 1975, tmdb_id: 578, art: '/library/metadata/5003/art/1', cast: [], directors: [] }); }
    if (url.includes('/api/movie/578')) { await wait(cfg.detailMs); return json({ id: 578, title: 'Jaws', backdrop_path: '/j.jpg', videos: { results: [] } }); }
    if (url.includes('/api/movie/603')) { await wait(cfg.detailMs); return json({ id: 603, title: 'The Matrix', backdrop_path: '/m.jpg', videos: { results: [] } }); }
    if (url.includes('/api/tv/1399')) { await wait(cfg.detailMs); return json({ id: 1399, name: 'Thrones', backdrop_path: '/t.jpg', seasons: [], videos: { results: [] } }); }
    return json({});
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

type Sheet = { name: string; kind: 'movie' | 'tv'; rk?: number; id: number; title: string; el: () => JSX.Element };
const SHEETS: Sheet[] = [
  { name: 'owned movie', kind: 'movie', rk: 5003, id: 578, title: 'Jaws', el: () => <MovieDetailModal ratingKey={5003} tmdbId={578} onClose={() => {}} /> },
  { name: 'TMDb movie', kind: 'movie', id: 603, title: 'The Matrix', el: () => <MovieDetailModal tmdbId={603} onClose={() => {}} /> },
  { name: 'TMDb show', kind: 'tv', id: 1399, title: 'Thrones', el: () => <TvDetailModal tmdbId={1399} onClose={() => {}} /> },
];

async function open(s: Sheet, rowText: string | null, ms = 3000) {
  const row = document.createElement('button'); if (rowText) row.setAttribute('aria-label', rowText);
  document.body.appendChild(row);
  void openWhenReady(s.kind, s.rk, s.id, row, () => {}, undefined, []);
  render(s.el());
  for (let i = 0; i < ms / 50; i++) await act(async () => { await vi.advanceTimersByTimeAsync(50); });
}
/** What the hero shows: 'logo' (a loaded logo over a hidden text title), the visible text title, or '' (neither). */
/** An element on the page that the user can't see: itself or an ancestor sits at opacity 0 (the held reveal). */
const held = (el: Element | null) => { for (let e = el; e; e = e.parentElement) if ((e as HTMLElement).style?.opacity === '0') return true; return false; };
function heroShows(): string {
  const logo = Array.from(document.querySelectorAll<HTMLImageElement>('img[src*="logo.png"]')).find((i) => !held(i)) ?? null;
  const h1 = Array.from(document.querySelectorAll('h1')).find((h) => h.style.display !== 'none' && h.style.visibility !== 'hidden' && !held(h));
  if (h1 && (h1.textContent ?? '').trim()) return (h1.textContent ?? '').trim();
  return logo && logo.complete ? 'logo' : '';
}

describe('v345: the logo lookup and load start at the tap', () => {
  it('the clearlogo request and a high-priority logo load start during the hold, before slow details', async () => {
    layoutMode.mode = 'compact'; cfg.detailMs = 900; cfg.logoMs = 50;
    const s = SHEETS[1];
    const row = document.createElement('button'); row.setAttribute('aria-label', s.title); document.body.appendChild(row);
    void openWhenReady(s.kind, s.rk, s.id, row, () => {}, undefined, []);
    render(s.el());
    for (let i = 0; i < 6; i++) await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(fetchCalls.find((c) => c.url.includes('/api/clearlogo/603'))?.at).toBeLessThan(50);
    const l = made.find((m) => isLogo(m.src));
    expect(l, 'logo load started before the details landed').toBeTruthy();
    expect(l!.pri).toBe('high');
    expect(l!.at).toBeLessThan(300);
  });
});

for (const mode of ['compact', 'expanded'] as const) {
  describe(`v345 ${mode}: a logo known by the 1.6s art deadline is shown, no swap`, () => {
    for (const s of SHEETS.filter((x) => x.kind === 'movie')) it(`${s.name}: logo lookup lands at 1.2s`, async () => {
      layoutMode.mode = mode; cfg.logoMs = 1200;
      await open(s, s.title);
      expect(document.querySelector('[data-hero-logo], img[src*="logo.png"]')).not.toBeNull();
      expect(heroShows()).toBe('logo');
    });
  });
  describe(`v345 ${mode}: never a blank title`, () => {
    for (const s of SHEETS) {
      const cases: [string, () => void, string | null][] = [
        ['logo decode fails', () => { cfg.img = 'fail'; }, s.title],
        ['logo never finishes loading (stall)', () => { cfg.img = 'stall'; }, s.title],
        ['logo img stalls in the DOM', () => { cfg.domStall = true; }, s.title],
        ['no logo', () => { cfg.logo = false; }, s.title],
        ['empty seed (a row with no title text), no logo', () => { cfg.logo = false; cfg.detailMs = 900; }, null],
        ['slow details (2s)', () => { cfg.detailMs = 2000; }, s.title],
      ];
      for (const [name, setup, rowText] of cases) it(`${s.name}: ${name}`, async () => {
        layoutMode.mode = mode; setup();
        await open(s, rowText, 4000);
        // A logo that never finishes is not a title: the frame is open, the title stays held (no cap-forced text).
        if (name.startsWith('logo never finishes')) {
          expect(document.querySelector('[data-modal="MovieDetailModal"], [data-modal="TvDetailModal"]')).not.toBeNull();
          expect(heroShows()).toBe('');
          return;
        }
        // Offscreen decode is display readiness. A DOM `complete` lie does not hide a decoded logo.
        if (name.startsWith('logo img stalls')) {
          expect(document.querySelector('img[src*="logo.png"]')).not.toBeNull();
          const h1 = Array.from(document.querySelectorAll('h1')).find((h) => (h.textContent ?? '').trim() === s.title);
          if (h1) expect(h1.style.display === 'none' || h1.style.visibility === 'hidden').toBe(true);
          return;
        }
        const shows = heroShows();
        expect(shows === 'logo' || shows === s.title, `hero shows "${shows}"`).toBe(true);
        if (name !== 'slow details (2s)') expect(shows).toBe(s.title);
      });
      it(`${s.name}: a decoded logo stays the logo if its <img> errors after the reveal`, async () => {
        layoutMode.mode = mode;
        await open(s, s.title);
        const img = document.querySelector<HTMLImageElement>('img[src*="logo.png"]');
        expect(img).not.toBeNull();
        act(() => { fireEvent.error(img!); });
        expect(heroShows()).toBe('logo');
      });
    }
  });
}

describe('v345b: desktop movie, logo shown means the text is hidden (kept in the DOM)', () => {
  const s = SHEETS[1];
  const h1 = () => Array.from(document.querySelectorAll('h1')).find((h) => (h.textContent ?? '').trim() === s.title)!;
  const visible = (h: HTMLElement) => h.style.display !== 'none' && h.style.visibility !== 'hidden';
  it('the frame is open while art holds, then shows the decoded logo with the text hidden', async () => {
    layoutMode.mode = 'expanded'; cfg.domStall = true; cfg.artMs = 1300;   // the backdrop holds the hero
    const row = document.createElement('button'); row.setAttribute('aria-label', s.title); document.body.appendChild(row);
    void openWhenReady(s.kind, s.rk, s.id, row, () => {}, undefined, []);
    render(s.el());
    for (let i = 0; i < 6; i++) await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(document.querySelector('[data-modal="MovieDetailModal"]'), 'the frame opens while art is undecoded').not.toBeNull();
    expect(heroShows(), 'nothing of the hero is visible before the art is decoded').toBe('');
    for (let i = 0; i < 40; i++) await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(document.querySelector('img[src*="logo.png"]')).not.toBeNull();
    expect(h1(), 'text kept in the DOM').toBeTruthy();
    expect(visible(h1()), 'text hidden under a shown logo').toBe(false);
  });
  it('a failed logo shows the text title; a decoded logo does not', async () => {
    layoutMode.mode = 'expanded'; cfg.domStall = true;
    await open(s, s.title);
    expect(document.querySelector('img[src*="logo.png"]')).not.toBeNull();
    expect(visible(h1())).toBe(false);
    cleanup(); _resetModalPrefetch(); _resetImageLoader(); cfg.domStall = false; cfg.img = 'fail';
    await open(s, s.title);
    expect(visible(h1())).toBe(true);
  });
  it('phone and TV desktop: the same rule (logo shown, text hidden)', async () => {
    for (const [mode, sh] of [['compact', SHEETS[1]], ['expanded', SHEETS[2]], ['compact', SHEETS[2]]] as const) {
      cleanup(); _resetModalPrefetch(); _resetImageLoader(); layoutMode.mode = mode;
      await open(sh, sh.title);
      const t = Array.from(document.querySelectorAll('h1')).find((h) => (h.textContent ?? '').trim() === sh.title)!;
      expect(document.querySelector('img[src*="logo.png"]'), `${mode} ${sh.name}`).not.toBeNull();
      expect(t.style.display === 'none' || t.style.visibility === 'hidden', `${mode} ${sh.name}`).toBe(true);
    }
  });
});

describe('v345b: a logo already loaded at the tap is the one the reveal uses (CW: Last Breath)', () => {
  for (const mode of ['compact', 'expanded'] as const) it(`${mode}: card logo loaded at the tap, details at 1.1s: logo`, async () => {
    layoutMode.mode = mode; cfg.detailMs = 1100;
    const s = SHEETS[0];
    const row = document.createElement('button'); row.setAttribute('aria-label', s.title); document.body.appendChild(row);
    void openWhenReady(s.kind, s.rk, undefined, row, () => {}, undefined, [], { kind: 'movie', tmdbId: s.id, url: 'https://image.tmdb.org/t/p/original/logo.png' });
    render(<MovieDetailModal ratingKey={5003} onClose={() => {}} />);
    for (let i = 0; i < 4; i++) await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(made.some((m) => isLogo(m.src) && m.pri === 'high'), 'logo loading from the tap').toBe(true);
    for (let i = 0; i < 60; i++) await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(heroShows()).toBe('logo');
  });
});
