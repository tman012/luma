import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The voice module primes an AudioContext on the first click; jsdom has none.
class FakeAudioContext { state = 'running'; resume() { return Promise.resolve(); } createBuffer() { return {}; } createBufferSource() { return { connect() {}, start() {}, buffer: null }; } destination = {}; }
(globalThis as unknown as { AudioContext: unknown }).AudioContext ??= FakeAudioContext;

const auth = { authenticated: true, loading: false, needsStart: false, startInProgress: false, pinUnlock: false,
  user: { id: 1, name: 'taylor', is_admin: true }, implicit: false, logout: vi.fn() };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
const modals = { openMovie: vi.fn(), openTv: vi.fn() };
vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => modals }));

import { MinimalHomePage } from '@/pages/MinimalHomePage';
import { LibrariesEditor } from '@/pages/LibrariesPage';
import { move } from '@/components/home/homeApi';

const HOME = {
  plex: true,
  continue_watching: [{ key: '10', type: 'episode', title: 'Severance', subtitle: 'S1 E2', poster: '/api/img/plex?url=a', art: null, progress: 0.25 }],
  rails: [
    { id: '1', title: 'Movies', type: 'movie', items: [{ key: '21', type: 'movie', title: 'Heat', subtitle: '1995', poster: null }] },
    { id: '2', title: 'TV Shows', type: 'show', items: [{ key: '31', type: 'season', title: 'Andor', subtitle: 'Season 2', poster: null }] },
    { id: '4', title: 'Empty', type: 'movie', items: [] },
  ],
};

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>(r => { resolve = r; });
  return { promise, resolve };
}
const json = (body: unknown, ok = true) => ({ ok, status: ok ? 200 : 500, json: async () => body }) as Response;

describe('Home', () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('shows skeletons, then reveals every rail together', async () => {
    const d = deferred<Response>();
    const fetchMock = vi.fn(() => d.promise);
    vi.stubGlobal('fetch', fetchMock);
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    expect(screen.getByTestId('home-skeleton')).toBeTruthy();
    expect(screen.queryByTestId('home-rails')).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toContain('/api/home');
    await act(async () => { d.resolve(json(HOME)); });
    await waitFor(() => expect(screen.getByTestId('home-rails')).toBeTruthy());
    expect(screen.queryByTestId('home-skeleton')).toBeNull();
    const titles = screen.getAllByRole('heading', { level: 2 }).map(h => h.textContent);
    expect(titles).toEqual(['Continue watching', 'Movies', 'TV Shows']);
    expect((screen.getByTestId('progress') as HTMLElement).style.width).toBe('25%');
    expect(screen.queryByRole('button', { name: 'Sign out' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Account' }));
    const items = screen.getAllByRole('menuitem').map(i => i.textContent?.trim());
    expect(items).toEqual(['Libraries', 'Sign out']);
  });

  it('scrolls: the page unlocks html/body, rails scroll sideways', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(HOME)));
    const view = render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    await waitFor(() => screen.getByTestId('home-rails'));
    expect(document.documentElement.style.overflow).toBe('auto');
    expect(document.body.style.overflow).toBe('auto');
    const row = screen.getByTestId('rail-continue').querySelector('.luma-rail') as HTMLElement;
    expect(row.style.overflowX).toBe('auto');
    expect(row.style.touchAction).toBe('pan-x pan-y');
    view.unmount();
    expect(document.documentElement.style.overflow).toBe('');
  });

  it('shows Retry when Home fails, then loads', async () => {
    const fetchMock = vi.fn(async () => { throw new Error('offline'); });
    vi.stubGlobal('fetch', fetchMock);
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    const retry = await screen.findByRole('button', { name: 'Retry' });
    fetchMock.mockImplementation(async () => json(HOME));
    await act(async () => { fireEvent.click(retry); });
    await waitFor(() => expect(screen.getByTestId('home-rails')).toBeTruthy());
  });

  it('tapping a poster or Continue card opens the detail popup', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ ...HOME, continue_watching: [{ ...HOME.continue_watching[0], show_key: '9', season: 1, episode: 2 }] })));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    await waitFor(() => screen.getByTestId('home-rails'));
    fireEvent.click(screen.getByTestId('card-21'));
    expect(modals.openMovie).toHaveBeenCalledWith({ ratingKey: 21, origin: 'continue_watching' });
    fireEvent.click(screen.getByTestId('card-10'));
    expect(modals.openTv).toHaveBeenLastCalledWith({ ratingKey: 9, origin: 'continue_watching',
      episodeContext: { seasonNumber: 1, episodeNumber: 2, episodeRatingKey: 10 } });
    fireEvent.click(screen.getByTestId('card-31'));
    expect(modals.openTv).toHaveBeenLastCalledWith({ ratingKey: 31, origin: 'recently_added_tv' });
    // Continue cards keep the backdrop look (art + progress bar).
    expect(screen.getByTestId('card-10').querySelector('[data-testid="progress"]')).toBeTruthy();
  });

  it('says Nothing here yet when every rail is empty', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ plex: true, continue_watching: [], rails: [] })));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    expect((await screen.findByTestId('home-empty')).textContent).toBe('Nothing here yet');
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  });

  it("says Can't reach Plex with a Retry pill when Plex is down", async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ plex: false, continue_watching: [], rails: [] })));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    const box = await screen.findByTestId('home-down');
    expect(box.textContent).toBe("Can't reach PlexRetry");
  });

  it("gives up on the skeleton after 8 s", async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    expect(screen.getByTestId('home-skeleton')).toBeTruthy();
    await act(async () => { vi.advanceTimersByTime(7900); });
    expect(screen.queryByTestId('home-down')).toBeNull();
    await act(async () => { vi.advanceTimersByTime(200); });
    expect(screen.getByTestId('home-down')).toBeTruthy();
    vi.useRealTimers();
  });

  it('hides Libraries for non-admins', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(HOME)));
    auth.user = { id: 2, name: 'guest', is_admin: false };
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    await waitFor(() => screen.getByTestId('home-rails'));
    fireEvent.click(screen.getByRole('button', { name: 'Account' }));
    expect(screen.getAllByRole('menuitem').map(i => i.textContent?.trim())).toEqual(['Sign out']);
    auth.user = { id: 1, name: 'taylor', is_admin: true };
  });
});

describe('Libraries', () => {
  beforeEach(() => { vi.restoreAllMocks(); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('says Nothing here yet with no libraries', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ libraries: [] })));
    render(<MemoryRouter><LibrariesEditor /></MemoryRouter>);
    expect((await screen.findByTestId('libraries-empty')).textContent).toBe('Nothing here yet');
  });

  it('move reorders within bounds', () => {
    expect(move([1, 2, 3], 0, 1)).toEqual([2, 1, 3]);
    expect(move([1, 2, 3], 0, -1)).toEqual([1, 2, 3]);
    expect(move([1, 2, 3], 2, 3)).toEqual([1, 2, 3]);
  });

  it('lists libraries, toggles and reorders, saving server-side', async () => {
    const libs = [
      { id: '1', title: 'Movies', type: 'movie', enabled: true },
      { id: '2', title: 'TV Shows', type: 'show', enabled: true },
    ];
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === 'PUT') {
        const body = JSON.parse(String(init.body));
        return json({ libraries: body.libraries.map((b: { id: string; enabled: boolean }) => ({ ...libs.find(l => l.id === b.id)!, enabled: b.enabled })) });
      }
      return json({ libraries: libs });
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<MemoryRouter><LibrariesEditor /></MemoryRouter>);
    await waitFor(() => screen.getByTestId('libraries'));
    const switches = screen.getAllByRole('switch');
    expect(switches.map(s => s.getAttribute('aria-checked'))).toEqual(['true', 'true']);
    fireEvent.click(switches[0]!);
    await waitFor(() => expect(screen.getAllByRole('switch')[0]!.getAttribute('aria-checked')).toBe('false'));
    let put = fetchMock.mock.calls.filter(c => c[1]?.method === 'PUT').pop()!;
    expect(JSON.parse(String(put[1]!.body))).toEqual({ libraries: [{ id: '1', enabled: false }, { id: '2', enabled: true }] });
    fireEvent.click(screen.getByLabelText('Move TV Shows up'));
    await waitFor(() => expect(screen.getAllByRole('switch')[0]!.getAttribute('aria-label')).toBe('TV Shows'));
    put = fetchMock.mock.calls.filter(c => c[1]?.method === 'PUT').pop()!;
    expect(JSON.parse(String(put[1]!.body)).libraries.map((l: { id: string }) => l.id)).toEqual(['2', '1']);
  });
});
