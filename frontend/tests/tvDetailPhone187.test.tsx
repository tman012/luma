import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PhoneSeriesList } from '@/components/library/TvSeries';

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
