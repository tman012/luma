/** v185.1 (Taylor): the season card status bar (have/missing; unaired never counts; unmonitored does). */
import { describe, expect, it } from 'vitest';
import { SEASON_STATUS_COLOR, seasonStatus } from '@/components/library/TvSeriesDetail';
import type { DetailState } from '@/components/library/tvCalendarApi';

const ep = (state: DetailState, has_file = state === 'downloaded') => ({ state, has_file });

describe('seasonStatus', () => {
  it('green when every aired episode has a file (unaired ones ignored)', () => {
    expect(seasonStatus([ep('downloaded'), ep('downloaded'), ep('unaired'), ep('no_date')])).toBe('complete');
  });
  it('yellow when some aired have files and some are missing', () => {
    expect(seasonStatus([ep('downloaded'), ep('missing'), ep('unaired')])).toBe('partial');
    expect(seasonStatus([ep('downloaded'), ep('downloading')])).toBe('partial');
  });
  it('red when no aired episode has a file', () => {
    expect(seasonStatus([ep('missing'), ep('missing'), ep('unaired')])).toBe('none');
    expect(seasonStatus([ep('downloading')])).toBe('none');
  });
  it('blue (Unaired) with no aired episode yet, or no episodes', () => {
    expect(seasonStatus([ep('unaired'), ep('no_date')])).toBe('unaired');
    expect(seasonStatus([])).toBe('unaired');
  });
  it('an early file on an unaired episode counts as had', () => {
    expect(seasonStatus([{ state: 'unaired', has_file: true }, ep('unaired')])).toBe('complete');
  });
  it('uses the episode rows\u2019 colors', () => {
    expect(SEASON_STATUS_COLOR.complete).toContain('#22c55e');
    expect(SEASON_STATUS_COLOR.none).toContain('#ef4444');
    expect(SEASON_STATUS_COLOR.unaired).toContain('#3b82f6');
    expect(SEASON_STATUS_COLOR.partial).toContain('#fbbf24');
  });
});
