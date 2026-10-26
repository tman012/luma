/** v295: Recently added TV — one card per show (FE dedupe). */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dedupeRecentTvRows } from '@/features/home/recentTvDedupe';

describe('v295 recent TV one card per show', () => {
  it('source: useHomeRails uses dedupeRecentTvRows', () => {
    const src = readFileSync('src/features/home/useHomeRails.ts', 'utf8');
    expect(src).toContain("from './recentTvDedupe'");
    expect(src).toContain('dedupeRecentTvRows');
    // old null-key push path gone
    expect(src).not.toContain('if (rk != null && seen.has(rk)) continue');
  });

  it('52 Bluey rows same show_rating_key → 1 card', () => {
    const eps = Array.from({ length: 52 }, (_, i) => ({
      show_rating_key: 695,
      show_title: 'Bluey',
      show_year: 2018,
      poster_url: '/p/695',
      episode: i + 1,
    }));
    const out = dedupeRecentTvRows(eps);
    expect(out).toHaveLength(1);
    expect(out[0].title).toBe('Bluey');
    expect(out[0].showRatingKey).toBe(695);
    expect(out[0].id).toBe('recent-tv-rk:695');
  });

  it('missing show_rating_key same title → 1 card', () => {
    const eps = Array.from({ length: 10 }, (_, i) => ({
      show_rating_key: null,
      show_title: 'Bluey',
      poster_url: null,
      episode: i + 1,
    }));
    const out = dedupeRecentTvRows(eps as Record<string, unknown>[]);
    expect(out).toHaveLength(1);
    expect(out[0].title).toBe('Bluey');
    expect(out[0].showRatingKey).toBeNull();
    expect(out[0].id).toBe('recent-tv-title:bluey');
  });

  it('two shows → 2 cards; skip rows with neither identity', () => {
    const eps = [
      { show_rating_key: 695, show_title: 'Bluey' },
      { show_rating_key: 695, show_title: 'Bluey' },
      { show_rating_key: 900, show_title: 'Andor' },
      { show_rating_key: null, show_title: '' },
      { show_rating_key: 0, show_title: '' },
    ];
    const out = dedupeRecentTvRows(eps as Record<string, unknown>[]);
    expect(out).toHaveLength(2);
    expect(out.map((x) => x.title)).toEqual(['Bluey', 'Andor']);
  });

  it('synthetic / negative show_rating_key falls back to title identity', () => {
    const out = dedupeRecentTvRows([
      { show_rating_key: -12345, show_title: 'Bluey' },
      { show_rating_key: -12345, show_title: 'Bluey' },
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe('recent-tv-title:bluey');
    expect(out[0].ratingKey).toBeNull();
  });
});
