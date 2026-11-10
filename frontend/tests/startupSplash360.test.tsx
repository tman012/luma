/** v360 startup splash: the Luma logo on the Home background, held until Home's data has loaded and its first-screen
 *  artwork has decoded, then handed off into Home's own logo. Bounded by a hard cap, a plain crossfade under reduced
 *  motion, and never shown again by a background refresh. */
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

class FakeAudioContext { state = 'running'; resume() { return Promise.resolve(); } createBuffer() { return {}; } createBufferSource() { return { connect() {}, start() {}, buffer: null }; } destination = {}; }
(globalThis as unknown as { AudioContext: unknown }).AudioContext ??= FakeAudioContext;

const auth = { authenticated: true, loading: false, needsStart: false, startInProgress: false, pinUnlock: false,
  user: { id: 1, name: 'taylor', is_admin: true }, implicit: false, logout: vi.fn() };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
const modals = { openMovie: vi.fn(), openTv: vi.fn() };
vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => modals }));

import { MinimalHomePage } from '@/pages/MinimalHomePage';
import { runHomeGate } from '@/startup/homeGate';
import { artRect, createSplash, flipTransform, releaseSplash, resetSplash, splashActive, splashRemainingMs, type Splash } from '@/startup/splash';
import { firstScreenImages } from '@/startup/decode';
import { MOTION, SPLASH_MOTION } from '@/theme/motion';
import { _resetSheetPriority } from '@/loading/sheetPriority';

const read = (p: string) => readFileSync(resolve(__dirname, '..', p), 'utf8');
const root = (p: string) => resolve(__dirname, '../..', p);

/* ── The readiness gate: data AND decoded artwork, a hard cap, never a hang ─────────────────────────────── */

describe('the readiness gate', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  function gate(over: Partial<Parameters<typeof runHomeGate<{ n: number }>>[0]> = {}) {
    let answer!: (d: { n: number }) => void;
    let fail!: () => void;
    let finishDecode!: () => void;
    const decode = vi.fn((_u: string[], _cap: number) => new Promise<void>(r => { finishDecode = r; }));
    const g = runHomeGate<{ n: number }>({
      load: () => new Promise((res, rej) => { answer = res; fail = () => rej(new Error('x')); }),
      urls: () => ['a.jpg', 'b.jpg'],
      decode,
      capMs: 7000,
      holdMs: Infinity,
      ...over,
    });
    let result: unknown = 'pending';
    void g.promise.then(r => { result = r; });
    return { g, decode, answer: (d = { n: 1 }) => answer(d), fail: () => fail(), finishDecode: () => finishDecode(), result: () => result };
  }

  it('is not ready on data alone: it waits for the artwork to decode', async () => {
    const t = gate();
    t.answer();
    await vi.advanceTimersByTimeAsync(2000);
    expect(t.decode).toHaveBeenCalledWith(['a.jpg', 'b.jpg'], expect.any(Number));
    expect(t.result()).toBe('pending');
    t.finishDecode();
    await vi.advanceTimersByTimeAsync(0);
    expect(t.result()).toEqual({ data: { n: 1 }, reason: 'ready' });
  });

  it('is not ready on decoded artwork alone: no answer, no handoff', async () => {
    const t = gate();
    await vi.advanceTimersByTimeAsync(6900);
    expect(t.decode).not.toHaveBeenCalled();
    expect(t.result()).toBe('pending');
  });

  it('is ready at once when the data is warm and nothing needs decoding', async () => {
    const t = gate({ urls: () => [], decode: () => Promise.resolve() });
    t.answer();
    await vi.advanceTimersByTimeAsync(0);
    expect(t.result()).toEqual({ data: { n: 1 }, reason: 'ready' });
  });

  it('cap: with no answer at the cap it gives up with nothing instead of hanging; a late answer is dropped', async () => {
    const t = gate();
    await vi.advanceTimersByTimeAsync(6999);
    expect(t.result()).toBe('pending');
    await vi.advanceTimersByTimeAsync(2);
    expect(t.result()).toEqual({ data: null, reason: 'timeout' });
    t.answer();
    await vi.advanceTimersByTimeAsync(0);
    expect(t.decode).not.toHaveBeenCalled();
    expect(t.result()).toEqual({ data: null, reason: 'timeout' });
  });

  it('cap: artwork that never decodes is released at the cap with the data that did load', async () => {
    let t0 = 0;
    const t = gate({ now: () => t0 });
    await vi.advanceTimersByTimeAsync(3000); t0 = 3000;
    t.answer();
    await vi.advanceTimersByTimeAsync(0);
    expect(t.decode).toHaveBeenCalledWith(expect.any(Array), 4000);   // only what is left of the cap
  });

  it('the artwork hold is also bounded by holdMs when the splash is not up', async () => {
    const t = gate({ holdMs: 1500 });
    t.answer();
    await vi.advanceTimersByTimeAsync(0);
    expect(t.decode).toHaveBeenCalledWith(expect.any(Array), 1500);
  });

  it('a failed load settles with an error, not a hang', async () => {
    const t = gate();
    t.fail();
    await vi.advanceTimersByTimeAsync(0);
    expect(t.result()).toEqual({ data: null, reason: 'error' });
  });

  it('cancel drops the result', async () => {
    const t = gate();
    t.g.cancel();
    t.answer();
    await vi.advanceTimersByTimeAsync(7100);
    expect(t.result()).toBe('pending');
  });
});

/* ── The splash itself: intro minimum, decode wait, flip, reduced motion, cap, once ─────────────────────── */

type Call = { el: HTMLElement; keyframes: Keyframe[]; ms: number };

function stage(opts: { reduced?: boolean; capMs?: number } = {}) {
  document.body.innerHTML = `<div id="splash"><div id="splash-bg"></div><div id="splash-stage"><img id="splash-logo" alt=""></div></div><div id="root"></div>`;
  const calls: Call[] = [];
  let t = 0;
  const splash = document.getElementById('splash')!;
  const logo = document.getElementById('splash-logo')!;
  for (const el of [splash, document.getElementById('splash-bg')!, logo]) {
    (el as unknown as { animate: unknown }).animate = vi.fn((keyframes: Keyframe[], o: KeyframeAnimationOptions) => {
      calls.push({ el, keyframes, ms: Number(o.duration) });
      return { finished: new Promise<void>(r => setTimeout(r, Number(o.duration))) };
    });
  }
  // The CSS intro: finished after introMs of the page's life.
  const introMs = opts.reduced ? SPLASH_MOTION.reducedIntroMs : SPLASH_MOTION.introMs;
  (logo as unknown as { getAnimations: unknown }).getAnimations = () => [{ finished: new Promise<void>(r => setTimeout(r, Math.max(0, introMs - t))) }];
  logo.getBoundingClientRect = () => ({ left: 95, top: 400, width: 200, height: 91, right: 295, bottom: 491, x: 95, y: 400, toJSON() {} });
  const advance = async (ms: number) => { t += ms; await vi.advanceTimersByTimeAsync(ms); };
  const s = createSplash({ now: () => t, reduced: () => !!opts.reduced, capMs: opts.capMs ?? 7000 });
  return { s, calls, advance, now: () => t, splash, logo };
}

function homeLogo(rect = { left: 27, top: 12, width: 90, height: 41 }) {
  const el = document.createElement('img');
  el.setAttribute('data-splash-target', '');
  el.getBoundingClientRect = () => ({ ...rect, right: rect.left + rect.width, bottom: rect.top + rect.height, x: rect.left, y: rect.top, toJSON() {} });
  (el as unknown as { decode: unknown }).decode = () => Promise.resolve();
  document.getElementById('root')!.appendChild(el);
  return el;
}

describe('the splash', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); document.body.innerHTML = ''; document.documentElement.removeAttribute('data-splash-flip'); });

  it('warm: no wait beyond the intro, then the logo flies into the Home logo while the background fades', async () => {
    const { s, calls, advance, splash } = stage();
    const target = homeLogo();
    void s.release({ target: () => target });
    await advance(SPLASH_MOTION.introMs - 1);
    expect(calls).toHaveLength(0);                       // the intro is the minimum
    expect(s.phase).toBe('showing');
    await advance(1);
    expect(s.phase).toBe('handoff');
    expect(document.documentElement.hasAttribute('data-splash-flip')).toBe(true);
    expect(splash.style.pointerEvents).toBe('none');    // taps reach Home during the handoff
    const logoMove = calls.find(c => c.el.id === 'splash-logo')!;
    const bgFade = calls.find(c => c.el.id === 'splash-bg')!;
    expect(logoMove.ms).toBe(SPLASH_MOTION.handoffMs);
    expect(bgFade.keyframes).toEqual([{ opacity: 1 }, { opacity: 0 }]);
    // translate + scale only; the box lands on the Home logo's: 90 / 200 scale, centers 72/32.5 vs 195/445.5
    expect(logoMove.keyframes[1]).toEqual({ transform: 'translate(-123px, -413px) scale(0.45)' });
    await advance(SPLASH_MOTION.handoffMs);
    expect(s.phase).toBe('done');
    expect(document.getElementById('splash')).toBeNull();
    expect(document.documentElement.hasAttribute('data-splash-flip')).toBe(false);
    expect(s.stats.mode).toBe('flip');
    expect(s.stats.reason).toBe('ready');
  });

  it('holds past the intro while the first-screen artwork is still decoding', async () => {
    const { s, calls, advance } = stage();
    let done!: () => void;
    const slow = document.createElement('img');
    (slow as unknown as { decode: unknown }).decode = () => new Promise<void>(r => { done = r; });
    void s.release({ images: () => [slow], target: () => homeLogo() });
    await advance(3000);
    expect(s.phase).toBe('showing');
    expect(calls).toHaveLength(0);
    done();
    await advance(0);
    expect(s.phase).toBe('handoff');
  });

  it('an image that fails to decode does not hold the splash', async () => {
    const { s, advance } = stage();
    const bad = document.createElement('img');
    (bad as unknown as { decode: unknown }).decode = () => Promise.reject(new Error('decode'));
    void s.release({ images: () => [bad] });
    await advance(SPLASH_MOTION.introMs);
    expect(s.phase).not.toBe('showing');
  });

  it('cap: when the first screen never becomes ready, the hard cap hands off anyway and nothing hangs', async () => {
    const { s, advance } = stage();
    const never = document.createElement('img');
    (never as unknown as { decode: unknown }).decode = () => new Promise<void>(() => {});
    void s.release({ images: () => [never] });
    await advance(6999);
    expect(s.phase).toBe('showing');
    await advance(1);
    expect(s.phase).toBe('handoff');
    expect(s.stats.reason).toBe('cap');
    await advance(SPLASH_MOTION.handoffMs);
    expect(s.phase).toBe('done');
    expect(document.getElementById('splash')).toBeNull();
  });

  it('cap: with no screen ever calling release, the armed cap still hands off', async () => {
    const { s, advance } = stage();
    s.arm();
    await advance(7000);
    expect(s.phase).toBe('handoff');
    await advance(SPLASH_MOTION.handoffMs);
    expect(s.phase).toBe('done');
    expect(s.stats.reason).toBe('cap');
    expect(s.stats.mode).toBe('fade');                    // nothing to fly into: a plain crossfade
  });

  it('reduced motion: one crossfade of the whole splash, no scale or translate, Home logo never hidden', async () => {
    const { s, calls, advance } = stage({ reduced: true });
    void s.release({ target: () => homeLogo() });
    await advance(SPLASH_MOTION.reducedIntroMs);
    expect(s.phase).toBe('handoff');
    expect(document.documentElement.hasAttribute('data-splash-flip')).toBe(false);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.el.id).toBe('splash');
    expect(calls[0]!.keyframes).toEqual([{ opacity: 1 }, { opacity: 0 }]);
    expect(calls[0]!.ms).toBe(SPLASH_MOTION.reducedHandoffMs);
    expect(JSON.stringify(calls)).not.toMatch(/transform|scale|translate/);
    await advance(SPLASH_MOTION.reducedHandoffMs);
    expect(s.phase).toBe('done');
    expect(s.stats.mode).toBe('fade');
  });

  it('a target with no size (its image not laid out) falls back to the crossfade', async () => {
    const { s, calls, advance } = stage();
    void s.release({ target: () => homeLogo({ left: 0, top: 0, width: 0, height: 0 }) });
    await advance(SPLASH_MOTION.introMs);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.el.id).toBe('splash');
  });

  it('is released once and cannot come back: a later release or arm does nothing', async () => {
    const { s, advance } = stage();
    void s.release();
    await advance(SPLASH_MOTION.introMs + SPLASH_MOTION.handoffMs);
    expect(s.phase).toBe('done');
    expect(s.active()).toBe(false);
    void s.release({ target: () => homeLogo() });
    s.arm();
    await advance(10000);
    expect(s.phase).toBe('done');
    expect(document.getElementById('splash')).toBeNull();
  });

  it('without a splash in the page (a test, a route that never had one) it is inactive and never blocks', () => {
    document.body.innerHTML = '<div id="root"></div>';
    const s = createSplash({ now: () => 0 });
    expect(s.active()).toBe(false);
    expect(s.remainingMs()).toBe(Infinity);
  });
});

describe('the flip math', () => {
  it('moves the splash logo box onto the target box about their centers', () => {
    expect(flipTransform({ left: 100, top: 300, width: 200, height: 91 }, { x: 20, y: 10, w: 100, h: 45.5 }))
      .toBe('translate(-130px, -312.75px) scale(0.5)');
  });

  it('the phone logo carries transparent padding: only the artwork crop is the target', () => {
    const img = document.createElement('img');
    img.setAttribute('data-splash-art', '215 258 916 416 1520 1024');
    img.getBoundingClientRect = () => ({ left: 6, top: -22, width: 148.4375, height: 100, right: 154.4, bottom: 78, x: 6, y: -22, toJSON() {} });
    const a = artRect(img);
    const s = 100 / 1024;
    expect(a.x).toBeCloseTo(6 + 215 * s, 3);
    expect(a.y).toBeCloseTo(-22 + 258 * s, 3);
    expect(a.w).toBeCloseTo(916 * s, 3);
    expect(a.h).toBeCloseTo(416 * s, 3);
    expect(a.w / a.h).toBeCloseTo(916 / 416, 5);        // the splash logo's own ratio: a uniform scale lands exactly
  });

  it('a target without a crop is its whole box', () => {
    const img = document.createElement('img');
    img.getBoundingClientRect = () => ({ left: 44, top: 20, width: 140, height: 64, right: 184, bottom: 84, x: 44, y: 20, toJSON() {} });
    expect(artRect(img)).toEqual({ x: 44, y: 20, w: 140, h: 64 });
  });
});

describe('first-screen images', () => {
  afterEach(() => { document.body.innerHTML = ''; });
  it('takes the ones in the viewport, makes them eager and high priority, and leaves the splash and offscreen ones', () => {
    document.body.innerHTML = '<div id="splash"><img id="s"></div><img id="a" loading="lazy"><img id="b" loading="lazy">';
    const rect = (top: number) => () => ({ left: 0, top, width: 100, height: 100, right: 100, bottom: top + 100, x: 0, y: top, toJSON() {} });
    for (const id of ['s', 'a']) document.getElementById(id)!.getBoundingClientRect = rect(10);
    document.getElementById('b')!.getBoundingClientRect = rect(5000);
    const got = firstScreenImages();
    expect(got.map(i => i.id)).toEqual(['a']);
    expect(got[0]!.loading).toBe('eager');
    expect(got[0]!.getAttribute('fetchpriority')).toBe('high');
    expect(document.getElementById('b')!.getAttribute('loading')).toBe('lazy');
  });
});

/* ── Home: the real page, with a splash in the document ─────────────────────────────────────────────────── */

const HOME = {
  plex: true,
  continue_watching: [],
  rails: [{ id: '1', title: 'Movies', type: 'movie', items: [{ key: '21', type: 'movie', title: 'Heat', subtitle: '1995', poster: '/api/img/plex?url=p' }] }],
};
const json = (body: unknown, ok = true) => ({ ok, status: ok ? 200 : 500, json: async () => body }) as Response;

describe('Home with the startup splash', () => {
  let t = 0;
  let spl: Splash;
  beforeEach(() => {
    _resetSheetPriority();
    t = 0;
    document.body.innerHTML = '<div id="splash"><div id="splash-bg"></div><div id="splash-stage"><img id="splash-logo" alt=""></div></div>';
    for (const id of ['splash', 'splash-bg', 'splash-logo']) {
      (document.getElementById(id) as unknown as { animate: unknown }).animate = () => ({ finished: Promise.resolve() });
    }
    spl = createSplash({ now: () => t, reduced: () => false });
    resetSplash(spl);
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); resetSplash(null); document.body.innerHTML = ''; });

  const mount = () => render(<MemoryRouter><MinimalHomePage /></MemoryRouter>, { container: document.body.appendChild(document.createElement('div')) });

  it('stays up while the rails load, shows no skeleton, and hands off once Home is populated', async () => {
    let answer!: (r: Response) => void;
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(r => { answer = r; })));
    mount();
    expect(splashActive()).toBe(true);
    expect(spl.phase).toBe('showing');
    expect(screen.queryByTestId('home-skeleton')).toBeNull();
    expect(screen.queryByTestId('home-rails')).toBeNull();
    await act(async () => { answer(json(HOME)); });
    await waitFor(() => expect(screen.getByTestId('home-rails')).toBeTruthy());
    await waitFor(() => expect(spl.phase).toBe('done'));
    expect(document.getElementById('splash')).toBeNull();
    expect(spl.stats.reason).toBe('ready');
    expect(screen.getByTestId('phone-logo').getAttribute('data-splash-target')).toBe('');
    expect(screen.getByTestId('phone-logo').getAttribute('data-splash-art')).toBe('215 258 916 416 1520 1024');
  });

  it('with the splash up, only the images actually on the first screen are waited for (no pre-decode of every rail)', async () => {
    const made: string[] = [];
    class CountingImage { decoding = ''; set src(v: string) { made.push(v); } decode() { return Promise.resolve(); } }
    vi.stubGlobal('Image', CountingImage);
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(json(HOME))));
    mount();
    await waitFor(() => expect(spl.phase).toBe('done'));
    expect(made).toEqual([]);
    expect(screen.getByTestId('home-rails')).toBeTruthy();
  });

  it('startup timers follow the splash cap: Home gives up at the cap, not a second 7 s later', async () => {
    vi.useFakeTimers();
    t = 2000;                                                  // the app booted 2 s into the page load
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    mount();
    expect(splashRemainingMs()).toBe(5000);
    await act(async () => { t = 6900; await vi.advanceTimersByTimeAsync(4900); });
    expect(screen.queryByTestId('home-down')).toBeNull();
    await act(async () => { t = 7000; await vi.advanceTimersByTimeAsync(200); });
    expect(screen.getByTestId('home-down')).toBeTruthy();       // whatever loaded: the existing Retry state
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(spl.phase).toBe('done');
  });

  it('a background refresh after startup never shows the splash or the logo screen again', async () => {
    let calls = 0;
    vi.stubGlobal('fetch', vi.fn(() => { calls++; return Promise.resolve(json(HOME)); }));
    const view = mount();
    await waitFor(() => expect(spl.phase).toBe('done'));
    expect(screen.getByTestId('home-rails')).toBeTruthy();
    const seen: string[] = [];
    const watch = new MutationObserver(() => {
      if (document.getElementById('splash')) seen.push('splash');
      if (screen.queryByTestId('startup-loading')) seen.push('startup-loading');
      if (screen.queryByTestId('home-skeleton')) seen.push('skeleton');
    });
    watch.observe(document.body, { childList: true, subtree: true });
    // A later refetch (a resize across the kiosk width, a Retry) and a repeated release keep Home and add nothing.
    window.dispatchEvent(new Event('focus'));
    document.dispatchEvent(new Event('visibilitychange'));
    await act(async () => { await releaseSplash(); });
    view.rerender(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    await act(async () => { await Promise.resolve(); });
    watch.disconnect();
    expect(seen).toEqual([]);
    expect(splashActive()).toBe(false);
    expect(spl.phase).toBe('done');
    expect(screen.getByTestId('home-rails')).toBeTruthy();
    expect(calls).toBeGreaterThanOrEqual(1);
  });

  it('a Home that errors still hands the splash off (never a hang)', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('down'))));
    mount();
    await waitFor(() => expect(spl.phase).toBe('done'));
    expect(screen.getByTestId('home-down')).toBeTruthy();
  });
});

/* ── The static launch: index.html, manifest, iOS launch screens, SW stamp ──────────────────────────────── */

describe('the native launch matches the splash', () => {
  const html = read('index.html');
  const manifest = JSON.parse(readFileSync(root('static/manifest.json'), 'utf8'));

  it('index.html paints the logo with no spinner and no text, before any script', () => {
    expect(html).toMatch(/<div id="splash"[^>]*>[\s\S]*<img id="splash-logo"[^>]*src="data:image\/webp;base64,/);
    expect(html.indexOf('id="splash"')).toBeLessThan(html.indexOf('<script'));
    expect(html).not.toMatch(/Loading|spinner|splash-ring|splash-status/);
    const body = html.slice(html.indexOf('<body>'), html.indexOf('<script'));
    expect(body.replace(/<[^>]+>/g, '').trim()).toBe('');            // no text node anywhere in the splash markup
  });

  it('the inline logo is the committed splash asset', () => {
    const b64 = readFileSync(root('static/images/luma_splash.webp')).toString('base64');
    expect(html).toContain(`data:image/webp;base64,${b64}`);
  });

  it('manifest colors, theme color and the splash background are one color', () => {
    expect(manifest.background_color).toBe(SPLASH_MOTION.bg);
    expect(manifest.theme_color).toBe(SPLASH_MOTION.bg);
    expect(html).toContain(`<meta name="theme-color" content="${SPLASH_MOTION.bg}">`);
    expect(html).toMatch(/#splash-bg \{[^}]*background: #15102a/);
    expect(read('src/index.css')).toContain('--color-kiosk-black: #15102a');
  });

  it('the inline CSS repeats the motion tokens exactly', () => {
    expect(html).toContain(`animation: lumaSplashIn ${SPLASH_MOTION.introMs}ms ${SPLASH_MOTION.introEase} both`.replace(/,(?=\d|0)/g, ', '));
    expect(html).toContain(`from { opacity: 0; transform: scale(${SPLASH_MOTION.introScale}); }`);
    expect(html).toContain(`width: ${SPLASH_MOTION.logoWidth}`);
    expect(html).toContain(`animation: lumaSplashFade ${SPLASH_MOTION.reducedIntroMs}ms linear both`);
    expect(SPLASH_MOTION.introMs).toBe(MOTION.dur.sheetEnter);
    expect(SPLASH_MOTION.capMs).toBe(7000);
    expect(read('src/components/start/StartupLoading.tsx')).toContain('SPLASH_MOTION.logoWidth');
  });

  it('every iOS launch image exists, is solid splash color, and is listed with its device query', () => {
    const links = [...html.matchAll(/<link rel="apple-touch-startup-image" media="\(device-width: (\d+)px\) and \(device-height: (\d+)px\) and \(-webkit-device-pixel-ratio: (\d)\) and \(orientation: portrait\)" href="\/static\/images\/splash\/([^"]+)">/g)];
    expect(links.length).toBeGreaterThanOrEqual(12);
    for (const [, w, h, d, file] of links) {
      expect(file).toBe(`ios-${Number(w) * Number(d)}x${Number(h) * Number(d)}.png`);
      expect(existsSync(root(`static/images/splash/${file}`))).toBe(true);
    }
  });

  it('the page is a standalone app on iOS and the splash height is the full screen box Home uses', () => {
    expect(html).toContain('<meta name="apple-mobile-web-app-capable" content="yes">');
    expect(html).toContain('--luma-screen-h, 100lvh');
  });

  it('the service worker is v362 and the build stamp names it', () => {
    expect(read('../static/sw.js')).toContain("const CACHE_VERSION = 'media-kiosk-v362';");
    expect(JSON.parse(read('../static/sw-stamp.json')).cache_version).toBe('media-kiosk-v362');
  });
});

describe('the splash does not touch what is installed', () => {
  it('the nav bar, Watchlist rail and sheets are not imported by the splash modules', () => {
    for (const f of ['src/startup/splash.ts', 'src/startup/homeGate.ts', 'src/startup/decode.ts']) {
      expect(read(f)).not.toMatch(/HomeSearchR3|DesktopDock|Watchlist|AppModalHost|SheetFrame|useSurfacePresence/);
    }
  });
  it('the splash animates transform and opacity only, and has no way to put itself back on screen', () => {
    const src = read('src/startup/splash.ts');
    const keys = src.split('\n').filter(l => l.includes('.animate(')).flatMap(l => [...l.matchAll(/[{,] ?([a-zA-Z]+): /g)].map(m => m[1]!));
    expect(new Set(keys.filter(k => !['duration', 'easing', 'fill'].includes(k)))).toEqual(new Set(['transform', 'opacity']));
    expect(src).not.toMatch(/createElement|appendChild|insertAdjacent|innerHTML/);
  });
});
