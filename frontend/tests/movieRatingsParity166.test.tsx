import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { mergeRatings } from '@/components/media/MovieDetailModal/effectiveRatings';
import { buildRatingItems } from '@/utils/ratingIcons';

const dir = resolve(__dirname, '../src/components/media/MovieDetailModal');

describe('movie ratings parity (phone = web)', () => {
  it('keeps IMDb, RT and Metacritic from the ratings', () => {
    const r = mergeRatings({ imdb: { score: 8.3 }, rotten_tomatoes_critic: { score: 83 }, rotten_tomatoes_audience: { score: 94 }, metacritic: { score: 76 } } as never,
      { critic_score: 10, audience_score: 20 });
    expect(r?.rotten_tomatoes_critic?.score).toBe(83);
    expect(r?.rotten_tomatoes_audience?.score).toBe(94);
    expect(buildRatingItems(r!).length).toBe(4);
  });
  it('fills RT from the reviews endpoint when the ratings lack it', () => {
    const r = mergeRatings({ imdb: { score: 8.3 }, metacritic: { score: 76 } } as never, { critic_score: 83, audience_score: 94 });
    expect(r?.imdb?.score).toBe(8.3);
    expect(r?.metacritic?.score).toBe(76);
    expect(r?.rotten_tomatoes_critic?.score).toBe(83);
    expect(r?.rotten_tomatoes_audience?.score).toBe(94);
    expect(buildRatingItems(r!).length).toBe(4);
  });
  it('returns null when nothing is known', () => {
    expect(mergeRatings(null, null)).toBeNull();
    expect(mergeRatings({} as never, {})).toBeNull();
  });
  it('phone (CompactLayout) and web (ExpandedLayout) both use the shared merge', () => {
    const compact = readFileSync(resolve(dir, 'CompactLayout.tsx'), 'utf8');
    const expanded = readFileSync(resolve(dir, 'ExpandedLayout.tsx'), 'utf8');
    expect(compact).toMatch(/useEffectiveRatingsState\(ratings/);
    expect(compact).toMatch(/useRatingsSettle\(effRatings\.ratings/);   // v336: rendered through the settled row
    expect(compact).not.toMatch(/<RatingsBar ratings=\{ratings\}/);
    expect(expanded).toMatch(/mergeRatings\(ratings, reviewData\)/);
  });
});
