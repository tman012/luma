/** v346 (Tester, Escape from L.A. 390 cold: backdrop a frame+ after the content): the hero's latch is ONE commit.
 *  The logo-vs-text outcome (logoShown) is final in the latch render itself; nothing re-renders the sheet after the
 *  latch for a logo that is already loaded, so backdrop, logo and content paint in the same frame. A logo <img>
 *  not loaded at the latch still keeps the text (never neither). */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { renderHook, act, cleanup } from '@testing-library/react';
import { useSheetHero } from '@/hooks/useSheetBackdrop';
import { _resetImageLoader } from '@/loading/imageLoader';

const decodes = new Map<string, () => void>();
class FakeImage {
  private s = ''; onload: null | (() => void) = null; onerror: null | (() => void) = null; fetchPriority = ''; decoding = '';
  set src(v: string) { this.s = v; } get src() { return this.s; }
  decode = () => new Promise<void>((r) => { decodes.set(this.s, r); });
}
beforeEach(() => { decodes.clear(); _resetImageLoader(); vi.useFakeTimers(); vi.stubGlobal('Image', FakeImage); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const decode = async (u: string) => { await act(async () => { decodes.get(u)!(); await Promise.resolve(); await Promise.resolve(); }); };
const img = (src: string, loaded: boolean) => {
  const el = document.createElement('img'); el.setAttribute('src', src);
  Object.defineProperty(el, 'complete', { get: () => loaded }); Object.defineProperty(el, 'naturalWidth', { get: () => (loaded ? 300 : 0) });
  return el;
};

function mount(el: HTMLImageElement) {
  const attached = { done: false };
  const log: { state: string; shown: boolean; art: string | null }[] = [];
  const h = renderHook(() => {
    const r = useSheetHero('/art.jpg', true, '/logo.png', true, 'k');
    // The phone mounts the logo <img> in the latch commit: React attaches its ref before any layout effect runs.
    if (r.state === 'logo' && !attached.done) { attached.done = true; r.logoRef(el); }
    log.push({ state: r.state, shown: r.logoShown, art: r.art });
    return r;
  });
  return { h, log };
}

describe('v346: the hero latch is one commit (backdrop paints with the content)', () => {
  it('a loaded logo: the latch render already hides the text, and nothing renders again after it', async () => {
    const { log } = mount(img('/logo.png', true));
    await decode('/logo.png');
    await decode('/art.jpg');
    const first = log.findIndex((r) => r.state === 'logo');
    expect(first).toBeGreaterThanOrEqual(0);
    expect(log[first]).toEqual({ state: 'logo', shown: true, art: '/art.jpg' });
    const n = log.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    expect(log.length - first, 'no re-render after the latch').toBe(1);
    expect(log.length).toBe(n);
  });
  it('a logo <img> not loaded at the latch keeps the text title (never neither)', async () => {
    const { h } = mount(img('/logo.png', false));
    await decode('/logo.png'); await decode('/art.jpg');
    expect(h.result.current.state).toBe('logo');
    expect(h.result.current.logoShown).toBe(false);
  });
});
