import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const fetchSettings = vi.fn();
const saveSettings = vi.fn();
vi.mock('@/services/settingsApi', () => ({ fetchSettings: () => fetchSettings(), saveSettings: (c: unknown) => saveSettings(c) }));

import { SEARCH_FIELDS, SearchSettings, searchFieldError } from '@/components/trackers/SearchSettings';

const SAVED = { movie_categories: '2000,2040', max_age_days: 45, min_seeders: 3, limit: 250 };

describe('Settings > Trackers: search settings (v173)', () => {
  beforeEach(() => {
    fetchSettings.mockResolvedValue({ tracker_search: SAVED, indexer: { api_key: '********' } });
    saveSettings.mockResolvedValue({ success: true });
  });
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it.each([true, false])('shows the four settings with their saved values (wide=%s)', async wide => {
    render(<SearchSettings wide={wide} onSaved={() => {}} />);
    await screen.findByTestId('tracker-search-settings');
    expect(screen.getByRole('heading', { name: 'Search' })).toBeTruthy();
    expect((screen.getByLabelText('Movie categories') as HTMLInputElement).value).toBe('2000,2040');
    expect((screen.getByLabelText('Max age (days)') as HTMLInputElement).value).toBe('45');
    expect((screen.getByLabelText('Minimum seeders') as HTMLInputElement).value).toBe('3');
    expect((screen.getByLabelText('Result limit') as HTMLInputElement).value).toBe('250');
  });

  it('labels are sentence case and there is no helper text', async () => {
    const { container } = render(<SearchSettings wide onSaved={() => {}} />);
    await screen.findByTestId('tracker-search-settings');
    for (const f of SEARCH_FIELDS) {
      const words = f.label.replace(/\(.*\)/, '').trim().split(/\s+/);
      expect(words.slice(1).every(w => w === w.toLowerCase())).toBe(true);
    }
    const text = container.textContent || '';
    expect(text).toBe(['Search', ...SEARCH_FIELDS.map(f => f.label)].join(''));
  });

  it('saves a changed value on blur under tracker_search', async () => {
    const onSaved = vi.fn();
    render(<SearchSettings wide onSaved={onSaved} />);
    const input = await screen.findByLabelText('Minimum seeders');
    fireEvent.change(input, { target: { value: '5' } });
    fireEvent.blur(input);
    await waitFor(() => expect(saveSettings).toHaveBeenCalledWith({ tracker_search: { min_seeders: 5 } }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('Saved'));
    const cats = screen.getByLabelText('Movie categories');
    fireEvent.change(cats, { target: { value: '2000, 2045' } });
    fireEvent.blur(cats);
    await waitFor(() => expect(saveSettings).toHaveBeenCalledWith({ tracker_search: { movie_categories: '2000,2045' } }));
  });

  it('an unchanged blur saves nothing; an invalid value shows an error and is not saved', async () => {
    render(<SearchSettings wide onSaved={() => {}} />);
    const age = await screen.findByLabelText('Max age (days)');
    fireEvent.blur(age);
    fireEvent.change(age, { target: { value: '400' } });
    fireEvent.blur(age);
    expect(await screen.findByTestId('tracker-search-max_age_days-error')).toBeTruthy();
    expect(saveSettings).not.toHaveBeenCalled();
  });

  it('validates each field', () => {
    const [cat, age, seed, lim] = SEARCH_FIELDS;
    expect(searchFieldError(cat, '2000, 5000')).toBe('');
    expect(searchFieldError(cat, 'movies')).not.toBe('');
    expect(searchFieldError(age, '0')).not.toBe('');
    expect(searchFieldError(age, '365')).toBe('');
    expect(searchFieldError(seed, '0')).toBe('');
    expect(searchFieldError(seed, '-1')).not.toBe('');
    expect(searchFieldError(lim, '9')).not.toBe('');
    expect(searchFieldError(lim, '500')).toBe('');
  });
});
