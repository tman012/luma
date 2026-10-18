import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const fetchSearch = vi.fn();
const saveSearch = vi.fn();
vi.mock('@/components/trackers/trackersApi', () => ({
  fetchSearchSettings: () => fetchSearch(),
  saveSearchSettings: (p: unknown) => saveSearch(p),
}));
const settingsApi = vi.fn();
vi.mock('@/services/settingsApi', () => ({ fetchSettings: () => settingsApi(), saveSettings: () => settingsApi() }));

import { CATEGORIES_ERROR, SEARCH_FIELDS, SearchSettings, searchFieldError } from '@/components/trackers/SearchSettings';

const KNOWN = [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070, 2080];
const ok = (movie_categories: string) => ({ ok: true, status: 200, data: { movie_categories, known_categories: KNOWN } });

describe('Settings > Trackers: search settings (v173, v174)', () => {
  beforeEach(() => {
    fetchSearch.mockResolvedValue(ok('2000,2040'));
    saveSearch.mockImplementation(async (p: { movie_categories: string }) => ok(p.movie_categories));
  });
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it.each([true, false])('shows only Movie categories with its saved value (wide=%s)', async wide => {
    const { container } = render(<SearchSettings wide={wide} onSaved={() => {}} />);
    await screen.findByTestId('tracker-search-settings');
    expect(screen.getByRole('heading', { name: 'Search' })).toBeTruthy();
    expect((screen.getByLabelText('Movie categories') as HTMLInputElement).value).toBe('2000,2040');
    expect(container.querySelectorAll('input')).toHaveLength(1);
    expect(SEARCH_FIELDS.map(f => f.key)).toEqual(['movie_categories']);
    for (const gone of ['Max age', 'Minimum seeders', 'Result limit']) expect(container.textContent).not.toContain(gone);
    expect(container.textContent).toBe('SearchMovie categories');          // no helper text
  });

  it('v174 (FF-8): reads and writes the trackers API, never /api/settings (404 on the luma-test build)', async () => {
    const onSaved = vi.fn();
    render(<SearchSettings wide onSaved={onSaved} />);
    const cats = await screen.findByLabelText('Movie categories');
    fireEvent.change(cats, { target: { value: '2000, 2045' } });
    fireEvent.blur(cats);
    await waitFor(() => expect(saveSearch).toHaveBeenCalledWith({ movie_categories: '2000,2045' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('Saved'));
    expect(settingsApi).not.toHaveBeenCalled();
  });

  it('an unchanged blur saves nothing; an invalid or unknown value shows an error and is not saved', async () => {
    render(<SearchSettings wide onSaved={() => {}} />);
    const cats = await screen.findByLabelText('Movie categories');
    fireEvent.blur(cats);
    for (const bad of ['movies', '2000;DROP', '5000', '9'.repeat(90)]) {
      fireEvent.change(cats, { target: { value: bad } });
      fireEvent.blur(cats);
      expect((await screen.findByTestId('tracker-search-movie_categories-error')).textContent).toBe(CATEGORIES_ERROR);
    }
    expect(saveSearch).not.toHaveBeenCalled();
    expect((cats as HTMLInputElement).maxLength).toBe(80);
  });

  it('a server 400 shows the field error', async () => {
    saveSearch.mockResolvedValue({ ok: false, status: 400, data: { error: 'Invalid', fields: { movie_categories: 'x' } } });
    const onSaved = vi.fn();
    render(<SearchSettings wide onSaved={onSaved} />);
    const cats = await screen.findByLabelText('Movie categories');
    fireEvent.change(cats, { target: { value: '2050' } });
    fireEvent.blur(cats);
    expect((await screen.findByTestId('tracker-search-movie_categories-error')).textContent).toBe(CATEGORIES_ERROR);
    expect(onSaved).not.toHaveBeenCalledWith('Saved');
  });

  it('renders nothing when the API refuses (not the owner)', async () => {
    fetchSearch.mockResolvedValue({ ok: false, status: 403, data: { error: 'Owner access required' } });
    const { container } = render(<SearchSettings wide onSaved={() => {}} />);
    await waitFor(() => expect(fetchSearch).toHaveBeenCalled());
    expect(container.textContent).toBe('');
  });

  it('validates categories against the known movie categories', () => {
    const [cat] = SEARCH_FIELDS;
    expect(searchFieldError(cat, '2000, 2045')).toBe('');
    expect(searchFieldError(cat, '2000')).toBe('');
    for (const bad of ['movies', '', '2000,', '5000', '2000, 5000', '2000;DROP', '1'.repeat(81), '0002000']) {
      expect(searchFieldError(cat, bad)).not.toBe('');
    }
  });
});
