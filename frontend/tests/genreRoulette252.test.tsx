import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { GenreRouletteSettings, clock, dot, flattenHistory, isGrabPick, result, runDetails, stamp, when } from '@/components/automation/GenreRouletteSettings';

const GENRES = ['Action', 'Adventure', 'Animation', 'Comedy', 'Crime', 'Documentary', 'Drama', 'Family', 'Fantasy', 'History',
  'Horror', 'Music', 'Mystery', 'Romance', 'Science Fiction', 'Thriller', 'War', 'Western'];
const SETTINGS = {
  enabled: false, dry_run: false, time: '22:50', days: 'daily', genres_per_run: 3, movies_per_genre: 1,
  include_genres: ['Action', 'Science Fiction'], exclude_genres: ['Documentary', 'Horror'], year_from: 1986, year_to: 2023,
  language: 'en', min_votes: 800, imdb_min_rating: 0, imdb_min_votes: 120000, profile_id: null, min_seeders: 5, skip_same_collection: true,
};
const RUN = {
  id: 7, started_at: Date.parse('2026-10-20T03:00:00-04:00') / 1000, finished_at: null, kind: 'scheduled', dry: false, status: 'done',
  wanted: 3, grabbed: 2, stop_reason: 'pool_exhausted', genres: ['Thriller', 'Mystery', 'Action'], error: null,
  picks: [{ tmdb_id: 146233, title: 'Prisoners', year: 2013, genre: 'Thriller', poster_path: '/p.jpg' },
    { tmdb_id: 1949, title: 'Zodiac', year: 2007, genre: 'Mystery', poster_path: null }],
  failures: [{ genre: 'Action', reason: 'No release found' }],
};
const view = (over: Record<string, unknown> = {}) => ({
  settings: SETTINGS, genres: GENRES, imdb_active: false,
  profiles: [{ id: 1, name: 'Any', default: false }, { id: 2, name: 'HD 1080p', default: true }],
  status: { running: false, enabled: false, next_run: null, failures_7d: 1, last_run: { ...RUN } }, history: [RUN], ...over,
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
    if (init?.method === 'PUT') { cur = view({ settings: { ...cur.settings, ...JSON.parse(String(init.body)) } }); return ok(cur); }
    if (url.endsWith('/status')) return ok({ status: cur.status, history: cur.history, preview: { running: false, result: null } });
    return ok(cur);
  }));
  return calls;
}

describe('v252 Settings > Automation > Genre roulette', () => {
  it('matches GR5: header, sections, chips, ratings, quality and status with history', async () => {
    stub();
    render(<GenreRouletteSettings />);
    await waitFor(() => expect(screen.getByTestId('genre-roulette')).toBeTruthy());
    expect(screen.getByRole('switch', { name: 'Genre roulette' }).getAttribute('aria-checked')).toBe('false');
    expect(screen.getByTestId('gr-sub').textContent).toBe('Last: 2 of 3');
    for (const t of ['Run time', 'Days', 'Genres per run', 'Movies per genre', 'Years', 'Language', 'Minimum TMDb votes',
      'Minimum IMDb rating', 'Minimum IMDb votes', 'Quality profile', 'Minimum seeders', 'Skip same collection', 'Dry run']) {
      expect(screen.getByText(t)).toBeTruthy();
    }
    expect(screen.getByTestId('gr-time-select').textContent).toBe('10:50 PM');
    expect(screen.getByTestId('gr-days-select').textContent).toBe('Every day');
    expect(screen.getByTestId('gr-profile-select').textContent).toBe('HD 1080p');
    expect(screen.getByTestId('gr-language-select').textContent).toBe('English');
    expect((screen.getByTestId('gr-min-votes-input') as HTMLInputElement).value).toBe('800');
    expect((screen.getByTestId('gr-imdb-votes-input') as HTMLInputElement).value).toBe('120,000');
    expect((screen.getByTestId('gr-imdb-votes-input') as HTMLInputElement).disabled).toBe(true);
    const chips = screen.getAllByTestId('gr-chip');
    expect(chips).toHaveLength(18);
    const state = (n: string) => chips.find(c => (c.textContent || '').endsWith(n))!.dataset.state;
    expect([state('Action'), state('Sci-Fi'), state('Horror'), state('Drama')]).toEqual(['on', 'on', 'out', 'off']);
    expect(chips.find(c => c.textContent?.endsWith('Horror'))!.style.textDecoration).toBe('line-through');
    expect(screen.getByTestId('gr-state').textContent).toBe('● Off');
    expect(screen.getAllByTestId('gr-tile').map(t => t.textContent)).toContain('Last resultAdded 2 of 3');
    // Compact history (v298): grabbed titles only by default; failures behind per-run disclosure
    expect(screen.queryByTestId('gr-history-run')).toBeNull();
    expect(screen.getByTestId('gr-history').textContent || '').not.toMatch(/\d+ of \d+/);
    expect(screen.getAllByTestId('gr-pick').map(p => p.textContent)).toEqual(['Prisoners', 'Zodiac']);
    expect(screen.getAllByTestId('gr-history-row').map(r => r.dataset.kind)).toEqual(['grab', 'grab']);
    expect(screen.queryByTestId('gr-failure')).toBeNull();
    expect(screen.getByTestId('gr-history-disclose')).toBeTruthy();
    fireEvent.click(screen.getByTestId('gr-history-disclose'));
    expect(screen.getByTestId('gr-failure').textContent).toBe('⚠ Action: no release found');
    expect(screen.getByTestId('gr-history-summary').textContent).toBe('Added 2 of 3');
    expect(screen.getAllByText(/Tue Oct 20, 3:00 AM/).length).toBeGreaterThanOrEqual(2);
    expect(document.body.textContent || '').not.toMatch(/radarr|colour|favourite/i);
  });

  it('saves changes: switch, chip cycle, numbers, stepper; a rejected value shows the error and goes back', async () => {
    const calls = stub((url, init) => (init?.method === 'PUT' && String(init.body).includes('"min_seeders":-') ? ok({ error: 'Invalid', errors: { min_seeders: 'Invalid' } }, 400) : undefined));
    render(<GenreRouletteSettings />);
    await waitFor(() => expect(screen.getByTestId('genre-roulette')).toBeTruthy());
    const puts = () => calls.filter(c => c.init?.method === 'PUT').map(c => JSON.parse(String(c.init!.body)));
    fireEvent.click(screen.getByRole('switch', { name: 'Genre roulette' }));
    await waitFor(() => expect(puts()).toEqual([{ enabled: true }]));
    fireEvent.click(screen.getByRole('button', { name: 'Drama' }));
    await waitFor(() => expect(puts()[1]).toEqual({ include_genres: ['Action', 'Science Fiction', 'Drama'], exclude_genres: ['Documentary', 'Horror'] }));
    fireEvent.click(screen.getByRole('button', { name: 'Action, included' }));
    await waitFor(() => expect(puts()[2]).toEqual({ include_genres: ['Science Fiction', 'Drama'], exclude_genres: ['Documentary', 'Horror', 'Action'] }));
    fireEvent.click(screen.getByRole('button', { name: 'Horror, excluded' }));
    await waitFor(() => expect(puts()[3]).toEqual({ include_genres: ['Science Fiction', 'Drama'], exclude_genres: ['Documentary', 'Action'] }));
    const votes = screen.getByTestId('gr-min-votes-input');
    fireEvent.change(votes, { target: { value: '1,500' } });
    fireEvent.blur(votes);
    await waitFor(() => expect(puts()[4]).toEqual({ min_votes: 1500 }));
    fireEvent.click(screen.getByTestId('gr-genres-per-run-plus'));
    await waitFor(() => expect(puts()[5]).toEqual({ genres_per_run: 4 }));
    const seeders = screen.getByTestId('gr-seeders-input') as HTMLInputElement;
    fireEvent.change(seeders, { target: { value: '-2' } });
    fireEvent.blur(seeders);
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe("Couldn't save"));
    await waitFor(() => expect(seeders.value).toBe('5'));
    expect(seeders.getAttribute('aria-invalid')).toBe('true');
  });

  it('Run now and Preview start runs; the preview shows what it would add', async () => {
    let previewDone = false;
    const calls = stub(url => {
      if (url.endsWith('/run') || url.endsWith('/preview')) { previewDone = url.endsWith('/preview'); return ok({ ok: true }, 202); }
      if (url.endsWith('/status')) {
        return ok({ status: view().status, history: [RUN],
          preview: previewDone ? { running: false, result: { ...RUN, dry: true, grabbed: 3, wanted: 3, failures: [] } } : { running: false, result: null } });
      }
      return undefined;
    });
    render(<GenreRouletteSettings />);
    await waitFor(() => expect(screen.getByTestId('genre-roulette')).toBeTruthy());
    fireEvent.click(screen.getByTestId('gr-run'));
    await waitFor(() => expect(calls.some(c => c.url.endsWith('/run') && c.init?.method === 'POST')).toBe(true));
    await act(async () => { fireEvent.click(screen.getByTestId('gr-preview')); });
    await waitFor(() => expect(screen.getByTestId('gr-preview-result').textContent).toContain('Would add 3 of 3'));
  });

  it('Run now that the server refuses says so', async () => {
    stub(url => (url.endsWith('/run') ? ok({ error: 'Sign in' }, 401) : undefined));
    render(<GenreRouletteSettings />);
    await waitFor(() => expect(screen.getByTestId('genre-roulette')).toBeTruthy());
    fireEvent.click(screen.getByTestId('gr-run'));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe("Couldn't start"));
  });

  it('a malformed answer reads as Couldn\'t load, never a crash; list fields that aren\'t arrays read as empty', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok({})));
    render(<GenreRouletteSettings />);
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe("Couldn't load"));
    cleanup();
    stub((url, init) => (!init?.method && !url.endsWith('/status')
      ? ok({ ...view(), history: null, profiles: 'x', settings: { ...SETTINGS, include_genres: null } }) : undefined));
    render(<GenreRouletteSettings />);
    await waitFor(() => expect(screen.getByTestId('genre-roulette')).toBeTruthy());
    expect(screen.getAllByTestId('gr-chip').filter(c => c.dataset.state === 'on')).toHaveLength(0);
    expect(screen.getByText('No runs yet')).toBeTruthy();
  });

  it('formats times and results', () => {
    expect(stamp(new Date('2026-10-21T07:00:00Z'))).toBe('Wed Oct 21, 3:00 AM');
    const now = new Date('2026-10-21T16:00:00Z');
    expect([when(new Date('2026-10-22T02:50:00Z'), now), when(new Date('2026-10-23T02:50:00Z'), now), when(new Date('2026-10-24T07:00:00Z'), now)])
      .toEqual(['Today 10:50 PM', 'Tomorrow 10:50 PM', 'Sat 3:00 AM']);
    expect([clock('22:50'), clock('00:00'), clock('12:05')]).toEqual(['10:50 PM', '12:00 AM', '12:05 PM']);
    expect(result({ grabbed: 1, wanted: 3 })).toBe('Added 1 of 3');
    expect(result({ grabbed: 1, wanted: 3, dry: true })).toBe('Would add 1 of 3');
    expect(result({ error: "qBittorrent couldn't be reached" })).toBe("qBittorrent couldn't be reached");
    const r = { status: 'done', error: null };
    expect([dot({ ...r, grabbed: 3, wanted: 3 }), dot({ ...r, grabbed: 2, wanted: 3 }), dot({ ...r, grabbed: 0, wanted: 3 })]).toEqual(['#4ade80', '#fbbf24', '#f87171']);
  });

  it('defaults history to grabbed titles; attempts stay behind disclosure', () => {
    const withSkips = {
      ...RUN,
      picks: [
        { ...RUN.picks[0], grabbed: true },
        { ...RUN.picks[1], grabbed: true },
      ],
      skips: { same_collection: [{ tmdb_id: 1, title: 'Duped', year: 2010, genre: 'Action' }] },
    };
    const rows = flattenHistory([withSkips as never, {
      ...RUN, id: 8, grabbed: 0, wanted: 3, picks: [], failures: [], error: "Trackers couldn't be reached",
    } as never]);
    expect(rows.map(r => r.kind)).toEqual(['grab', 'grab', 'compact']);
    expect(rows.filter(r => r.kind === 'grab').map(r => r.kind === 'grab' && r.pick.title)).toEqual(['Prisoners', 'Zodiac']);
    expect(rows[1].kind === 'grab' && rows[1].disclose).toBe(true);
    expect(rows[2].kind === 'compact' && rows[2].error).toBe("Trackers couldn't be reached");
    expect(rows[2].kind === 'compact' && rows[2].disclose).toBe(true);
    const empty = flattenHistory([{ ...RUN, id: 9, picks: [], failures: [], error: null, grabbed: 0, wanted: 3, status: 'done' } as never]);
    expect(empty.map(r => r.kind)).toEqual(['compact']);
    expect(empty[0].kind === 'compact' && empty[0].label).toBe('Nothing added');
    expect(isGrabPick({ dry: true }, { grabbed: false })).toBe(true);
    expect(isGrabPick({ dry: false }, { grabbed: false })).toBe(false);
    expect(isGrabPick({ dry: false }, {})).toBe(true);
    const d = runDetails(withSkips as never)!;
    expect(d.skips.map(s => s.title)).toEqual(['Duped']);
    expect(d.failures.map(f => f.genre)).toEqual(['Action']);
  });

  it('dry-run would-grabs appear as grab rows with Dry run stamp', async () => {
    const dryRun = {
      ...RUN, id: 11, dry: true, grabbed: 2, picks: RUN.picks.map(p => ({ ...p, grabbed: false })), failures: [],
    };
    stub((url, init) => (!init?.method && !url.endsWith('/status')
      ? ok({ ...view(), history: [dryRun] }) : undefined));
    render(<GenreRouletteSettings />);
    await waitFor(() => expect(screen.getByTestId('genre-roulette')).toBeTruthy());
    expect(screen.getAllByTestId('gr-history-row').map(r => r.dataset.kind)).toEqual(['grab', 'grab']);
    expect(screen.getAllByText('Dry run').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByTestId('gr-failure')).toBeNull();
  });
});
