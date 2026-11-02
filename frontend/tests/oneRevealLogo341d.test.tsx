import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { renderHook, act, cleanup } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { useSheetHero, BACKDROP_CAP_MS } from '@/hooks/useSheetBackdrop';
import { openIds } from '@/services/searchApi';

const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
const decodes = new Map<string, () => void>();
class FakeImage {
  private s = ''; onload: null | (() => void) = null; onerror: null | (() => void) = null;
  set src(v: string) { this.s = v; } get src() { return this.s; }
  decode = () => new Promise<void>((r) => { decodes.set(this.s, r); });
}
beforeEach(() => { decodes.clear(); vi.useFakeTimers(); vi.stubGlobal('Image', FakeImage); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const decode = async (u: string) => { await act(async () => { decodes.get(u)!(); await Promise.resolve(); await Promise.resolve(); }); };
type P = { logo: string | null | undefined; settled: boolean };
const mount = (x: P) => renderHook(({ logo, settled }: P) => useSheetHero(null, true, logo, settled, 'k'), { initialProps: x });

describe('v341d: the clearlogo never pops in or moves the block', () => {
  it('the logo is decoded before the reveal: a known logo holds the title until it decodes', async () => {
    const h = mount({ logo: '/logo.png', settled: true });
    expect(h.result.current.state).toBe('hold');
    await decode('/logo.png');
    expect(h.result.current).toMatchObject({ state: 'logo', logo: '/logo.png' });
  });
  it('a logo from the late clearlogo fetch (after the deadline) never swaps in: text title for the open', () => {
    const h = mount({ logo: undefined, settled: false });
    act(() => { vi.advanceTimersByTime(BACKDROP_CAP_MS); });
    expect(h.result.current.state).toBe('text');
    h.rerender({ logo: '/late-logo.png', settled: true });
    expect(h.result.current).toMatchObject({ state: 'text', logo: null });
  });
  it('a logo URL known but decoding late: text at the deadline, the decode afterwards changes nothing', async () => {
    const h = mount({ logo: '/slow.png', settled: true });
    act(() => { vi.advanceTimersByTime(BACKDROP_CAP_MS); });
    expect(h.result.current.state).toBe('text');
    await decode('/slow.png');
    expect(h.result.current).toMatchObject({ state: 'text', logo: null });
  });
  it('meta offsetTop cannot depend on the logo: the title box has its final fixed height and the logo is laid out absolutely', () => {
    for (const k of ['MovieDetailModal', 'TvDetailModal']) {
      const s = src(`components/media/${k}/CompactLayout.tsx`);
      expect(s, k).toMatch(/data-title-box=""[\s\S]{0,400}height: '140px'/);
      expect(s, k).toMatch(/data-hero-title=\{sheetHero\.state\} style=\{\{ position: 'absolute', inset: 0/);
      expect(s, k).not.toMatch(/naturalHeight|onLoad=\{[^}]*setLogo/);
      expect(s, k).toMatch(/sheetHero\.deadline && !reveal\.revealed && !error && \(\s*<div data-sheet-spinner/);
    }
  });
  it('an owned title opens with its Plex key as well as the TMDb id (the Plex art path), movies too (v341i)', () => {
    expect(openIds(95396, 6003, 'tv')).toEqual({ ratingKey: 6003, tmdbId: 95396 });
    expect(openIds(95396, undefined, 'tv')).toEqual({ ratingKey: undefined, tmdbId: 95396 });
    expect(openIds(329865, 5003, 'movie')).toEqual({ ratingKey: 5003, tmdbId: 329865 });
  });
});

import { useRevealFreeze, TV_TEXT } from '@/hooks/useOneReveal';
describe('v341d: late TMDb text after the reveal changes nothing visible', () => {
  it('meta, overview and season count stay as revealed; ids and ownership stay live; a new open takes new text', () => {
    type S = { title: string; year: number; summary: string; numberOfSeasons: number; genres: string[]; isOwned: boolean; tmdbId?: number };
    const plex: S = { title: 'Severance', year: 2022, summary: 'Plex summary', numberOfSeasons: 1, genres: [], isOwned: true };
    const h = renderHook(({ v, r, k }: { v: S; r: boolean; k: string }) => useRevealFreeze(v, r, k, TV_TEXT as unknown as (keyof S)[]),
      { initialProps: { v: plex, r: false, k: 'a' } });
    h.rerender({ v: plex, r: true, k: 'a' });
    const first = h.result.current;
    h.rerender({ v: { ...plex, summary: 'TMDb overview', numberOfSeasons: 2, genres: ['Drama'], tmdbId: 95396 }, r: true, k: 'a' });
    expect(h.result.current).toMatchObject({ summary: 'Plex summary', numberOfSeasons: 1, genres: [], tmdbId: 95396 });
    expect(first).toMatchObject({ summary: 'Plex summary' });
    h.rerender({ v: { ...plex, summary: 'Other show' }, r: false, k: 'b' });
    h.rerender({ v: { ...plex, summary: 'Other show' }, r: true, k: 'b' });
    expect(h.result.current?.summary).toBe('Other show');
  });
  it('every layout renders the frozen details', () => {
    for (const [k, v] of [['TvDetailModal/CompactLayout', 'show'], ['TvDetailModal/ExpandedLayout', 'show'], ['MovieDetailModal/CompactLayout', 'detail'], ['MovieDetailModal/ExpandedLayout', 'detail']]) {
      expect(src(`components/media/${k}.tsx`), k).toMatch(new RegExp(`const ${v} = useRevealFreeze\\(live\\w+, reveal\\.revealed, props\\.openKey`));
    }
  });
});
describe('v341d: desktop keeps its sheet spinner until the one reveal (no blank between details and reveal)', () => {
  it('movie and TV expanded', () => {
    for (const k of ['MovieDetailModal', 'TvDetailModal']) {
      expect(src(`components/media/${k}/ExpandedLayout.tsx`), k).toMatch(/\{\(loading \|\| \(!reveal\.revealed && !error\)\) && \(/);
    }
  });
});
describe('v341e: the logo is held to the title box; the ratings slot is final when the details land', () => {
  it('logo fills the fixed box with object-fit contain, box clips, and the reserve is decided in the composing render', () => {
    for (const k of ['MovieDetailModal', 'TvDetailModal']) {
      const s = src(`components/media/${k}/CompactLayout.tsx`);
      expect(s, k).toMatch(/data-hero-logo="" [^\n]*\n\s*width=\{300\} height=\{140\}/);
      expect(s, k).toMatch(/height: '100%', maxWidth: '100%', maxHeight: '100%', objectFit: 'contain'/);
      expect(s, k).not.toMatch(/maxHeight: '200px'/);
      expect(s, k).toMatch(/data-title-box=""[\s\S]{0,400}overflow: 'hidden'/);
      expect(s, k).toMatch(/reserve=\{reserveRatings \|\| \(composed && \(/);
    }
  });
});
