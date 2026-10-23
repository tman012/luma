import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WhatsMissingSettings, daysLabel, hoursLabel, yearOptions } from '@/components/automation/WhatsMissingSettings';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });


const S = {
  movies_shown: 20, rebuild_hours: 8, no_repeat_days: 3, year_from: 1985, year_to: null, languages: ['en'],
  min_tmdb_votes: 150, min_imdb_votes: 1000, min_trakt_watchers: 50,
  trakt_lists: ['popular', 'trending', 'anticipated', 'boxoffice', 'watched_yearly', 'watched_all', 'favorited_yearly', 'favorited_all'],
  tmdb_lists: ['popular', 'top_rated', 'most_voted', 'most_popular', 'highest_grossing', 'highest_rated', 'by_genre', 'by_year'],
  exclude_genres: [] as string[], include_adult: false, require_poster: true,
};
const TL = [['popular', 'Popular'], ['trending', 'Trending'], ['anticipated', 'Anticipated'], ['boxoffice', 'Box office'],
  ['watched_yearly', 'Most watched this year'], ['watched_all', 'Most watched all time'], ['favorited_yearly', 'Most favorited this year'],
  ['favorited_all', 'Most favorited all time']].map(([id, label]) => ({ id, label }));
const ML = [['popular', 'Popular'], ['top_rated', 'Top rated'], ['most_voted', 'Most voted'], ['most_popular', 'Most popular'],
  ['highest_grossing', 'Highest grossing'], ['highest_rated', 'Highest rated'], ['by_genre', 'By genre'], ['by_year', 'By year']].map(([id, label]) => ({ id, label }));
const GENRES = ['Action', 'Adventure', 'Animation', 'Comedy', 'Crime', 'Drama', 'Family', 'Fantasy', 'History', 'Horror', 'Mystery', 'Romance', 'Science Fiction', 'Thriller'];
const STATUS = { on_home: true, building: false, last_rebuilt: '2026-10-23T06:00:00-04:00', pool_size: 3412, next_rebuild: '2026-10-23T14:00:00-04:00', error: null };
const VIEW = (over: Record<string, unknown> = {}) => ({
  settings: S, trakt_lists: TL, tmdb_lists: ML, genres: GENRES,
  languages: [{ id: 'en', label: 'English' }, { id: 'fr', label: 'French' }, { id: 'ko', label: 'Korean' }],
  rebuild_hours: [2, 4, 6, 8, 12, 24], movies_min: 5, movies_max: 40, no_repeat_max: 14, imdb_active: false,
  sources: { trakt: true, tmdb: true, omdb: false }, status: STATUS,
  dismissed: [{ tmdb_id: 7, title: 'Cats', year: 2019, poster_path: null, at: 1 }], ...over,
});

function stub(handler?: (url: string, init?: RequestInit) => Response | undefined) {
  const calls: { url: string; init?: RequestInit }[] = [];
  let cur = VIEW();
  let building = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const h = handler?.(url, init);
    if (h) return h;
    if (init?.method === 'PUT') { cur = VIEW({ settings: { ...cur.settings, ...JSON.parse(String(init.body)) } }); return json(cur); }
    if (url.endsWith('/status')) return json({ status: building ? { ...cur.status, building, next_rebuild: null } : cur.status });
    if (url.endsWith('/refresh')) { building = true; return json({ ok: true, status: { ...STATUS, building: true, next_rebuild: null } }, 202); }
    if (url.endsWith('/restore')) return json({ ok: true, dismissed: [] });
    return json(cur);
  }));
  return calls;
}
const puts = (calls: { init?: RequestInit }[]) => calls.filter(c => c.init?.method === 'PUT').map(c => JSON.parse(String(c.init!.body)));

describe("v254 Settings > Automation > What you're missing", () => {
  it("shows every setting with prod's defaults, sentence case, no helper text", async () => {
    stub();
    render(<WhatsMissingSettings />);
    await waitFor(() => screen.getByTestId('whats-missing'));
    for (const t of ["What you're missing", 'Movies shown', 'Rebuild every', "Don't repeat for", 'Earliest year', 'Latest year',
      'Original languages', 'Minimum TMDb votes', 'Minimum IMDb votes', 'Minimum Trakt watchers', 'Trakt lists', 'TMDb lists',
      'Genres', 'Include adult', 'Require poster', 'Dismissed movies', 'Refresh now', 'Status', 'Last rebuilt', 'Pool size', 'Next rebuild']) {
      expect(screen.getAllByText(t).length, t).toBeGreaterThan(0);
    }
    expect(screen.queryByText(/Show What you're missing/)).toBeNull();          // Home screen settings owns show/hide
    expect((screen.getByTestId('wym-movies-input') as HTMLInputElement).value).toBe('20');
    expect(screen.getByTestId('wym-rebuild-select').textContent).toBe('8 hours');
    expect(screen.getByTestId('wym-repeat-select').textContent).toBe('3 days');
    expect((screen.getByTestId('wym-year-from-input') as HTMLInputElement).value).toBe('1985');
    expect(screen.getByTestId('wym-year-to-select').textContent).toBe('Current year');
    expect((screen.getByTestId('wym-tmdb-votes-input') as HTMLInputElement).value).toBe('150');
    expect((screen.getByTestId('wym-imdb-votes-input') as HTMLInputElement).value).toBe('1,000');
    expect((screen.getByTestId('wym-imdb-votes-input') as HTMLInputElement).disabled).toBe(true);      // no OMDb key
    expect((screen.getByTestId('wym-trakt-watchers-input') as HTMLInputElement).value).toBe('50');
    const checks = (id: string) => [...screen.getByTestId(id).querySelectorAll('[role="checkbox"]')];
    expect(checks('wym-trakt-lists').map(c => c.getAttribute('aria-checked'))).toEqual(Array(8).fill('true'));
    expect(checks('wym-tmdb-lists').map(c => c.textContent)).toEqual(ML.map(m => m.label));
    const chips = [...screen.getByTestId('wym-genres').querySelectorAll('button')];
    expect(chips).toHaveLength(14);
    expect(chips.every(c => c.getAttribute('data-state') === 'on')).toBe(true);
    expect(screen.getByRole('switch', { name: 'Include adult' }).getAttribute('aria-checked')).toBe('false');
    expect(screen.getByRole('switch', { name: 'Require poster' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getAllByTestId('wym-tile').map(t => t.textContent)).toContain('Pool size3,412');
    expect(screen.getByTestId('wym-dismissed-row').textContent).toContain('Cats (2019)');
    expect(document.body.textContent || '').not.toMatch(/radarr|colour|favourite|What You're Missing/);
  });

  it('saves as it changes: genres gray out with a strikethrough, lists, languages, year, toggles, numbers', async () => {
    const calls = stub();
    render(<WhatsMissingSettings />);
    await waitFor(() => screen.getByTestId('whats-missing'));
    fireEvent.click(screen.getByRole('button', { name: 'Horror, included' }));
    await waitFor(() => expect(puts(calls).at(-1)).toEqual({ exclude_genres: ['Horror'] }));
    const horror = screen.getByRole('button', { name: 'Horror, excluded' });
    expect(horror.style.textDecoration).toBe('line-through');
    expect(horror.dataset.state).toBe('out');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Box office' }));
    await waitFor(() => expect(puts(calls).at(-1)).toEqual({ trakt_lists: S.trakt_lists.filter(x => x !== 'boxoffice') }));
    fireEvent.click(screen.getByRole('button', { name: 'Korean' }));
    await waitFor(() => expect(puts(calls).at(-1)).toEqual({ languages: ['en', 'ko'] }));
    fireEvent.click(screen.getByRole('switch', { name: 'Include adult' }));
    await waitFor(() => expect(puts(calls).at(-1)).toEqual({ include_adult: true }));
    const votes = screen.getByTestId('wym-tmdb-votes-input');
    fireEvent.change(votes, { target: { value: '1,200' } });
    fireEvent.blur(votes);
    await waitFor(() => expect(puts(calls).at(-1)).toEqual({ min_tmdb_votes: 1200 }));
    const movies = screen.getByTestId('wym-movies-input');
    fireEvent.change(movies, { target: { value: '99' } });
    fireEvent.blur(movies);
    await waitFor(() => expect(puts(calls).at(-1)).toEqual({ movies_shown: 40 }));
    fireEvent.click(screen.getByTestId('wym-year-to-select'));
    fireEvent.click(screen.getByRole('option', { name: '2020' }));
    await waitFor(() => expect(puts(calls).at(-1)).toEqual({ year_to: 2020 }));
  });

  it('keeps one language; a rejected change goes back and says so', async () => {
    const calls = stub((_u, init) => (init?.method === 'PUT' && String(init.body).includes('year_from') ? json({ error: 'Invalid', errors: { year_from: 'Invalid' } }, 400) : undefined));
    render(<WhatsMissingSettings />);
    await waitFor(() => screen.getByTestId('whats-missing'));
    fireEvent.click(screen.getByRole('button', { name: 'English, included' }));
    expect(puts(calls)).toEqual([]);
    const y = screen.getByTestId('wym-year-from-input') as HTMLInputElement;
    fireEvent.change(y, { target: { value: '2030' } });
    fireEvent.blur(y);
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe("Couldn't save"));
    await waitFor(() => expect(y.value).toBe('1985'));
    expect(y.getAttribute('aria-invalid')).toBe('true');
  });

  it('Refresh now starts a rebuild; Restore brings a film back', async () => {
    const calls = stub();
    render(<WhatsMissingSettings />);
    await waitFor(() => screen.getByTestId('whats-missing'));
    fireEvent.click(screen.getByTestId('wym-refresh'));
    await waitFor(() => expect(screen.getByTestId('wym-refresh').textContent).toBe('Rebuilding'));
    expect(screen.getByTestId('wym-state').textContent).toBe('● Rebuilding');
    expect(calls.some(c => c.url.endsWith('/api/rails/what-youre-missing/refresh') && c.init?.method === 'POST')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Restore Cats' }));
    await waitFor(() => expect(screen.queryByTestId('wym-dismissed-row')).toBeNull());
    const r = calls.find(c => c.url.endsWith('/restore'))!;
    expect(JSON.parse(String(r.init!.body))).toEqual({ tmdb_id: 7 });
    expect(screen.getByText('None')).toBeTruthy();
  });

  it('says when the rail is hidden (Home screen settings)', async () => {
    stub(url => (url.endsWith('what-youre-missing') ? json(VIEW({ status: { ...STATUS, on_home: false, next_rebuild: null } })) : undefined));
    render(<WhatsMissingSettings />);
    await waitFor(() => screen.getByTestId('whats-missing'));
    expect(screen.getByTestId('wym-state').textContent).toBe('● Hidden');
  });

  it('labels', () => {
    expect([hoursLabel(2), hoursLabel(24), daysLabel(0), daysLabel(1), daysLabel(3)]).toEqual(['2 hours', '24 hours', 'Off', '1 day', '3 days']);
    const y = yearOptions(new Date('2026-10-23T12:00:00-04:00'));
    expect(y[0]).toEqual({ id: 'current', label: 'Current year' });
    expect(y[1]).toEqual({ id: '2026', label: '2026' });
    expect(y.at(-1)).toEqual({ id: '1950', label: '1950' });
  });
});
