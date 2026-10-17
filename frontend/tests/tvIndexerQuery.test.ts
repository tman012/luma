import { describe, it, expect } from 'vitest';
import { tvIndexerQuery } from '@/components/media/TvDetailModal/useTvSourcesData';

describe('tvIndexerQuery', () => {
  it('adds SxxEyy for an episode and never the year', () => {
    expect(tvIndexerQuery('Breaking Bad', { type: 'episode', season: 1, episode: 5 })).toBe('Breaking Bad S01E05');
  });
  it('adds Sxx for a season', () => {
    expect(tvIndexerQuery('Breaking Bad', { type: 'season', season: 3 })).toBe('Breaking Bad S03');
  });
  it('is empty without a title', () => {
    expect(tvIndexerQuery('', { type: 'season', season: 1 })).toBe('');
  });
});
