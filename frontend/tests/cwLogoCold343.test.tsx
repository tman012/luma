/** cw-logo cold path: the server names the logo on the card; the tap starts that image at high priority even while
 *  the open's hold pauses background loads; the sheet's decoders reuse that same load (one download); and the sheet's
 *  logo lookup settles from the card's answer without a round trip. */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cardLogo, cardLogoOf } from '@/components/home/Rails';
import { openWhenReady, primeClearlogo, _resetModalPrefetch } from '@/services/modalPrefetch';
import { _resetModalColors } from '@/services/modalColors';
import { beginSheetPriority, sheetOpening } from '@/loading/sheetPriority';
import { loadImage } from '@/loading/imageLoader';
import { useTitleReveal } from '@/hooks/useTitleReveal';
import { useSheetHero } from '@/hooks/useSheetBackdrop';
import { useTvDetail } from '@/hooks/useTvDetail';
import { logoInput } from '@/hooks/useTitleReveal';
import type { Card } from '@/components/home/homeApi';

const RAW = 'https://image.tmdb.org/t/p/original/cw-logo.png';
const made: { src: string; prio?: string; resolve: () => void }[] = [];
class FakeImage {
  private s = ''; fetchPriority?: string; decoding = ''; onload: null | (() => void) = null; onerror: null | (() => void) = null;
  private r: () => void = () => {};
  private ready = new Promise<void>((res) => { this.r = res; });
  set src(v: string) { this.s = v; if (v) made.push({ src: v, prio: this.fetchPriority, resolve: this.r }); }
  get src() { return this.s; }
  removeAttribute() { this.s = ''; }
  decode = () => this.ready;
}
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });

beforeEach(() => { made.length = 0; _resetModalPrefetch(); _resetModalColors(); vi.stubGlobal('Image', FakeImage); vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {}))); });
afterEach(() => { vi.unstubAllGlobals(); _resetModalPrefetch(); });

describe('cw-logo: the logo known on the card', () => {
  it('a card the server resolved names the sheet logo and its lookup answer', () => {
    const c = { key: '4591', type: 'episode', show_key: '4501', tmdb_hint: 6191, clearlogo_url: RAW, title: 'S', subtitle: '', poster: null } as Card;
    expect(cardLogo(c)).toContain('cw-logo.png');
    expect(cardLogoOf(c, 'tv', 6191)).toEqual({ kind: 'tv', tmdbId: 6191, url: RAW });
    expect(cardLogoOf({ ...c, clearlogo_url: undefined, has_logo: false } as Card, 'tv', 6191)).toEqual({ kind: 'tv', tmdbId: 6191, url: null });
    expect(cardLogoOf({ ...c, clearlogo_url: undefined } as Card, 'tv', 6191)).toBeUndefined();
  });

  it('the tap starts the logo image at once at high priority, though the open hold is active', async () => {
    const logo = cardLogo({ clearlogo_url: RAW } as Card)!;
    vi.useFakeTimers();   // the open itself never runs here (only the tap's first, synchronous steps)
    void openWhenReady('tv', 4501, 6191, null, () => {}, undefined, [], { kind: 'tv', tmdbId: 6191, url: RAW });
    expect(sheetOpening()).toBe(true);                       // the hold that pauses low-priority loads
    const l = made.find((m) => m.src === logo);
    expect(l).toBeTruthy();
    expect(l!.prio).toBe('high');
    vi.useRealTimers();
  });

  it('the sheet decoders reuse that load (no second download) and resolve with it', async () => {
    const logo = '/api/img/tmdb?path=%2Fcw-logo.png&size=w300';
    void loadImage(logo, 'high');
    expect(made.filter((m) => m.src === logo)).toHaveLength(1);
    const t = renderHook(() => useTitleReveal(logo, 'k'));
    const h = renderHook(() => useSheetHero(null, true, logo, true, 'k', true));
    expect(made.filter((m) => m.src === logo)).toHaveLength(1);
    await act(async () => { made[0]!.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
    await waitFor(() => expect(t.result.current).toBe('logo'));
    await waitFor(() => expect(h.result.current.state).toBe('logo'));
    expect(made.filter((m) => m.src === logo)).toHaveLength(1);
  });

  it('a warm-up queued behind the hold is promoted to high priority by the sheet, not duplicated', async () => {
    const logo = '/api/img/tmdb?path=%2Fqueued.png&size=w300';
    beginSheetPriority();
    void loadImage(logo, 'low');                              // paused: nothing downloads during the hold
    expect(made.filter((m) => m.src === logo)).toHaveLength(0);
    renderHook(() => useTitleReveal(logo, 'k2'));
    expect(made.filter((m) => m.src === logo)).toHaveLength(1);
    expect(made.find((m) => m.src === logo)!.prio).toBe('high');
  });

  it('the sheet settles its logo from the card answer without a clearlogo request', async () => {
    const f = vi.fn(async (u: string) => {
      const url = String(u);
      if (url.includes('/api/plex/tv/4501')) return json({ title: 'Show', tmdb_id: 6191, cast: [] });
      if (url.includes('/api/tv/6191')) return json({ id: 6191, name: 'Show', videos: { results: [] } });
      return json({});
    });
    vi.stubGlobal('fetch', f);
    primeClearlogo({ kind: 'tv', tmdbId: 6191, url: RAW });
    const { result } = renderHook(() => useTvDetail(4501, 6191));
    await waitFor(() => expect(logoInput(result.current.show)).toContain('cw-logo.png'));
    expect(f.mock.calls.filter((c) => String(c[0]).includes('/api/clearlogo/')).length).toBe(0);
  });
});
