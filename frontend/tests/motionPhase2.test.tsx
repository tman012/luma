/** Phase 2 motion: the remaining surfaces (Home pills, Vibe pages, rails, search, and the Storage, Activity, Calendar and
 *  Rooms sheets) take every duration and easing from the shared tokens; nothing overshoots; scrims animate opacity only;
 *  reduced motion is honored; the detail sheets' below-the-fold sections are staged but still render. */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SimpleSheet } from '@/components/layout/SimpleSheet';
import { StagedBox, StagedProvider } from '@/components/motion/useStagedMount';
import { MOTION, SHEET_MOTION, staggerMs } from '@/theme/motion';
import { afterNextFrame, yieldToMain } from '@/utils/scheduling';

const SRC = join(__dirname, '../src');
const read = (p: string) => readFileSync(join(SRC, p), 'utf8');

const SURFACES: Record<string, string[]> = {
  'Home pills': ['components/home/HomePills.tsx'],
  'Storage sheet': ['components/storage/StoragePill.tsx'],
  'Activity sheet': ['components/activity/ActivitySheet.tsx', 'components/activity/ActivityFeed.tsx'],
  'Calendar sheet': ['components/calendar/TvCalendarSheet.tsx', 'components/library/TvCalendar.tsx'],
  'Rooms sheet': ['components/rooms/RoomsSheet.tsx', 'components/rooms/FullScreenRemote.tsx', 'components/rooms/RoomsSettings.tsx',
    'components/rooms/PlayIn.tsx', 'components/rooms/roomIcons.tsx'],
  'Shared sheet shell': ['components/layout/SimpleSheet.tsx', 'components/motion/useStagedMount.ts', 'utils/scheduling.ts'],
  Search: ['components/search/HomeSearchPalette.tsx', 'components/search/HomeSearchR3.tsx', 'components/search/SearchShelf.tsx',
    'components/search/KioskSearch.tsx', 'components/mobile/MobileSearchOverlay.tsx'],
  'Vibe pages': ['pages/VibeEnginePage.tsx', 'pages/DescribeItPage.tsx', 'pages/vibe/DescribeMode.tsx', 'pages/vibe/DescribeResultsCarousel.tsx',
    'pages/vibe/ExploreVisuallyMode.tsx', 'pages/vibe/FeelSomethingMode.tsx', 'pages/vibe/MobileDescribeResults.tsx',
    'pages/vibe/PickForMeMode.tsx', 'components/media/VibeEngineModal.tsx', 'hooks/useSwipeCarousel.ts'],
  Rails: ['components/home/Rails.tsx', 'components/mobile/MobileRail.tsx', 'components/mobile/MobilePosterCard.tsx',
    'components/media/ExploreTile.tsx', 'components/media/ExplorePosterTile.tsx', 'components/media/ExploreCard.tsx'],
};
const ALL = Object.entries(SURFACES).flatMap(([name, files]) => files.map((f) => [name, f] as const));

/** A literal time or easing in a transition or animation: in a quoted style value, or in CSS text. Infinite loops
 *  (spinners, ambient orbits) are loops, not motion, and `0s` is the visibility switch. */
const LITERAL = /(?:(?<![\w.$)}-])\d*\.?\d+m?s\b|\bcubic-bezier\(|\bease(?:-in|-out|-in-out)?\b)/;
const TW_LITERAL = /\b(?:duration-(?:\d|\[)|ease-(?:in|out|in-out|linear)\b|active:scale-(?:\d|\[)|(?:group-)?hover:scale-(?:\d|\[)|transition-\[width\])/;
const clean = (v: string) => v.replace(/var\([^)]*\)/g, '').replace(/\$\{[^}]*\}/g, '').replace(/\b0s\b/g, '');
function literalTimings(src: string): string[] {
  const hits: string[] = [];
  src.split('\n').forEach((line, i) => {
    if (/^\s*(\/\/|\*|\/\*)/.test(line)) return;
    const m = /\b(?:transition|animation)[A-Za-z]*['"]?\s*[:=]\s*(.*)$/.exec(line);
    if (m && !/infinite/.test(m[1]!)) {
      for (const lit of m[1]!.match(/(['"`])(?:(?!\1).)*\1/g) ?? []) if (LITERAL.test(clean(lit.slice(1, -1)))) hits.push(`${i + 1}: ${line.trim().slice(0, 120)}`);
    }
    const css = /\b(?:transition|animation)(?:-duration)?\s*:\s*([^;}`'"]+)/g;
    for (let c = css.exec(line); c; c = css.exec(line)) if (!/infinite/.test(c[1]!) && LITERAL.test(clean(c[1]!))) hits.push(`${i + 1}: ${line.trim().slice(0, 120)}`);
  });
  return hits;
}
const twLiterals = (src: string) => src.split('\n').flatMap((l, i) => (/^\s*(\/\/|\*|\/\*)/.test(l) || /infinite/.test(l) || !TW_LITERAL.test(l) ? [] : [`${i + 1}: ${l.trim().slice(0, 120)}`]));

describe('Phase 2 token usage per surface', () => {
  it.each(ALL)('%s: %s has no literal duration or easing', (_s, f) => {
    expect(literalTimings(read(f))).toEqual([]);
    expect(twLiterals(read(f))).toEqual([]);
  });

  it('the scanners see a literal (so the checks above can fail)', () => {
    expect(literalTimings("style={{ transition: 'opacity 0.3s ease' }}")).toHaveLength(1);
    expect(literalTimings('.a{animation:x 180ms ease-out}')).toHaveLength(1);
    expect(literalTimings('.a{animation:x var(--m-dur-standard) var(--m-ease-decel) both}')).toHaveLength(0);
    expect(literalTimings("style={{ animation: `x var(--m-dur-fast) ${staggerMs(2)}ms var(--m-ease-decel) both` }}")).toHaveLength(0);
    expect(literalTimings("style={{ animation: 'spin 0.8s linear infinite' }}")).toHaveLength(0);
    expect(twLiterals('className="transition-all duration-300 active:scale-95"')).toHaveLength(1);
    expect(twLiterals('className="transition-all duration-(--m-dur-fast) active:scale-(--m-press-scale)"')).toHaveLength(0);
  });

  it('nothing in any surface overshoots: no easing with a control point past 0..1, no scale keyframe past 1', () => {
    for (const [, f] of ALL) {
      const src = read(f);
      for (const m of src.matchAll(/cubic-bezier\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)/g)) {
        for (const y of [Number(m[2]), Number(m[4])]) { expect(y, f).toBeGreaterThanOrEqual(0); expect(y, f).toBeLessThanOrEqual(1); }
      }
      expect(src, f).not.toMatch(/@keyframes\s+(?:cardReveal|vibeEnter|vibeCardIn|fadeSlideUp|mobileCardReveal|shelfSlide)[^}]*scale\(1\.\d/);
    }
    expect(read('index.css')).not.toMatch(/\.explore-tile[\s\S]{0,200}cubic-bezier\(0\.34,\s*1\.56/);
  });

  it('every sheet shell of the Home sheets is the shared SimpleSheet, with no phase machine of its own', () => {
    for (const f of ['components/storage/StoragePill.tsx', 'components/activity/ActivitySheet.tsx', 'components/calendar/TvCalendarSheet.tsx',
      'components/rooms/RoomsSheet.tsx', 'components/rooms/FullScreenRemote.tsx', 'pages/DescribeItPage.tsx']) {
      const src = read(f);
      expect(src, f).toContain('<SimpleSheet');
      expect(src, f).not.toMatch(/useState<'entering'|SheetPhase|const SPRING|ENTER_DURATION|EXIT_DURATION|createPortal/);
    }
  });

  it('the pills and rails use the shared state classes, not a touch-start pressed state', () => {
    expect(read('components/home/HomePills.tsx')).toContain('m-interactive');
    expect(read('components/home/HomePills.tsx')).not.toMatch(/onTouchStart|setPressed/);
    expect(read('components/storage/StoragePill.tsx')).toContain('m-interactive');
    expect(read('components/home/Rails.tsx')).toContain('m-interactive');
  });

  it('staggers come from the one helper: 30 ms steps, capped at 240 ms', () => {
    expect([0, 1, 2, 7, 8, 40].map(staggerMs)).toEqual([0, 30, 60, 210, 240, 240]);
    for (const f of ['pages/vibe/FeelSomethingMode.tsx', 'pages/vibe/PickForMeMode.tsx', 'pages/vibe/MobileDescribeResults.tsx', 'pages/VibeEnginePage.tsx', 'components/mobile/MobileRail.tsx'])
      expect(read(f), f).toContain('staggerMs(');
  });
});

describe('Phase 2 scrims animate opacity only', () => {
  it('no scrim, backdrop or overlay of the surfaces blurs or animates a color or a layout property', () => {
    const palette = read('components/search/HomeSearchPalette.tsx');
    const scrimCss = palette.slice(palette.indexOf('.lsp-scrim{'));
    expect(scrimCss).not.toMatch(/backdrop-filter/);
    const r3 = read('components/search/HomeSearchR3.tsx');
    expect(r3.slice(r3.indexOf('.r3-backdrop{'), r3.indexOf('.r3-panel{'))).not.toMatch(/backdrop-filter/);
    expect(r3).not.toMatch(/html\[data-luma-modal\] \.r3-backdrop/);
    expect(read('pages/VibeEnginePage.tsx')).not.toMatch(/bg-kiosk-black\/\d+ backdrop-blur/);
    for (const f of ['components/layout/SimpleSheet.tsx', 'components/rooms/RoomsSheet.tsx', 'components/storage/StoragePill.tsx',
      'components/activity/ActivitySheet.tsx', 'components/calendar/TvCalendarSheet.tsx', 'hooks/useSwipeCarousel.ts']) {
      const src = read(f);
      expect(src, f).not.toMatch(/backdrop-?[fF]ilter|backdrop-blur/);
      expect(src, f).not.toMatch(/backgroundColor:\s*(?:phase|sheetPhase)\b/);   // a scrim that fades its color per phase
    }
    expect(read('components/rooms/RoomsSheet.tsx')).not.toMatch(/filter:\s*remoteTarget|brightness\(/);
    for (const f of ['components/rooms/PlayIn.tsx', 'components/rooms/roomIcons.tsx', 'components/rooms/RoomsSettings.tsx']) expect(read(f), f).not.toMatch(/backdropFilter/);
  });

  it('rows and bars move on transform, not on width, bottom or height', () => {
    const feed = read('components/activity/ActivityFeed.tsx');
    expect(feed).not.toMatch(/transition[^\n]*(?:width|bottom|height|left|top)/);
    expect(read('components/storage/StoragePill.tsx')).toContain('scaleX(');
    expect(read('components/rooms/RoomsSheet.tsx')).not.toContain('transition-[width]');
    expect(read('components/rooms/FullScreenRemote.tsx')).not.toContain('transition-[width]');
  });
});

describe('SimpleSheet', () => {
  let animations: { el: Element; keyframes: Keyframe[]; options: KeyframeAnimationOptions }[];
  beforeEach(() => {
    animations = [];
    (HTMLElement.prototype as unknown as { animate: unknown }).animate = function (this: Element, keyframes: Keyframe[], options: KeyframeAnimationOptions) {
      animations.push({ el: this, keyframes, options });
      return { finished: Promise.resolve(), cancel() {}, onfinish: null } as unknown as Animation;
    };
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); delete (HTMLElement.prototype as unknown as { animate?: unknown }).animate; vi.restoreAllMocks(); });

  const mount = (props: Partial<React.ComponentProps<typeof SimpleSheet>> = {}) => {
    const onClosed = vi.fn();
    render(<SimpleSheet name="TestSheet" onClosed={onClosed} {...props}><div data-sheet-handle>Grab</div><p>Body</p></SimpleSheet>);
    return onClosed;
  };

  it('opens on the sheet motion: the panel slides on transform and the scrim fades on opacity, in the same frame', () => {
    mount();
    const panel = document.querySelector('[data-sheet-panel]')!;
    const scrim = document.querySelector('[data-sheet-scrim]')!;
    const p = animations.find((a) => a.el === panel)!;
    const s = animations.find((a) => a.el === scrim)!;
    expect(p.options.duration).toBe(SHEET_MOTION.enterMs);
    expect(JSON.stringify(p.keyframes[0])).toContain('translate3d(0, 100%, 0)');
    expect(Object.keys(s.keyframes[0]!)).toEqual(['opacity']);
    expect(s.options.duration).toBe(SHEET_MOTION.scrimEnterMs);
    expect(document.querySelector('[data-sheet="TestSheet"]')).toBeTruthy();
  });

  it('a scrim tap, Escape or the close api plays the shared exit once, then calls onClosed once', async () => {
    vi.useFakeTimers();
    const onClosed = mount();
    fireEvent.click(document.querySelector('[data-sheet-scrim]')!);
    fireEvent.keyDown(window, { key: 'Escape' });
    const exits = animations.filter((a) => a.options.duration === SHEET_MOTION.exitMs);
    expect(exits.length).toBe(2);   // the panel and the scrim, once
    expect(onClosed).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(MOTION.dur.sheetExit + 50); });
    expect(onClosed).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[data-sheet="TestSheet"]')).toBeNull();
  });

  it('a guard takes the dismiss first (a layer inside closes before the sheet); scrimDismiss=false ignores the scrim', async () => {
    vi.useFakeTimers();
    let layerOpen = true;
    const onClosed = mount({ guard: () => { const was = layerOpen; layerOpen = false; return was; }, scrimDismiss: false });
    fireEvent.keyDown(window, { key: 'Escape' });
    await act(async () => { await vi.advanceTimersByTimeAsync(MOTION.dur.sheetExit + 50); });
    expect(onClosed).not.toHaveBeenCalled();
    fireEvent.click(document.querySelector('[data-sheet-scrim]')!);
    await act(async () => { await vi.advanceTimersByTimeAsync(MOTION.dur.sheetExit + 50); });
    expect(onClosed).not.toHaveBeenCalled();
    fireEvent.keyDown(window, { key: 'Escape' });
    await act(async () => { await vi.advanceTimersByTimeAsync(MOTION.dur.sheetExit + 50); });
    expect(onClosed).toHaveBeenCalledTimes(1);
  });

  it('locks the page scroll while open and restores it', () => {
    document.body.style.overflow = 'auto';
    mount();
    expect(document.body.style.overflow).toBe('hidden');
    cleanup();
    expect(document.body.style.overflow).toBe('auto');
  });

  it('a drag on the handle moves the panel and fades the scrim with it; a long drag closes, a short one springs back', async () => {
    vi.useFakeTimers();
    const onClosed = mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    const panel = document.querySelector('[data-sheet-panel]') as HTMLElement;
    const scrim = document.querySelector('[data-sheet-scrim]') as HTMLElement;
    const handle = screen.getByText('Grab');
    fireEvent.touchStart(handle, { touches: [{ clientY: 100 }] });
    fireEvent.touchMove(handle, { touches: [{ clientY: 160 }] });
    expect(panel.style.transform).toBe('translate3d(0, 60px, 0)');
    expect(Number(scrim.style.opacity)).toBeLessThan(1);
    fireEvent.touchEnd(handle);
    expect(panel.style.transform).toBe('');
    expect(panel.style.transition).toContain('transform');
    fireEvent.touchStart(handle, { touches: [{ clientY: 100 }] });
    fireEvent.touchMove(handle, { touches: [{ clientY: 400 }] });
    fireEvent.touchEnd(handle);
    await act(async () => { await vi.advanceTimersByTimeAsync(MOTION.dur.sheetExit + 50); });
    expect(onClosed).toHaveBeenCalledTimes(1);
  });

  it('reduced motion: the panel only fades (no transform keyframe), over the short token', () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: /reduce/.test(q), media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false }));
    mount();
    const panel = document.querySelector('[data-sheet-panel]')!;
    const p = animations.find((a) => a.el === panel)!;
    expect(Object.keys(p.keyframes[0]!)).toEqual(['opacity']);
    expect(p.options.duration).toBe(MOTION.dur.fast);
    vi.unstubAllGlobals();
  });
});

describe('Phase 2 reduced motion in the surfaces', () => {
  it('the global rules zero the rise, the route shift and the hover and press scales, and stop ambient loops', () => {
    const css = read('index.css');
    const reduced = css.slice(css.indexOf('@media (prefers-reduced-motion: reduce) {\n  :root {'));
    expect(reduced).toMatch(/--m-rise: 0px/);
    expect(reduced).toMatch(/--m-press-scale: 1/);
    expect(reduced).toMatch(/--m-hover-scale: 1/);
    expect(css).toMatch(/\[style\*="infinite"\]:not\(\[style\*="spin" i\]\)\s*\{\s*animation: none !important/);
    expect(css).toMatch(/\.explore-tile:hover\s*\{\s*transform: scale\(var\(--m-hover-scale\)\)/);
  });

  it('the search palette stops its enter and exit animations, and the R3 modal goes instant', () => {
    const palette = read('components/search/HomeSearchPalette.tsx');
    expect(palette).toMatch(/prefers-reduced-motion: reduce\)\{\s*\.lsp-scrim,\.lsp-panel\{animation:none\}/);
    expect(read('components/search/HomeSearchR3.tsx')).toContain('@media (prefers-reduced-motion: reduce){[data-r3-search]{--a1-dur:0ms}}');
  });

  it('the Rooms pairing loops and the Vibe ambient orbits are covered by a reduced-motion rule', () => {
    expect(read('components/rooms/RoomsSettings.tsx')).toMatch(/prefers-reduced-motion: reduce\)\{\.room-loop\{animation:none!important\}\}/);
    expect(read('index.css')).toContain('[style*="infinite"]');
  });
});

describe('scheduling helpers', () => {
  it('yieldToMain resolves in a later task, before a zero timer is required', async () => {
    const order: string[] = [];
    queueMicrotask(() => order.push('micro'));
    const p = yieldToMain().then(() => order.push('yield'));
    order.push('sync');
    await p;
    expect(order).toEqual(['sync', 'micro', 'yield']);
  });

  it('afterNextFrame runs after a frame and a task hop, and can be cancelled', async () => {
    const fn = vi.fn();
    const cancel = afterNextFrame(fn);
    expect(fn).not.toHaveBeenCalled();
    await new Promise((r) => setTimeout(r, 80));
    expect(fn).toHaveBeenCalledTimes(1);
    const fn2 = vi.fn();
    afterNextFrame(fn2)();
    cancel();
    await new Promise((r) => setTimeout(r, 80));
    expect(fn2).not.toHaveBeenCalled();
  });
});

describe('deferred detail sections still render', () => {
  afterEach(cleanup);
  it('a staged box holds its reserved height, then mounts its content and marks itself shown', async () => {
    render(<StagedProvider total={1} enabled resetKey="a"><StagedBox at={1} reserve={170}><span>Cast list</span></StagedBox></StagedProvider>);
    const box = document.querySelector('[data-staged]') as HTMLElement;
    expect(box.dataset.staged).toBe('pending');
    expect(box.style.minHeight).toBe('170px');
    expect(screen.queryByText('Cast list')).toBeNull();
    await act(async () => { await new Promise((r) => setTimeout(r, 120)); });
    expect(screen.getByText('Cast list')).toBeTruthy();
    expect((document.querySelector('[data-staged]') as HTMLElement).dataset.staged).toBe('shown');
    expect((document.querySelector('[data-staged]') as HTMLElement).style.minHeight).toBe('');
  });

  it('it waits while the provider is not enabled, and a new key stages again', async () => {
    const view = (enabled: boolean, k: string) => (
      <StagedProvider total={1} enabled={enabled} resetKey={k}><StagedBox at={1} reserve={50}><span>Later</span></StagedBox></StagedProvider>
    );
    const r = render(view(false, 'a'));
    await act(async () => { await new Promise((x) => setTimeout(x, 120)); });
    expect(screen.queryByText('Later')).toBeNull();
    r.rerender(view(true, 'a'));
    await act(async () => { await new Promise((x) => setTimeout(x, 120)); });
    expect(screen.getByText('Later')).toBeTruthy();
    r.rerender(view(true, 'b'));
    expect((document.querySelector('[data-staged]') as HTMLElement).dataset.staged).toBe('pending');
    await act(async () => { await new Promise((x) => setTimeout(x, 120)); });
    expect(screen.getByText('Later')).toBeTruthy();
  });

  it('outside a provider a box renders at once (a layout under test)', () => {
    render(<StagedBox at={1} reserve={50}><span>Now</span></StagedBox>);
    expect(screen.getByText('Now')).toBeTruthy();
  });

  it('both phone layouts stage their cast rail inside a provider and keep a reserved box', () => {
    for (const f of ['components/media/MovieDetailModal/CompactLayout.tsx', 'components/media/TvDetailModal/CompactLayout.tsx']) {
      const src = read(f);
      expect(src, f).toMatch(/<StagedProvider total=\{1\}/);
      expect(src, f).toMatch(/<StagedBox at=\{1\} reserve=\{CAST_RAIL_RESERVE\}/);
      expect(src, f).toMatch(/const CAST_RAIL_RESERVE = \d+/);
    }
  });
});
