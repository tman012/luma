/** Mobile TV ratings: the phone sheet (TvDetailModal/CompactLayout) fetches through the
 * shared useImdbRatings hook and shows <RatingsBar size="sm"/> in the movie sheet's reserved slot. */
import { renderHook, waitFor } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useImdbRatings, useImdbRatingsState } from '@/hooks/useImdbRatings';

const read = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
const OMDB = { imdb: { score: 8.7, votes: 1000 }, rotten_tomatoes_critic: { score: 97 }, rotten_tomatoes_audience: null, metacritic: { score: 88 } };

describe('shared TV ratings hook', () => {
  afterEach(() => { (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__ = undefined; vi.unstubAllGlobals(); });

  it('fetches /api/ratings/<title>?year= and returns IMDb/RT/Metacritic (never TMDb); loading until then', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify(OMDB), { status: 200 }));
    vi.stubGlobal('fetch', f);
    const { result } = renderHook(() => useImdbRatingsState('Severance', 2022));
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.ratings?.imdb?.score).toBe(8.7));
    expect(result.current.loading).toBe(false);
    expect(result.current.ratings?.tmdb).toBeNull();
    expect(String((f.mock.calls[0] as unknown[])[0])).toMatch(/\/api\/ratings\/Severance\?year=2022$/);
  });

  it('no scores or an error is no ratings', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ imdb: null }), { status: 200 })));
    const { result } = renderHook(() => useImdbRatingsState('Nothing', null));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.ratings).toBeNull();
  });

  it('off in the minimal build (no fetch)', () => {
    (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__ = true;
    const f = vi.fn();
    vi.stubGlobal('fetch', f);
    const { result } = renderHook(() => useImdbRatings('Severance', 2022));
    expect(result.current).toBeNull();
    expect(f).not.toHaveBeenCalled();
  });
});

describe('both TV layouts use it; the phone sheet renders the bar', () => {
  it('ExpandedLayout imports the shared hook (no private copy)', () => {
    const s = read('components/media/TvDetailModal/ExpandedLayout.tsx');
    expect(s).toContain("import { useImdbRatingsState, ratingsDone } from '@/hooks/useImdbRatings';");
    expect(s).not.toMatch(/function useImdbRatings\(/);
  });
  it('CompactLayout: RatingsBar sm in the reserved slot, after the meta line and before the actions (as the movie sheet)', () => {
    const s = read('components/media/TvDetailModal/CompactLayout.tsx');
    expect(s).toContain("useImdbRatingsState(show?.title, show?.year, show?.ratings)");
    const slot = s.indexOf('<RatingsSettle settle={ratingsSettle} size="sm" reserve={reserveRatings ? RATINGS_SLOT_SM : 0} />');   // v336: the settled row
    expect(slot).toBeGreaterThan(0);
    expect(s).toContain('useRatingsSettle(ratingsState.ratings');
    expect(s.lastIndexOf('metaItems.map', slot)).toBeGreaterThan(0);
    expect(s.indexOf('<div data-modal-actions>', slot)).toBeGreaterThan(slot);
    expect(s).toContain('if (composed && (ratingsLoading || hasRatings)) setReserveRatings(true);');
  });
});
