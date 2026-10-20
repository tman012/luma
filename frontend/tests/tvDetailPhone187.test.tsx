import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PhoneSeriesList } from '@/components/library/TvSeries';
afterEach(cleanup);

vi.mock('@/components/library/tvCalendarApi', async (orig) => ({
  ...(await orig<object>()),
  setSeriesMonitored: vi.fn(() => Promise.reject(new Error('net'))),
}));

describe('v187 phone series list', () => {
  it('reverts the bookmark when the save throws', async () => {
    const show = { id: 1, tmdb_id: 1, title: 'Severance', year: 2022, path_id: 'p', path_name: 'p', folder: 'f', monitored: false, have: 1, aired: 2 } as never;
    render(<PhoneSeriesList shows={[show]} onOpen={() => {}} onChanged={() => {}} />);
    const flag = screen.getByTestId('tv-phone-flag');
    expect(flag.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(flag);
    await waitFor(() => expect(screen.getByTestId('tv-phone-flag').getAttribute('aria-pressed')).toBe('false'));
    expect(screen.getByText('2022 · 1 of 2')).toBeTruthy();
  });
});

import { EpisodeSheet } from '@/components/library/TvSeriesDetail';

describe('v194 phone series filters and rail', () => {
  const mk = (id: number, title: string, have: number, aired: number, continuing = false) =>
    ({ id, tmdb_id: id, title, year: 2020, path_id: 'p', path_name: 'p', folder: 'f', monitored: true, have, aired, continuing }) as never;
  const shows = [mk(1, 'Andor', 1, 2), mk(2, 'The Bear', 3, 3, true), mk(3, 'Severance', 4, 4), mk(4, 'Shogun', 2, 2, true)];
  it('chips count by progress() and filter; the rail jumps to the first show of a letter', () => {
    const into = vi.fn();
    Element.prototype.scrollIntoView = into;
    render(<PhoneSeriesList shows={shows} onOpen={() => {}} onChanged={() => {}} />);
    expect(screen.getByTestId('tv-filter-all').textContent).toBe('All 4');
    expect(screen.getByTestId('tv-filter-missing').textContent).toBe('Missing 1');
    expect(screen.getByTestId('tv-filter-continuing').textContent).toBe('Continuing 2');
    fireEvent.click(screen.getByTestId('tv-filter-continuing'));
    expect(screen.getAllByTestId('tv-phone-row').map(r => r.dataset.showId)).toEqual(['2', '4']);
    fireEvent.click(screen.getByTestId('tv-filter-all'));
    fireEvent.click(screen.getByLabelText('S'));
    expect((into.mock.contexts[0] as HTMLElement).dataset.showId).toBe('3');
    into.mockClear();
    fireEvent.click(screen.getByLabelText('C'));   // no C: the next letter with a show (S)
    expect((into.mock.contexts[0] as HTMLElement).dataset.showId).toBe('3');
  });
});

describe('v194 EpisodeSheet (Reviewer)', () => {
  it('a parent re-render with a new onClose does not steal focus or re-lock scroll', () => {
    const props = { heading: 'S · S1E1', title: 'Pilot', sub: '', monitored: true, onSearch() {}, onInteractive() {}, onMonitor() {} };
    const { rerender } = render(<div><input data-testid="other" /><EpisodeSheet {...props} onClose={() => {}} /></div>);
    const other = screen.getByTestId('other');
    other.focus();
    rerender(<div><input data-testid="other" /><EpisodeSheet {...props} onClose={() => {}} /></div>);
    expect(document.activeElement).toBe(other);
    expect(document.body.style.overflow).toBe('hidden');
  });
});
