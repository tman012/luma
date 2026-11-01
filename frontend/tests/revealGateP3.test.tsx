import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { renderHook, act } from '@testing-library/react';
import { SURFACE_DEADLINE_MS, deadlineLeft } from '@/loading/revealGate';
import { loadImage, isDecoded, _resetImageLoader, IMAGE_LOADER_MAX } from '@/loading/imageLoader';
import { useRatingsSettle } from '@/hooks/useRatingsSettle';
import { RATING_ICONS } from '@/utils/ratingIcons';
import { HOME_DECODE_CAP_MS } from '@/components/home/Rails';

const src = (p: string) => readFileSync(resolve(__dirname, '..', p), 'utf8');

describe('P3 reveal gate', () => {
  beforeEach(() => { vi.useFakeTimers(); _resetImageLoader(); });
  afterEach(() => { vi.useRealTimers(); });

  it('one shared deadline for sheets and Home', () => {
    expect(SURFACE_DEADLINE_MS).toBe(1000);
    expect(HOME_DECODE_CAP_MS).toBe(SURFACE_DEADLINE_MS);
    expect(deadlineLeft(1000, 1400)).toBe(600);
    expect(deadlineLeft(1000, 5000)).toBe(0);
    expect(src('src/hooks/useRatingsSettle.ts')).not.toMatch(/RATINGS_CAP_MS|RATINGS_ICON_CAP_MS/);
    // The sheet deadline counts from the tap when there was one.
    expect(src('src/hooks/useRatingsSettle.ts')).toMatch(/Date\.now\(\) - \(tap \?\? 0\)[\s\S]*deadlineLeft\(startedAt\)/);
  });

  it('reveals with what is in hand at the deadline, never locks empty', () => {
    const { result, rerender } = renderHook(({ v, s }) => useRatingsSettle(v, s, 'k', true),
      { initialProps: { v: null as never, s: false } });
    act(() => { vi.advanceTimersByTime(SURFACE_DEADLINE_MS + 10); });
    expect(result.current.shown).toBe(false);
    rerender({ v: { imdb: { score: 7.5 } } as never, s: false });
    expect(result.current.shown).toBe(false);   // v341: empty at the deadline waits for every source (one group)
    rerender({ v: { imdb: { score: 7.5 }, metacritic: { score: 90 } } as never, s: true });
    expect(result.current.shown).toBe(true);
    expect(result.current.ratings).toEqual({ imdb: { score: 7.5 }, metacritic: { score: 90 } });
  });

  it('a new open key (Fix Match) starts a new row', () => {
    const { result, rerender } = renderHook(({ v, k }) => useRatingsSettle(v, true, k, true),
      { initialProps: { v: { imdb: { score: 5 } } as never, k: 'a:1:0' } });
    act(() => { vi.advanceTimersByTime(SURFACE_DEADLINE_MS); });
    expect(result.current.ratings).toEqual({ imdb: { score: 5 } });
    rerender({ v: { imdb: { score: 8 } } as never, k: 'a:2:1' });
    act(() => { vi.advanceTimersByTime(SURFACE_DEADLINE_MS); });
    expect(result.current.ratings).toEqual({ imdb: { score: 8 } });
  });
});

describe('P3 image loader and bundled icons', () => {
  it('memory only: no Cache API, no storage, bounded', () => {
    const s = src('src/loading/imageLoader.ts');
    expect(s).not.toMatch(/caches\.|localStorage|indexedDB|sessionStorage/);
    expect(IMAGE_LOADER_MAX).toBeGreaterThan(0);
    expect(isDecoded('/x.png')).toBe(false);
    void loadImage('/x.png');
  });

  it('badge icons are bundled assets, not /static paths, and the SW is untouched', () => {
    for (const u of Object.values(RATING_ICONS)) expect(u).not.toMatch(/\/static\/images\//);
    expect(src('src/utils/ratingIcons.ts')).toMatch(/@\/assets\/ratings\/imdb-logo\.png/);
    expect(src('src/utils/ratingIcons.ts')).toMatch(/import \{ isDecoded, loadImage \} from '@\/loading\/imageLoader'/);
    expect(src('src/services/modalPrefetch.ts')).toMatch(/return loadImage\(url\)/);
  });

  it('the row dim sits on the slot, so text and icons share one opacity', () => {
    const s = src('src/components/media/RatingsSettle.tsx');
    expect(s).toMatch(/dim=\{false\}/);
    expect(s).toMatch(/visible \? SLOT_OPACITY : 0/);
  });

  it('Fix Match clears cached details and rekeys the row; season rows key by show and season', () => {
    const m = src('src/components/media/MovieDetailModal/index.tsx');
    expect(m).toMatch(/invalidateDetails\(\)/);
    expect(m).toMatch(/openKey: `\$\{ids\.ratingKey/);
    const t = src('src/components/media/TvDetailModal/ExpandedLayout.tsx');
    expect(t).toMatch(/seasonRatingsKey\(show, season\)/);
    expect(t).toMatch(/reserve=\{seasonRatingsRow \? RATINGS_SLOT_LG : 0\}/);
  });

  it('phone movie: a ratings source still out at compose (RT reviews) keeps the row reserved', () => {
    const c = src('src/components/media/MovieDetailModal/CompactLayout.tsx');
    expect(c).toMatch(/composed && \(ratingsLoading \|\| !effRatings\.settled \|\| hasRatings\)/);
  });
});
