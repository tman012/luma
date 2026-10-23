import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WhatsMissingSettings } from '@/components/automation/WhatsMissingSettings';
import { normalizeStatus, normalizeView } from '@/components/automation/whatsMissingApi';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

const SETTINGS = {
  movies_shown: 20, rebuild_hours: 24, no_repeat_days: 7, year_from: 1990, year_to: null, languages: ['en'],
  min_tmdb_votes: 500, min_imdb_votes: 10000, min_trakt_watchers: 0, trakt_lists: ['trending'], tmdb_lists: ['popular'],
  exclude_genres: [], include_adult: false, require_poster: true,
};
const VIEW = {
  settings: SETTINGS, trakt_lists: [{ id: 'trending', label: 'Trending' }], tmdb_lists: [{ id: 'popular', label: 'Popular' }],
  genres: ['Drama'], languages: [{ id: 'en', label: 'English' }], rebuild_hours: [6, 12, 24], movies_min: 5, movies_max: 50,
  no_repeat_max: 30, imdb_active: true, sources: { trakt: true, tmdb: true, omdb: true },
  status: { on_home: true, building: false, last_rebuilt: null, pool_size: 120, next_rebuild: null, error: null }, dismissed: [],
};

function stub(view: unknown, status: unknown) {
  const errors: unknown[] = [];
  vi.spyOn(console, 'error').mockImplementation((...a: unknown[]) => { errors.push(a); });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => (url.endsWith('/status') ? json(status) : json(view))));
  return errors;
}
const mount = () => render(<MemoryRouter><WhatsMissingSettings /></MemoryRouter>);

describe("v263 What you're missing card is null-safe", () => {
  it('normalizeStatus fills every missing field and keeps the last known ones', () => {
    expect(normalizeStatus({})).toEqual({ on_home: true, building: false, last_rebuilt: null, pool_size: 0, next_rebuild: null, error: null });
    expect(normalizeStatus(undefined).building).toBe(false);
    expect(normalizeStatus(null).pool_size).toBe(0);
    const prev = { ...VIEW.status, pool_size: 42 };
    expect(normalizeStatus({ building: true }, prev)).toEqual({ ...prev, building: true });
  });

  it('normalizeView: a view without status or lists still renders; no settings is a failed load', () => {
    const { status: _s, dismissed: _d, genres: _g, ...rest } = VIEW;
    const v = normalizeView(rest)!;
    expect(v.status.building).toBe(false);
    expect(v.dismissed).toEqual([]);
    expect(v.genres).toEqual([]);
    expect(normalizeView({})).toBeNull();
  });

  it('renders when the view has no status and the status endpoint answers {}', async () => {
    const { status: _s, ...noStatus } = VIEW;
    const errors = stub(noStatus, {});
    mount();
    await waitFor(() => screen.getByTestId('whats-missing'));
    expect(screen.getByTestId('wym-state').textContent).toContain('Idle');
    expect(screen.getByTestId('wym-refresh').textContent).toBe('Refresh now');
    expect(errors).toEqual([]);
  });

  it('a status poll of {} or { status: {} } keeps the card up with the last known values', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    for (const answer of [{}, { status: {} }, { status: { building: undefined } }]) {
      const errors = stub(VIEW, answer);
      mount();
      await waitFor(() => screen.getByTestId('whats-missing'));
      await vi.advanceTimersByTimeAsync(30_000);
      await waitFor(() => expect((fetch as unknown as { mock: { calls: string[][] } }).mock.calls.some(c => String(c[0]).endsWith('/status'))).toBe(true));
      expect(screen.getByTestId('whats-missing')).toBeTruthy();
      expect(screen.getByTestId('wym-sub').textContent).toContain('120 movies');
      expect(errors).toEqual([]);
      cleanup();
      vi.unstubAllGlobals();
    }
  });
});
