import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { PhoneDay, etParts } from '@/components/library/TvCalendar';
import type { CalEpisode, Day } from '@/components/library/tvCalendarApi';

const ep = (o: Partial<CalEpisode>): CalEpisode => ({ episode_id: 1, series_id: 1, show: 'Industry', year: 2020, poster_path: null, season: 4, episode: 3,
  title: 'Dolphin', air_date: '2026-10-18', downloaded: false, quality: null, monitored: true, airstamp: null, ...o });

describe('v211 U3 phone rows', () => {
  it('splits the ET time; untimed is null', () => {
    expect(etParts('2026-10-19T01:00:00Z')).toEqual(['9:00', 'PM']);
    expect(etParts(null)).toBeNull();
  });
  it('time column centered, no T2 headers, chip only when downloaded', () => {
    render(<PhoneDay day={'2026-10-18' as Day} today={'2026-10-18' as Day} eps={[
      ep({ episode_id: 1, airstamp: '2026-10-19T01:00:00Z', downloaded: true, quality: '2160p WEB-DL' }),
      ep({ episode_id: 2, show: 'Tracker', airstamp: '2026-10-19T01:00:00Z', downloaded: false, quality: '1080p WEB' }),
      ep({ episode_id: 3, show: 'Untimed', airstamp: null }),
    ]} />);
    expect(screen.queryByTestId('tv-time-head')).toBeNull();
    const rows = screen.getAllByTestId('tv-agenda-row');
    expect(rows).toHaveLength(3);
    const col = within(rows[0]!).getByTestId('tv-u3-time');
    expect(col.style.justifyContent).toBe('center');
    expect(col.style.alignSelf).toBe('center');
    expect(rows[0]!.style.alignItems).toBe('center');
    expect(col.textContent).toBe('9:00PM');
    expect(within(rows[0]!).getByTestId('tv-quality-chip').textContent).toBe('2160p WEB-DL');
    expect(within(rows[1]!).queryByTestId('tv-quality-chip')).toBeNull();
    expect(within(rows[2]!).getByTestId('tv-u3-time').textContent).toBe('');
  });
});
