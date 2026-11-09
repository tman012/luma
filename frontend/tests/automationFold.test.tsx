/** Automation sections start collapsed. Header controls stay usable and do not toggle the section. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { GenreRouletteSettings } from '@/components/automation/GenreRouletteSettings';
import { AnticipatedSettings } from '@/components/automation/AnticipatedSettings';

const GR = {
  settings: {
    enabled: true, dry_run: false, time: '22:50', days: 'daily', genres_per_run: 3, movies_per_genre: 1,
    include_genres: ['Comedy'], exclude_genres: [], year_from: 1986, year_to: 2023, language: 'en',
    min_votes: 800, imdb_min_rating: 0, imdb_min_votes: 120000, profile_id: null, min_seeders: 5,
    skip_same_collection: true,
  },
  genres: ['Comedy', 'Romance'], imdb_active: false, profiles: [{ id: 1, name: 'Any', default: true }],
  status: { running: false, enabled: true, next_run: null, failures_7d: 0, last_run: null }, history: [],
};
const AN = {
  settings: {
    interval_hours: 6, when_busy: 'skip', history_kept: 200,
    shows: { enabled: true, limit: 2, languages: ['en'], profile_id: 1, path_id: 1, monitor: 'all', search_on_add: true },
    movies: { enabled: false, limit: 2, languages: ['en'], profile_id: 1, path_id: 1, monitor: 'movie', search_on_add: true },
  },
  profiles: [{ id: 1, name: 'Any', default: true }], movie_profiles: [{ id: 1, name: 'Any', default: true }],
  libraries: [{ id: 1, name: 'TV' }], monitor: ['all'], languages: ['en'], history: [],
  status: { running: false, enabled: true, next_run: null, failures_7d: 0, last_run: null },
};

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const ok = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });

function stubGr() {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    if (init?.method === 'PUT') return ok({ ...GR, settings: { ...GR.settings, ...JSON.parse(String(init.body)) } });
    if (String(url).endsWith('/run')) return ok({ ok: true });
    if (String(url).endsWith('/status')) return ok({ status: GR.status, history: [], preview: { running: false, result: null } });
    return ok(GR);
  }));
  return calls;
}

describe('automation sections start collapsed', () => {
  it('keeps Genre roulette header controls usable and hides the schedule until the title is opened', async () => {
    const calls = stubGr();
    render(<GenreRouletteSettings />);
    await waitFor(() => expect(screen.getByTestId('genre-roulette')).toBeTruthy());
    const fold = screen.getByTestId('gr-fold');
    expect(fold.tagName).toBe('BUTTON');
    expect(fold.getAttribute('type')).toBe('button');
    expect(fold.getAttribute('aria-expanded')).toBe('false');
    expect(fold.getAttribute('aria-controls')).toBe('gr-details');
    expect(document.getElementById('gr-details')?.hasAttribute('hidden')).toBe(true);
    expect(screen.getByTestId('genre-roulette').getAttribute('data-open')).toBe('false');
    expect(screen.getByText('Run time').closest('[hidden]')).toBeTruthy();
    expect(screen.getByText('Last result').closest('[hidden]')).toBeTruthy();
    expect(screen.getByTestId('gr-run')).toBeTruthy();
    expect(screen.getByTestId('gr-preview')).toBeTruthy();

    fireEvent.click(screen.getByRole('switch', { name: 'Genre roulette' }));
    await waitFor(() => expect(calls.some(c => c.init?.method === 'PUT')).toBe(true));
    expect(fold.getAttribute('aria-expanded')).toBe('false');

    fireEvent.click(screen.getByTestId('gr-run'));
    await waitFor(() => expect(calls.some(c => c.url.endsWith('/run'))).toBe(true));
    expect(fold.getAttribute('aria-expanded')).toBe('false');

    fold.focus();
    expect(document.activeElement).toBe(fold);
    fireEvent.click(fold);
    expect(fold.getAttribute('aria-expanded')).toBe('true');
    expect(document.getElementById('gr-details')?.hasAttribute('hidden')).toBe(false);
    expect(screen.getByText('Run time').closest('[hidden]')).toBeNull();
    expect(screen.getByText('Last result').closest('[hidden]')).toBeNull();

    fireEvent.click(fold);
    expect(fold.getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByText('Run time').closest('[hidden]')).toBeTruthy();
  });

  it('starts Anticipated collapsed with Run now still available', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).endsWith('/status')) return ok({ status: AN.status, history: [] });
      return ok(AN);
    }));
    render(<AnticipatedSettings />);
    await waitFor(() => expect(screen.getByTestId('anticipated')).toBeTruthy());
    const fold = screen.getByTestId('an-fold');
    expect(fold.getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByText('Check every').closest('[hidden]')).toBeTruthy();
    expect(screen.getByTestId('an-run')).toBeTruthy();
    fireEvent.click(fold);
    expect(screen.getByText('Check every').closest('[hidden]')).toBeNull();
    fireEvent.click(screen.getByTestId('an-run'));
    expect(fold.getAttribute('aria-expanded')).toBe('true');
  });
});
