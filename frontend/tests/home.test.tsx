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
import { LibrariesEditor, LibrariesRedirect } from '@/pages/LibrariesPage';
import { Route, Routes } from 'react-router';
import { move } from '@/components/home/homeApi';
import { _resetModalPrefetch } from '@/services/modalPrefetch';

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
    // v260: the Home row's Storage pill reads /api/storage/downloads too; Home itself asks once.
    const homeCalls = () => fetchMock.mock.calls.filter(c => String((c as unknown[])[0]).includes('/api/home'));
    expect(homeCalls()).toHaveLength(1);
    expect(fetchMock.mock.calls.every(c => /\/api\/(home|storage\/downloads)/.test(String((c as unknown[])[0])))).toBe(true);
    await act(async () => { d.resolve(json(HOME)); });
    await waitFor(() => expect(screen.getByTestId('home-rails')).toBeTruthy());
    expect(screen.queryByTestId('home-skeleton')).toBeNull();
    const titles = screen.getAllByRole('heading', { level: 2 }).map(h => h.textContent);
    expect(titles).toEqual(['Continue watching', 'Movies', 'TV Shows']);
    expect((screen.getByTestId('progress') as HTMLElement).style.width).toBe('25%');
    expect(screen.queryByRole('button', { name: 'Sign out' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Menu' })).toBeNull();   // logo and avatar only
    fireEvent.click(screen.getByRole('button', { name: 'Account' }));
    // Phone: a right-side account panel with the username, Settings and Sign out.
    const panel = screen.getByTestId('account-panel');
    expect(panel.getAttribute('role')).toBe('dialog');
    expect(panel.style.width).toBe('82vw');
    expect(panel.style.maxWidth).toBe('340px');
    expect(screen.getByTestId('account-name').textContent).toBe('taylor');
    const rows = [...panel.querySelectorAll('button')];
    expect(rows.map(b => b.textContent?.trim())).toEqual(['Settings', 'Sign out']);
    expect(rows[0]!.style.height).toBe('52px');
    fireEvent.click(screen.getByTestId('account-scrim'));
    await waitFor(() => expect(screen.queryByTestId('account-panel')).toBeNull());
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
    _resetModalPrefetch();
    vi.stubGlobal('fetch', vi.fn(async () => json({ ...HOME, continue_watching: [{ ...HOME.continue_watching[0], show_key: '9', season: 1, episode: 2 }] })));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    await waitFor(() => screen.getByTestId('home-rails'));
    // The popup opens once it's ready (or at the 600 ms cap), never on the
    // tap itself; the tapped card shows a pressed state meanwhile.
    fireEvent.click(screen.getByTestId('card-21'));
    expect(modals.openMovie).not.toHaveBeenCalled();
    expect(screen.getByTestId('card-21').getAttribute('data-pressed')).toBe('true');
    await waitFor(() => expect(modals.openMovie).toHaveBeenCalledWith({ ratingKey: 21, origin: 'continue_watching' }), { timeout: 1500 });
    fireEvent.click(screen.getByTestId('card-10'));
    await waitFor(() => expect(modals.openTv).toHaveBeenLastCalledWith({ ratingKey: 9, origin: 'continue_watching',
      episodeContext: { seasonNumber: 1, episodeNumber: 2, episodeRatingKey: 10 } }), { timeout: 1500 });
    fireEvent.click(screen.getByTestId('card-31'));
    await waitFor(() => expect(modals.openTv).toHaveBeenLastCalledWith({ ratingKey: 31, origin: 'recently_added_tv' }), { timeout: 1500 });
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

  it("gives up on the skeleton after 7 s", async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    expect(screen.getByTestId('home-skeleton')).toBeTruthy();
    await act(async () => { vi.advanceTimersByTime(6900); });
    expect(screen.queryByTestId('home-down')).toBeNull();
    await act(async () => { vi.advanceTimersByTime(200); });
    expect(screen.getByTestId('home-down')).toBeTruthy();
    vi.useRealTimers();
  });

  it('v251: all rails ready: the skeleton holds, then one swap shows every rail', async () => {
    const d = deferred<Response>();
    vi.stubGlobal('fetch', vi.fn(() => d.promise));
    const seen: string[] = [];
    const obs = new MutationObserver(() => {
      const s = screen.queryByTestId('home-skeleton') ? 'skeleton' : `rails:${screen.queryAllByRole('heading', { level: 2 }).length}`;
      if (seen[seen.length - 1] !== s) seen.push(s);
    });
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    obs.observe(document.body, { childList: true, subtree: true });
    expect(screen.queryAllByRole('heading', { level: 2 })).toHaveLength(0);
    await act(async () => { d.resolve(json(HOME)); });
    await waitFor(() => expect(screen.getByTestId('home-rails')).toBeTruthy());
    obs.disconnect();
    expect(seen.filter(x => x !== 'skeleton')).toEqual(['rails:3']);       // never a partial set of rails
    expect(screen.queryByTestId('home-skeleton')).toBeNull();
  });

  it('v251: a failed rail is left out and the rest render (no skeleton for it)', async () => {
    // The server drops a rail that errors (app/home_rails.home); the phone renders what came back.
    vi.stubGlobal('fetch', vi.fn(async () => json({ ...HOME, rails: [HOME.rails[0]] })));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    await waitFor(() => screen.getByTestId('home-rails'));
    expect(screen.getAllByRole('heading', { level: 2 }).map(h => h.textContent)).toEqual(['Continue watching', 'Movies']);
    expect(screen.queryByTestId('home-skeleton')).toBeNull();
    expect(screen.queryByTestId('rail-2')).toBeNull();
  });

  describe('v251 poster decode hold', () => {
    const POSTERS = {
      ...HOME,
      continue_watching: [{ ...HOME.continue_watching[0], art: '/api/img/plex?url=art10' }],
      rails: [
        { id: '1', title: 'Movies', type: 'movie', items: [1, 2, 3, 4, 5, 6, 7, 8].map(i => ({ key: `m${i}`, type: 'movie', title: `M${i}`, subtitle: '', poster: `/api/img/plex?url=m${i}` })) },
        { id: '2', title: 'TV Shows', type: 'show', items: [{ key: 't1', type: 'show', title: 'T1', subtitle: '', poster: '/api/img/plex?url=t1' }] },
      ],
    };
    type Pending = { src: string; resolve: () => void; reject: () => void };
    let made: Pending[] = [];
    let mode: 'hold' | 'hang' | 'error' = 'hold';
    class FakeImage {
      decoding = '';
      src = '';
      decode() {
        return new Promise<void>((resolve, reject) => {
          made.push({ src: this.src, resolve, reject });
          if (mode === 'error') reject(new Error('404'));
        });
      }
    }
    beforeEach(() => { made = []; mode = 'hold'; vi.stubGlobal('Image', FakeImage); });

    it('waits for the first posters to decode, using the tiles\' own URLs', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => json(POSTERS)));
      render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
      await waitFor(() => expect(made.length).toBeGreaterThan(0));
      await act(async () => { await new Promise(r => setTimeout(r, 50)); });
      expect(screen.getByTestId('home-skeleton')).toBeTruthy();
      expect(screen.queryByTestId('home-rails')).toBeNull();
      const srcs = made.map(m => m.src);
      expect(srcs).toContain('/api/img/plex?url=art10');          // Continue watching decodes its art (what WideCard shows)
      expect(srcs).toContain('/api/img/plex?url=t1');
      expect(srcs).not.toContain('/api/img/plex?url=m8');         // past the first screen width
      await act(async () => { made.forEach(m => m.resolve()); });
      await waitFor(() => expect(screen.getByTestId('home-rails')).toBeTruthy());
      const tileSrcs = [...document.querySelectorAll('[data-testid="home-rails"] img')].map(i => i.getAttribute('src'));
      for (const u of srcs) expect(tileSrcs).toContain(u);         // same URL: the tile reads the decoded copy from cache
    });

    it('reveals at the 1.5 s cap when a decode hangs', async () => {
      vi.useFakeTimers();
      mode = 'hang';
      vi.stubGlobal('fetch', vi.fn(async () => json(POSTERS)));
      render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
      await act(async () => { await vi.advanceTimersByTimeAsync(10); });
      expect(made.length).toBeGreaterThan(0);
      await act(async () => { await vi.advanceTimersByTimeAsync(1480); });
      expect(screen.getByTestId('home-skeleton')).toBeTruthy();
      await act(async () => { await vi.advanceTimersByTimeAsync(30); });
      expect(screen.getByTestId('home-rails')).toBeTruthy();
      expect(screen.queryByTestId('home-skeleton')).toBeNull();
      vi.useRealTimers();
    });

    it('an errored poster counts as done', async () => {
      mode = 'error';
      vi.stubGlobal('fetch', vi.fn(async () => json(POSTERS)));
      const t0 = Date.now();
      render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
      await waitFor(() => expect(screen.getByTestId('home-rails')).toBeTruthy());
      expect(Date.now() - t0).toBeLessThan(1000);
    });

    it('one fade: skeleton, then every rail at once', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => json(POSTERS)));
      const seen: string[] = [];
      const obs = new MutationObserver(() => {
        const st = screen.queryByTestId('home-skeleton') ? 'skeleton' : `rails:${screen.queryAllByRole('heading', { level: 2 }).length}`;
        if (seen[seen.length - 1] !== st) seen.push(st);
      });
      render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
      obs.observe(document.body, { childList: true, subtree: true });
      await waitFor(() => expect(made.length).toBeGreaterThan(0));
      // decode one rail's posters at a time: nothing may show until the last one is done
      for (const m of made) {
        await act(async () => { m.resolve(); });
        if (m !== made[made.length - 1]) expect(screen.queryByTestId('home-rails')).toBeNull();
      }
      await waitFor(() => expect(screen.getByTestId('home-rails')).toBeTruthy());
      obs.disconnect();
      expect(seen.filter(x => x !== 'skeleton')).toEqual(['rails:3']);
      expect(document.querySelectorAll('.luma-rails-in')).toHaveLength(1);   // one container, one fade
    });
  });

  it('v251: past the timeout a late answer is dropped, never popped in', async () => {
    vi.useFakeTimers();
    const d = deferred<Response>();
    vi.stubGlobal('fetch', vi.fn(() => d.promise));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    await act(async () => { vi.advanceTimersByTime(7100); });
    expect(screen.getByTestId('home-down')).toBeTruthy();
    expect(screen.queryByTestId('home-skeleton')).toBeNull();
    await act(async () => { d.resolve(json(HOME)); });
    await act(async () => { vi.advanceTimersByTime(100); });
    expect(screen.queryByTestId('home-rails')).toBeNull();
    expect(screen.getByTestId('home-down')).toBeTruthy();
    vi.useRealTimers();
  });

  it('hides Settings for non-admins', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(HOME)));
    auth.user = { id: 2, name: 'guest', is_admin: false };
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    await waitFor(() => screen.getByTestId('home-rails'));
    fireEvent.click(screen.getByRole('button', { name: 'Account' }));
    expect([...screen.getByTestId('account-panel').querySelectorAll('button')].map(i => i.textContent?.trim())).toEqual(['Sign out']);
    auth.user = { id: 1, name: 'taylor', is_admin: true };
  });
});

describe('Libraries', () => {
  beforeEach(() => { vi.restoreAllMocks(); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('says No libraries with no libraries', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ libraries: [] })));
    render(<MemoryRouter><LibrariesEditor /></MemoryRouter>);
    expect((await screen.findByTestId('libraries-empty')).textContent).toBe('No libraries');
  });

  it('the Home screen libraries list has 44px controls', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ libraries: [
      { id: '1', title: 'Movies', type: 'movie', enabled: true }, { id: '2', title: 'TV Shows', type: 'show', enabled: false }] })));
    render(<MemoryRouter><LibrariesEditor /></MemoryRouter>);
    await waitFor(() => screen.getByTestId('libraries'));
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('Libraries');
    for (const b of [screen.getByLabelText('Reorder Movies'), screen.getByLabelText('Reorder TV Shows'), ...screen.getAllByRole('switch')]) {
      expect(parseInt(b.style.width, 10)).toBeGreaterThanOrEqual(44);
      expect(parseInt(b.style.height, 10)).toBeGreaterThanOrEqual(44);
    }
  });

  it('/settings/libraries redirects to /settings/home-screen', () => {
    render(<MemoryRouter initialEntries={['/settings/libraries']}><Routes>
      <Route path="/settings/libraries" element={<LibrariesRedirect />} />
      <Route path="/settings/home-screen" element={<p>settings page</p>} />
    </Routes></MemoryRouter>);
    expect(screen.getByText('settings page')).toBeTruthy();
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
    fireEvent.keyDown(screen.getByLabelText('Reorder TV Shows'), { key: 'ArrowUp' });
    await waitFor(() => expect(screen.getAllByRole('switch')[0]!.getAttribute('aria-label')).toBe('TV Shows'));
    put = fetchMock.mock.calls.filter(c => c[1]?.method === 'PUT').pop()!;
    expect(JSON.parse(String(put[1]!.body)).libraries.map((l: { id: string }) => l.id)).toEqual(['2', '1']);
    await waitFor(() => expect(document.activeElement?.getAttribute('aria-label')).toBe('Reorder TV Shows'));
  });

  it('drag handle reorders by pointer: lift, shift others, drop', async () => {
    const libs = [
      { id: '1', title: 'Movies', type: 'movie', enabled: true },
      { id: '2', title: 'TV Shows', type: 'show', enabled: false },
      { id: '3', title: 'Kids', type: 'movie', enabled: true },
    ];
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'PUT'
      ? json({ libraries: JSON.parse(String(init.body)).libraries.map((b: { id: string; enabled: boolean }) => ({ ...libs.find(l => l.id === b.id)!, enabled: b.enabled })) })
      : json({ libraries: libs }));
    vi.stubGlobal('fetch', fetchMock);
    render(<MemoryRouter><LibrariesEditor /></MemoryRouter>);
    await waitFor(() => screen.getByTestId('libraries'));
    const h = screen.getByLabelText('Reorder Movies');
    expect(h.style.touchAction).toBe('none');
    fireEvent.pointerDown(h, { clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(h, { clientY: 100 + 2 * 56, pointerId: 1 });
    const rows = screen.getAllByTestId('library-row');
    expect(rows[0]!.getAttribute('data-lifted')).toBe('true');
    expect(rows[0]!.style.transform).toContain('scale(1.02)');
    expect(rows[1]!.style.transform).toBe('translateY(-56px)');
    expect(rows[2]!.style.transform).toBe('translateY(-56px)');
    fireEvent.pointerUp(h, { clientY: 212, pointerId: 1 });
    await waitFor(() => expect(screen.getAllByRole('switch').map(s => s.getAttribute('aria-label'))).toEqual(['TV Shows', 'Kids', 'Movies']));
    const put = fetchMock.mock.calls.filter(c => c[1]?.method === 'PUT').pop()!;
    expect(JSON.parse(String(put[1]!.body)).libraries.map((l: { id: string }) => l.id)).toEqual(['2', '3', '1']);
  });

  it("a failed save reverts and says Couldn't save", async () => {
    const libs = [{ id: '1', title: 'Movies', type: 'movie', enabled: true }];
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'PUT' ? json({}, false) : json({ libraries: libs })));
    render(<MemoryRouter><LibrariesEditor /></MemoryRouter>);
    await waitFor(() => screen.getByTestId('libraries'));
    fireEvent.click(screen.getByRole('switch'));
    await waitFor(() => expect(screen.getByTestId('notice-toast').textContent).toContain("Couldn't save"));
    expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe('true');
  });
});
