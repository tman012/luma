import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

// Tester LOW (v255): /api/home answering {}, null, or without rails / continue_watching shows the empty
// state instead of crashing MinimalHomePage.
class FakeAudioContext { state = 'running'; resume() { return Promise.resolve(); } createBuffer() { return {}; } createBufferSource() { return { connect() {}, start() {}, buffer: null }; } destination = {}; }
(globalThis as unknown as { AudioContext: unknown }).AudioContext ??= FakeAudioContext;

const auth = { authenticated: true, loading: false, needsStart: false, startInProgress: false, pinUnlock: false,
  user: { id: 1, name: 'taylor', is_admin: true }, implicit: false, logout: vi.fn() };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => ({ openMovie: vi.fn(), openTv: vi.fn() }) }));

import { MinimalHomePage, firstPosterUrls, homeParts } from '@/pages/MinimalHomePage';
import type { HomeData } from '@/components/home/homeApi';

const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;

describe('Home with an empty or partial /api/home answer', () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it.each([
    ['{}', {}],
    ['null', null],
    ['no rails', { plex: true, continue_watching: [] }],
    ['no continue_watching', { plex: true, rails: [] }],
    ['rails not a list', { plex: true, continue_watching: [], rails: { a: 1 } }],
    ['a rail without items', { plex: true, continue_watching: [], rails: [{ id: '1', title: 'Movies', type: 'movie' }, null] }],
  ])('%s shows the empty state', async (_name, body) => {
    vi.stubGlobal('fetch', vi.fn(async () => json(body)));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('home-empty')).toBeTruthy());
    expect(screen.getByText('Nothing here yet')).toBeTruthy();
    expect(screen.queryByTestId('home-skeleton')).toBeNull();
  });

  it('keeps the good rails when another is malformed', async () => {
    const body = { plex: true, continue_watching: undefined, rails: [
      { id: 'trakt_anticipated_movies', title: 'Anticipated Movies', type: 'movie', items: [{ key: 'tmdb-movie-1', type: 'movie', title: 'Dune', subtitle: '2027', poster: null, tmdb_id: 1 }] },
      { id: 'x', title: 'Broken', type: 'movie', items: null },
    ] };
    vi.stubGlobal('fetch', vi.fn(async () => json(body)));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    await act(async () => {});
    await waitFor(() => expect(screen.getByTestId('home-rails')).toBeTruthy());
    expect(screen.getAllByRole('heading', { level: 2 }).map(h => h.textContent)).toEqual(['Anticipated Movies']);
  });

  it('homeParts and firstPosterUrls tolerate missing fields', () => {
    expect(homeParts({} as HomeData)).toEqual({ cw: [], rails: [] });
    expect(homeParts(null)).toEqual({ cw: [], rails: [] });
    expect(firstPosterUrls({} as HomeData, 390)).toEqual([]);
    expect(firstPosterUrls({ plex: true } as unknown as HomeData, 390)).toEqual([]);
  });
});
