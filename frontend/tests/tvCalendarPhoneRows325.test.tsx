/** v325 phone (Taylor via PM): phone TV Calendar list rows drop the quality chip, the Downloaded chip and the
 * green check; the green/red left edge is the only got-it indicator. */
import { afterEach, describe, it, expect } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
afterEach(cleanup);
import { GLASS_DOT, PhoneDay } from '@/components/library/TvCalendar';
import { type CalEpisode, type Day } from '@/components/library/tvCalendarApi';

const ep = (o: Partial<CalEpisode>): CalEpisode => ({ episode_id: 1, series_id: 1, show: 'Industry', year: 2020, poster_path: null, season: 4, episode: 3,
  title: 'Dolphin', air_date: '2026-10-18', downloaded: false, quality: null, monitored: true, airstamp: '2026-10-18T16:00:00Z', ...o });
const DAY = '2026-10-18' as Day;

describe('v325 phone calendar rows', () => {
  const draw = () => render(<PhoneDay day={DAY} today={'2026-10-19' as Day} eps={[
    ep({ episode_id: 1, show: 'Have', downloaded: true, quality: '1080p WEB' }),
    ep({ episode_id: 2, series_id: 2, show: 'Unknown', downloaded: true, quality: null }),
    ep({ episode_id: 3, series_id: 3, show: 'Missing', downloaded: false }),
  ]} />);
  it('no quality or Downloaded chip, no check mark', () => {
    const { container } = draw();
    expect(screen.queryAllByTestId('tv-quality-chip')).toHaveLength(0);
    expect(container.textContent).not.toMatch(/1080p|Downloaded|In Library|\u2713|\u2714/);
    expect(container.querySelector('[data-testid*="check"]')).toBeNull();
  });
  it('v322 (option C): each card has one status dot at its top right: green when got it, amber when missing; no left edge', () => {
    draw();
    const rows = screen.getAllByTestId('tv-agenda-row');
    rows.forEach(r => { expect(within(r).getAllByTestId('tv-glass-dot')).toHaveLength(1); expect(within(r).queryByTestId('tv-stripe')).toBeNull(); });
    // PhoneDay sorts like sortDay (same time, then show name): Have, Missing, Unknown
    expect(rows.map(r => r.dataset.state)).toEqual(['downloaded', 'missing', 'downloaded']);
    const dot = (r: HTMLElement) => within(r).getByTestId('tv-glass-dot');
    const rgb = (h: string) => `rgb(${[1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)).join(', ')})`;
    expect(dot(rows[0]!).style.background).toBe(rgb(GLASS_DOT.downloaded));
    expect(dot(rows[1]!).style.background).toBe(rgb(GLASS_DOT.missing));
    expect(dot(rows[0]!).style.top).toBe('16px'); expect(dot(rows[0]!).style.right).toBe('16px');
    expect(rows[0]!.style.position).toBe('relative');
  });
});
