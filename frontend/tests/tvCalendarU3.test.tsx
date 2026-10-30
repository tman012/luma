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
  it('v319 phone card: time on top, show, then SxxExx line, all centered; no count line, no chip', () => {
    render(<PhoneDay day={'2026-10-18' as Day} today={'2026-10-18' as Day} eps={[
      ep({ episode_id: 1, airstamp: '2026-10-19T01:00:00Z', downloaded: true, quality: '2160p WEB-DL' }),
      ep({ episode_id: 2, series_id: 2, show: 'Tracker', airstamp: '2026-10-19T01:00:00Z', downloaded: false, quality: '1080p WEB' }),
      ep({ episode_id: 3, series_id: 3, show: 'Untimed', airstamp: null, downloaded: true, quality: null }),
    ]} />);
    expect(screen.queryByTestId('tv-time-head')).toBeNull();
    expect(screen.queryByTestId('tv-day-count')).toBeNull();
    expect(screen.queryByText(/\d+ episodes?$/)).toBeNull();
    const rows = screen.getAllByTestId('tv-agenda-row');
    expect(rows).toHaveLength(3);
    // v322 (option C): the whole card is centered; the show and episode lines ellipsize on one line
    expect(rows[0]!.style.textAlign).toBe('center');
    for (const el of [within(rows[0]!).getByTestId('tv-card-show'), within(rows[0]!).getByTestId('tv-glass-ep-title')]) {
      expect(el.style.textOverflow).toBe('ellipsis');
      expect(el.style.whiteSpace).toBe('nowrap');
    }
    expect(within(rows[0]!).getByTestId('tv-glass-time').textContent).toBe('9:00 PM');
    expect(within(rows[0]!).getByTestId('tv-glass-code').textContent).toBe('S04E03');
    expect(screen.queryAllByTestId('tv-quality-chip')).toHaveLength(0);
    expect(within(rows[2]!).queryByTestId('tv-glass-time')).toBeNull();   // untimed: no time line
  });
  it('v319 r4 phone list sorts by air time (API order 8, 9, 6, 7 PM); untimed last', () => {
    const { container } = render(<PhoneDay day={'2026-10-18' as Day} today={'2026-10-18' as Day} eps={[
      ep({ episode_id: 1, series_id: 1, show: 'Eight', airstamp: '2026-10-19T00:00:00Z' }),
      ep({ episode_id: 2, series_id: 2, show: 'Untimed', airstamp: null }),
      ep({ episode_id: 3, series_id: 3, show: 'Nine', airstamp: '2026-10-19T01:00:00Z' }),
      ep({ episode_id: 4, series_id: 4, show: 'Six', airstamp: '2026-10-18T22:00:00Z' }),
      ep({ episode_id: 5, series_id: 5, show: 'Seven', airstamp: '2026-10-18T23:00:00Z' }),
      ep({ episode_id: 6, series_id: 6, show: 'Other day', air_date: '2026-10-19', airstamp: '2026-10-19T20:00:00Z' }),
    ]} />);
    const rows = within(container as HTMLElement).getAllByTestId('tv-agenda-row');
    expect(rows.map(r => within(r).getByTestId('tv-card-show').textContent)).toEqual(['Six', 'Seven', 'Eight', 'Nine', 'Untimed']);
    expect(rows.map(r => within(r).queryByTestId('tv-glass-time')?.textContent ?? '')).toEqual(['6:00 PM', '7:00 PM', '8:00 PM', '9:00 PM', '']);
  });
});
