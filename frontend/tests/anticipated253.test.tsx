import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { AnticipatedSettings, counts, skips } from '@/components/automation/AnticipatedSettings';

const G = {
  enabled: false, limit: 20, profile_id: null, search_on_add: true, languages: ['en'], allow_unknown_language: true,
  skip_plex: true, skip_if_plex_unreadable: true, no_readd: false, page: 1,
};
const SETTINGS = {
  shows: { ...G, path_id: null, monitor: 'all', require_tvdb: true }, movies: { ...G, languages: [] },
  interval_hours: 2, when_busy: 'skip', history_kept: 200,
};
const LAST = {
  shows: { checked: 20, added: 3, skipped: { existing: 9, plex: 4, language: 2, no_tvdb: 2, no_tmdb: 0, removed: 0, grabbed: 0 }, errors: [], error: null },
  movies: { checked: 0, added: 0, skipped: {}, errors: [], error: 'Trakt is busy' }, error: null,
};
const HIST = [
  { id: 2, kind: 'movies', tmdb_id: 9, tvdb_id: null, title: 'Dune: Part Three', year: 2026, added_at: Date.parse('2026-10-22T15:00:00-04:00') / 1000 },
  { id: 1, kind: 'shows', tmdb_id: 5, tvdb_id: 7, title: 'The Last of Us', year: 2027, added_at: Date.parse('2026-10-22T03:05:00-04:00') / 1000 },
];
const view = (over: Record<string, unknown> = {}) => ({
  settings: SETTINGS, monitor: ['all'], languages: ['en', 'fr', 'ja'],
  profiles: [{ id: 1, name: 'Any', default: false }, { id: 2, name: 'HD-1080p', default: true }],
  movie_profiles: [{ id: 4, name: 'TFlix', default: true }],
  libraries: [{ id: 'p_tv', name: 'TV' }, { id: 'p_kids', name: 'Kids TV' }],
  status: { running: false, enabled: false, next_run: null, last_run: Date.parse('2026-10-22T15:00:00-04:00') / 1000, last_kind: 'manual', last_result: LAST },
  history: HIST, ...over,
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const ok = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });

function stub(handler?: (url: string, init?: RequestInit) => Response | undefined) {
  const calls: { url: string; init?: RequestInit }[] = [];
  let cur = view();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const h = handler?.(url, init);
    if (h) return h;
    if (init?.method === 'PUT') {
      const p = JSON.parse(String(init.body));
      const s = { ...cur.settings, ...p, shows: { ...cur.settings.shows, ...p.shows }, movies: { ...cur.settings.movies, ...p.movies } };
      cur = view({ settings: s });
      return ok(cur);
    }
    if (init?.method === 'POST') return ok({ ok: true, started: true }, 202);
    if (url.endsWith('/status')) return ok({ status: cur.status, history: cur.history });
    return ok(cur);
  }));
  return calls;
}
const puts = (calls: { init?: RequestInit }[]) => calls.filter(c => c.init?.method === 'PUT').map(c => JSON.parse(String(c.init!.body)));

describe('v253 Settings > Automation > Anticipated', () => {
  it('shows both groups with prod defaults, shared settings, status counts and history', async () => {
    stub();
    render(<AnticipatedSettings />);
    await waitFor(() => expect(screen.getByTestId('anticipated')).toBeTruthy());
    expect(screen.getByRole('heading', { name: 'Anticipated' })).toBeTruthy();
    for (const t of ['Check every', 'When Trakt is busy', 'History kept', 'Shows per run', 'Movies per run', 'Library', 'Monitor',
      'Skip shows without a TVDB ID', "Don't re-add removed shows", "Don't re-add removed movies", 'Skip shows in Plex', 'Skip movies in Plex']) {
      expect(screen.getByText(t)).toBeTruthy();
    }
    expect(screen.getAllByText('Search when added')).toHaveLength(2);
    expect(screen.getAllByText("Skip the run if Plex can't be read")).toHaveLength(2);
    expect(screen.getByTestId('an-every-select').textContent).toBe('2 hours');
    expect(screen.getByTestId('an-busy-select').textContent).toBe('Skip this run');
    expect(screen.getByTestId('an-kept-select').textContent).toBe('200');
    expect(screen.getByTestId('an-shows-profile-select').textContent).toBe('HD-1080p');
    expect(screen.getByTestId('an-movies-profile-select').textContent).toBe('TFlix');
    expect(screen.getByTestId('an-shows-library-select').textContent).toBe('TV');
    expect(screen.getByTestId('an-shows-monitor-select').textContent).toBe('All episodes');
    expect(screen.getByTestId('an-shows-limit-value').textContent).toBe('20');
    expect((screen.getByTestId('an-shows-page-input') as HTMLInputElement).value).toBe('1');
    const shows = within(screen.getByTestId('an-shows'));
    const movies = within(screen.getByTestId('an-movies'));
    expect(shows.getByRole('switch', { name: 'Shows' }).getAttribute('aria-checked')).toBe('false');
    expect(movies.getByRole('switch', { name: 'Movies' }).getAttribute('aria-checked')).toBe('false');
    expect(movies.queryByText('Monitor')).toBeNull();
    expect(movies.queryByText('Library')).toBeNull();
    const pressed = (w: typeof shows) => w.getAllByTestId('an-lang').filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.dataset.code);
    expect(pressed(shows)).toEqual(['en']);
    expect(pressed(movies)).toEqual([]);
    expect(screen.getAllByRole('switch').every(s => s.getAttribute('role') === 'switch')).toBe(true);
    const tiles = screen.getAllByTestId('an-tile').map(t => t.textContent);
    expect(tiles[2]).toBe('ShowsAdded 3 of 20Skipped: already added 9, in Plex 4, language 2, no TVDB ID 2');
    expect(tiles[3]).toBe('MoviesTrakt is busy');
    expect(screen.getAllByTestId('an-history').map(h => h.textContent)).toEqual([
      'Dune: Part Three2026Thu Oct 22, 3:00 PM', 'The Last of Us2027Thu Oct 22, 3:05 AM']);
    expect(screen.getByTestId('an-state').textContent).toBe('● Off');
    expect(document.body.textContent || '').not.toMatch(/sonarr|radarr|colour/i);
  });

  it('saves each group on its own and the shared settings at the top level', async () => {
    const calls = stub();
    render(<AnticipatedSettings />);
    await waitFor(() => expect(screen.getByTestId('anticipated')).toBeTruthy());
    fireEvent.click(within(screen.getByTestId('an-movies')).getByRole('switch', { name: 'Movies' }));
    await waitFor(() => expect(puts(calls)).toEqual([{ movies: { enabled: true } }]));
    fireEvent.click(screen.getByTestId('an-shows-limit-plus'));
    await waitFor(() => expect(puts(calls)[1]).toEqual({ shows: { limit: 21 } }));
    fireEvent.click(within(screen.getByTestId('an-shows')).getAllByTestId('an-lang').find(b => b.dataset.code === 'en')!);
    await waitFor(() => expect(puts(calls)[2]).toEqual({ shows: { languages: [] } }));
    fireEvent.click(within(screen.getByTestId('an-movies')).getAllByTestId('an-lang').find(b => b.dataset.code === 'ja')!);
    await waitFor(() => expect(puts(calls)[3]).toEqual({ movies: { languages: ['ja'] } }));
    fireEvent.click(screen.getByTestId('an-movies-readd-toggle'));
    await waitFor(() => expect(puts(calls)[4]).toEqual({ movies: { no_readd: true } }));
    const page = screen.getByTestId('an-shows-page-input') as HTMLInputElement;
    fireEvent.change(page, { target: { value: '3' } });
    fireEvent.blur(page);
    await waitFor(() => expect(puts(calls)[5]).toEqual({ shows: { page: 3 } }));
    expect(screen.getByTestId('an-shows-limit-value').textContent).toBe('21');
  });

  it('a rejected save goes back and shows the error', async () => {
    stub((url, init) => (init?.method === 'PUT' ? ok({ error: 'Invalid', errors: { 'shows.limit': 'Invalid' } }, 400) : undefined));
    render(<AnticipatedSettings />);
    await waitFor(() => expect(screen.getByTestId('anticipated')).toBeTruthy());
    fireEvent.click(screen.getByTestId('an-shows-limit-plus'));
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
    expect(screen.getByTestId('an-shows-limit-value').textContent).toBe('20');
  });

  it('Run now posts even when both groups are off', async () => {
    const calls = stub();
    render(<AnticipatedSettings />);
    await waitFor(() => expect(screen.getByTestId('anticipated')).toBeTruthy());
    fireEvent.click(screen.getByTestId('an-run'));
    await waitFor(() => expect(calls.some(c => c.init?.method === 'POST' && c.url.endsWith('/anticipated/run'))).toBe(true));
  });

  it('the 401 for the implicit LAN owner shows an error, not a change', async () => {
    stub((url, init) => (init?.method ? ok({ error: 'Sign in' }, 401) : undefined));
    render(<AnticipatedSettings />);
    await waitFor(() => expect(screen.getByTestId('anticipated')).toBeTruthy());
    fireEvent.click(within(screen.getByTestId('an-shows')).getByRole('switch', { name: 'Shows' }));
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
    expect(within(screen.getByTestId('an-shows')).getByRole('switch', { name: 'Shows' }).getAttribute('aria-checked')).toBe('false');
  });

  it('formats counts and skips', () => {
    expect(counts(null)).toBe('—');
    expect(counts({ checked: 5, added: 1, skipped: {}, errors: [], error: null })).toBe('Added 1 of 5');
    expect(skips({ checked: 5, added: 0, skipped: { grabbed: 2, removed: 1, plex: 0 }, errors: [], error: null })).toBe('already grabbed 2, removed before 1');
  });
});
