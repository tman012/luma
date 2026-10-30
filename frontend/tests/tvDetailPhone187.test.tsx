import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import { PhoneSeriesList } from '@/components/library/TvSeries';
afterEach(cleanup);


import { EpisodeSheet } from '@/components/library/TvSeriesDetail';

describe('v194 phone series rail (v226: no filter chips)', () => {
  const mk = (id: number, title: string, have: number, aired: number, continuing = false) =>
    ({ id, tmdb_id: id, title, year: 2020, path_id: 'p', path_name: 'p', folder: 'f', monitored: true, have, aired, continuing }) as never;
  const shows = [mk(1, 'Andor', 1, 2), mk(2, 'The Bear', 3, 3, true), mk(3, 'Severance', 4, 4), mk(4, 'Shogun', 2, 2, true)];
  it('the rail jumps to the first show of a letter', () => {
    const into = vi.fn();
    Element.prototype.scrollIntoView = into;
    render(<PhoneSeriesList shows={shows} onOpen={() => {}} onChanged={() => {}} />);
    expect(screen.queryByTestId('tv-series-filters')).toBeNull();
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

import { DayHeader, PhoneDay } from '@/components/library/TvCalendar';

describe('v197 phone day header A', () => {
  it('shows Today or the weekday bold with the date under it; ‹ and › step one day', () => {
    const onStep = vi.fn();
    const { rerender } = render(<DayHeader day="2026-10-18" today="2026-10-18" onStep={onStep} />);
    expect(screen.getByTestId('tv-day-title').textContent).toBe('Today');
    expect(screen.getByTestId('tv-day-date').textContent).toBe('Sun, Oct 18');
    rerender(<DayHeader day="2026-10-19" today="2026-10-18" onStep={onStep} />);
    expect(screen.getByTestId('tv-day-title').textContent).toBe('Monday');
    fireEvent.click(screen.getByTestId('tv-day-prev'));
    fireEvent.click(screen.getByTestId('tv-day-next'));
    expect(onStep.mock.calls).toEqual([[-1], [1]]);
    expect(screen.queryByTestId('tv-week-strip')).toBeNull();
  });
  it('no count and no date heading in the day list; an empty day says No episodes', () => {
    render(<PhoneDay eps={[]} day="2026-10-18" today="2026-10-18" />);
    expect(screen.getByTestId('tv-cal-empty').textContent).toBe('No episodes');
    expect(document.querySelector('h4')).toBeNull();
  });
});

import { QualityChip } from '@/components/library/TvCalendar';

describe('v197 quality chip (Q2)', () => {
  it('a downloaded row shows its file quality in the --quality-chip color; no file, no chip', () => {
    const { rerender } = render(<QualityChip e={{ downloaded: true, quality: '1080p WEB' }} />);
    const chip = screen.getByTestId('tv-quality-chip');
    expect(chip.textContent).toBe('1080p WEB');
    expect(chip.style.color).toContain('var(--quality-chip)');
    expect(parseInt(chip.style.fontSize, 10)).toBeGreaterThanOrEqual(12);
    rerender(<QualityChip e={{ downloaded: false, quality: null }} />);
    expect(screen.queryByTestId('tv-quality-chip')).toBeNull();
    rerender(<QualityChip e={{ downloaded: true, quality: null }} />);
    expect(screen.getByTestId('tv-quality-chip').textContent).toBe('Downloaded');   // v211: unknown quality
  });
});
