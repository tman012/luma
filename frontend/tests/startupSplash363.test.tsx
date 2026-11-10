/** v363 startup: Plex-style entrance, a FLIP that moves only the logo while Home fades in around it, one background
 *  color from the OS launch screen to Home, and a rail gate that covers every rail (below the fold included): no
 *  skeletons ever, a rail not ready at the handoff stays collapsed and fades in on its own. */
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { readFileSync } from 'node:fs';
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
import { cardsAcross } from '@/components/home/Rails';
import { RailGate, RAIL_PENDING_ATTR, RAIL_REVEAL_ATTR } from '@/startup/railGate';
import { createSplash, resetSplash, type Splash } from '@/startup/splash';
import { SPLASH_MOTION } from '@/theme/motion';
import { _resetSheetPriority } from '@/loading/sheetPriority';

const read = (p: string) => readFileSync(resolve(__dirname, '..', p), 'utf8');
const root = (p: string) => resolve(__dirname, '../..', p);

function decodable(count = 1) {
  const imgs = Array.from({ length: count }, () => {
    let done!: () => void;
    const img = document.createElement('img');
    (img as unknown as { decode: () => Promise<void> }).decode = () => new Promise<void>(r => { done = r; });
    return { img, done: () => done() };
  });
  return imgs;
}
const section = () => { const el = document.createElement('section'); document.body.appendChild(el); return el; };

describe('the rail gate', () => {
  afterEach(() => { document.body.innerHTML = ''; });

  it('is ready with no rails, and when every bound rail has decoded', async () => {
    const g = new RailGate(false);
    await expect(g.whenReady()).resolves.toBeUndefined();
    const [a, b] = decodable(1).concat(decodable(1));
    const elA = section(), elB = section();
    g.bind('rail-a', elA, [a!.img]);
    g.bind('rail-b', elB, [b!.img]);
    let ready = false;
    void g.whenReady().then(() => { ready = true; });
    expect(g.pending()).toEqual(['rail-a', 'rail-b']);
    a!.done(); await Promise.resolve(); await Promise.resolve();
    expect(ready).toBe(false);
    b!.done(); await new Promise(r => setTimeout(r, 0));
    expect(ready).toBe(true);
    expect(g.pending()).toEqual([]);
  });

  it('while the gate is open nothing is revealed; closing it collapses an undecoded rail and keeps a decoded one', async () => {
    const g = new RailGate(false);
    const [slow, fast] = decodable(1).concat(decodable(1));
    const elSlow = section(), elFast = section();
    g.bind('rail-watchlist', elSlow, [slow!.img]);
    g.bind('rail-movies', elFast, [fast!.img]);
    fast!.done(); await new Promise(r => setTimeout(r, 0));
    expect(elSlow.hasAttribute(RAIL_PENDING_ATTR)).toBe(false);     // the splash covers it: laid out, loading, invisible
    g.close();
    expect(elSlow.hasAttribute(RAIL_PENDING_ATTR)).toBe(true);      // not ready at the handoff: collapsed (display none, no box)
    expect(elFast.hasAttribute(RAIL_PENDING_ATTR)).toBe(false);
    expect(elFast.hasAttribute(RAIL_REVEAL_ATTR)).toBe(false);      // it was ready: no reveal, it is simply there
    slow!.done(); await new Promise(r => setTimeout(r, 0));
    expect(elSlow.hasAttribute(RAIL_PENDING_ATTR)).toBe(false);     // complete: expands once, fading in
    expect(elSlow.hasAttribute(RAIL_REVEAL_ATTR)).toBe(true);
  });

  it('a gate created after the splash is gone collapses every rail until it has decoded', async () => {
    const g = new RailGate(true);
    const [x] = decodable(1);
    const el = section();
    g.bind('rail-x', el, [x!.img]);
    expect(el.hasAttribute(RAIL_PENDING_ATTR)).toBe(true);
    x!.done(); await new Promise(r => setTimeout(r, 0));
    expect(el.hasAttribute(RAIL_PENDING_ATTR)).toBe(false);
    expect(el.hasAttribute(RAIL_REVEAL_ATTR)).toBe(true);
  });

  it('art that fails to decode counts as done, so a broken image never holds a rail back', async () => {
    const g = new RailGate(false);
    const img = document.createElement('img');
    (img as unknown as { decode: () => Promise<void> }).decode = () => Promise.reject(new Error('404'));
    g.bind('rail-x', section(), [img]);
    await expect(g.whenReady()).resolves.toBeUndefined();
  });

  it('binding again on a later commit does not restart the rail, and follows a re-mounted element', async () => {
    const g = new RailGate(false);
    const [x] = decodable(1);
    const first = section(), second = section();
    const decode = vi.spyOn(x!.img as unknown as { decode: () => Promise<void> }, 'decode');
    g.bind('rail-x', first, [x!.img]);
    g.bind('rail-x', second, [x!.img]);
    expect(decode).toHaveBeenCalledTimes(1);
    g.close();
    expect(second.hasAttribute(RAIL_PENDING_ATTR)).toBe(true);
  });

  it('puts the art of the first screen at high priority and loads it eagerly', () => {
    const g = new RailGate(false);
    const img = document.createElement('img');
    img.loading = 'lazy';
    g.bind('rail-x', section(), [img], true);
    expect(img.loading).toBe('eager');
    expect(img.getAttribute('fetchpriority')).toBe('high');
  });
});

describe('how many cards a rail waits for', () => {
  it('covers every card across a 390 px phone, the partly visible one included', () => {
    expect(cardsAcross('wide', 390)).toBe(2);        // a 281 px card and the start of the next
    expect(cardsAcross('poster', 390)).toBe(3);      // 117 px posters: three and a part
    expect(cardsAcross('watchlist', 390)).toBe(3);   // 116 px posters, 12 px edge
  });
  it('never asks for fewer than one, and grows with the width', () => {
    expect(cardsAcross('wide', 100)).toBe(1);
    expect(cardsAcross('poster', 768)).toBeGreaterThan(cardsAcross('poster', 390));
  });
});

/* ── Home with a splash in the page: the Watchlist rail, below the fold, is gated too ─────────────────────── */

const card = (key: string, art: string) => ({ key, type: 'movie', title: key, subtitle: '', poster: art });
const HOME = {
  plex: true,
  continue_watching: [],
  rails: [
    { id: '1', title: 'Movies', type: 'movie', items: [1, 2, 3, 4].map(i => card(`m${i}`, `/api/img/plex?url=m${i}`)) },
    { id: 'watchlist', title: 'Watchlist', type: 'movie', items: [1, 2, 3, 4].map(i => card(`w${i}`, `/api/img/plex?url=w${i}`)) },
  ],
};
const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;

describe('Home under the splash', () => {
  let t = 0;
  let spl: Splash;
  let holds: Map<string, () => void>;
  let proto: { decode?: unknown };
  let original: unknown;

  beforeEach(() => {
    _resetSheetPriority();
    t = 0;
    holds = new Map();
    proto = HTMLImageElement.prototype as unknown as { decode?: unknown };
    original = proto.decode;
    proto.decode = function (this: HTMLImageElement) {
      const src = this.getAttribute('src') ?? '';
      if (!src.includes('url=w')) return Promise.resolve();
      return new Promise<void>(r => { holds.set(src, r); });
    };
    document.body.innerHTML = '<div id="splash"><div id="splash-bg"></div><div id="splash-stage"><div id="splash-logo"></div></div></div>';
    for (const id of ['splash', 'splash-bg', 'splash-logo']) {
      (document.getElementById(id) as unknown as { animate: unknown }).animate = () => ({ finished: Promise.resolve() });
    }
    spl = createSplash({ now: () => t, reduced: () => false });
    resetSplash(spl);
  });
  afterEach(() => {
    cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); resetSplash(null); document.body.innerHTML = '';
    proto.decode = original;
  });

  const mount = () => render(<MemoryRouter><MinimalHomePage /></MemoryRouter>, { container: document.body.appendChild(document.createElement('div')) });

  it('holds the splash for the Watchlist art, shows no skeleton or placeholder at any point, then hands off', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(json(HOME))));
    const seen = new Set<string>();
    const watch = new MutationObserver(() => {
      if (document.querySelector('[data-testid="home-skeleton"], .luma-shimmer, [data-skel-rail]')) seen.add('skeleton');
      if (document.querySelector('[data-testid="startup-loading"]')) seen.add('loading');
    });
    watch.observe(document.body, { childList: true, subtree: true });
    mount();
    await waitFor(() => expect(screen.getByTestId('rail-watchlist')).toBeTruthy());
    await act(async () => { await new Promise(r => setTimeout(r, 30)); });
    expect(holds.size).toBeGreaterThan(0);                  // the Watchlist art is being waited for
    expect(spl.phase).toBe('showing');                      // below the fold or not, the splash stays up
    holds.forEach(r => r());
    await waitFor(() => expect(spl.phase).toBe('done'));
    watch.disconnect();
    expect(spl.stats.reason).toBe('ready');
    expect(seen.has('skeleton')).toBe(false);
    expect(screen.getByTestId('rail-watchlist').hasAttribute(RAIL_PENDING_ATTR)).toBe(false);
    expect(screen.getByTestId('rail-watchlist').hasAttribute(RAIL_REVEAL_ATTR)).toBe(false);   // ready in time: no late reveal
  });

  it('at the cap the rail that is not ready stays collapsed, with no skeleton in its place, and fades in when complete', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(json(HOME))));
    mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(screen.getByTestId('rail-watchlist')).toBeTruthy();
    expect(spl.phase).toBe('showing');
    t = SPLASH_MOTION.capMs;
    await act(async () => { await vi.advanceTimersByTimeAsync(SPLASH_MOTION.capMs); });
    expect(spl.stats.reason).toBe('cap');
    await act(async () => { await vi.advanceTimersByTimeAsync(SPLASH_MOTION.handoffMs + 50); });
    expect(spl.phase).toBe('done');
    const wl = screen.getByTestId('rail-watchlist');
    expect(wl.hasAttribute(RAIL_PENDING_ATTR)).toBe(true);                       // collapsed: display none, no reserved box
    expect(screen.getByTestId('rail-1').hasAttribute(RAIL_PENDING_ATTR)).toBe(false);
    expect(document.querySelector('[data-testid="home-skeleton"], .luma-shimmer, [data-skel-rail]')).toBeNull();
    expect(read('src/components/home/Rails.tsx')).toContain('[data-rail-pending]{display:none !important}');
    await act(async () => { holds.forEach(r => r()); await vi.advanceTimersByTimeAsync(0); });
    expect(wl.hasAttribute(RAIL_PENDING_ATTR)).toBe(false);
    expect(wl.hasAttribute(RAIL_REVEAL_ATTR)).toBe(true);                        // one fade-in, opacity only
  });
});

describe('the fade-in of a late rail and of Home', () => {
  const rails = read('src/components/home/Rails.tsx');
  const html = read('index.html');

  it('a late rail fades in with opacity only, so nothing above it moves', () => {
    const rule = rails.match(/@keyframes lumaRailReveal\{[^}]*\}[^}]*\}/)?.[0] ?? '';
    expect(rule).toMatch(/opacity/);
    expect(rule).not.toMatch(/transform|margin|height|top/);
    expect(rails).toContain('[data-rail-reveal]{animation:lumaRailReveal');
  });

  it('Home fades in with opacity and at most a few px of rise, and the splash background never animates', () => {
    expect(SPLASH_MOTION.homeRisePx).toBeLessThanOrEqual(8);
    expect(html).toMatch(/html\[data-splash\] #root \{ opacity: 0; \}/);
    expect(html).toMatch(/html\[data-splash="handoff"\] #root \{ opacity: 1; transition: opacity/);
    expect(html).not.toMatch(/#splash-bg[^{]*\{[^}]*animation/);
    expect(read('src/startup/splash.ts')).not.toMatch(/bg\.animate|splash-bg'\)\??\.animate/);
  });

  it('reduced motion: one plain crossfade, no rise, no entrance motion', () => {
    const block = html.slice(html.indexOf('@media (prefers-reduced-motion: reduce)'));
    expect(block).toMatch(/#splash-mark, #splash-uma \{ animation: lumaSplashFade/);
    expect(block).toMatch(/\[data-phone-rails\] \{ animation: none; \}/);
  });
});

describe('one color from the OS launch screen to Home', () => {
  const manifest = JSON.parse(readFileSync(root('static/manifest.json'), 'utf8'));
  it('the manifest, the meta theme color, the splash, Home and the server manifest all use the same color', () => {
    const c = SPLASH_MOTION.bg;
    expect(manifest.theme_color).toBe(c);
    expect(manifest.background_color).toBe(c);
    expect(read('index.html')).toContain(`<meta name="theme-color" content="${c}">`);
    expect(read('index.html')).toContain(`html, body, #root { margin: 0; background: ${c}; }`);
    expect(read('src/index.css')).toContain(`--color-kiosk-black: ${c}`);
    expect(readFileSync(root('app/settings_manager.py'), 'utf8')).toContain(`_STARTUP_THEME_COLOR = "${c}"`);
  });
});
