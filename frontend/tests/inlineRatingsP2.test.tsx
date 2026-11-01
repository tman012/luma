import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderHook, cleanup } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';

vi.mock('@/minimal', () => ({ isMinimal: () => true }));
import { useImdbRatingsState } from '@/hooks/useImdbRatings';
import { parseTmdbTvResponse, parsePlexResponse, parseTmdbResponse, type Ratings } from '@/utils/modalParsers';

const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const R = { imdb: { score: 8.7 }, rotten_tomatoes_critic: { score: 97 }, rotten_tomatoes_audience: null, metacritic: null, tmdb: null } as Ratings;

describe('ratings inline with details', () => {
  it('parsers keep the reply\'s ratings and partial flag; none when the reply has no field', () => {
    expect(parseTmdbTvResponse({ name: 'S', ratings: R, ratings_partial: true })).toMatchObject({ ratings: R, ratingsPartial: true });
    expect(parsePlexResponse({ title: 'A', ratings: null }).ratings).toBeNull();
    expect('ratings' in parseTmdbResponse({ title: 'A' })).toBe(false);
  });
  it('TV in the minimal build shows the inline ratings, without any ratings request', () => {
    const f = vi.fn(); vi.stubGlobal('fetch', f);
    const h = renderHook(() => useImdbRatingsState('Severance', 2022, R));
    expect(h.result.current).toEqual({ ratings: R, loading: false, doneFor: 'Severance' });
    expect(f).not.toHaveBeenCalled();
  });
  it('the movie sheet uses inline ratings and skips the Plex, OMDb and RT requests', () => {
    const s = src('hooks/useMovieDetail.ts');
    expect(s).toMatch(/if \(inline !== undefined\) \{\s*setRatings\(hasAnyRating\(inline\) \? inline : null\);\s*setRatingsLoading\(false\);\s*setRatingsSettled\(true\);\s*return;/);
    expect(src('components/media/MovieDetailModal/effectiveRatings.ts')).toMatch(/const needRt = !inline &&/);
    expect(src('components/media/MovieDetailModal/CompactLayout.tsx')).toMatch(/detail\?\.ratings !== undefined\)/);
  });
  it('background detail requests carry the warm id; a guarded reply is fetched again by the open', () => {
    const s = src('services/modalPrefetch.ts');
    expect(s).toMatch(/const bg = opts\.open \? undefined : warmHeaders/);
    expect(s).toMatch(/ratings_skipped":true/);
  });
});
