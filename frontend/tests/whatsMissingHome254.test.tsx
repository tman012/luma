import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

const auth = { authenticated: true, loading: false, needsStart: false, startInProgress: false, pinUnlock: false,
  user: { id: 1, name: 'taylor', is_admin: true }, implicit: false, logout: vi.fn() };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
const modals = { openMovie: vi.fn(), openTv: vi.fn() };
vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => modals }));

import { MinimalHomePage, firstPosterUrls } from '@/pages/MinimalHomePage';
import { LibrariesEditor } from '@/pages/LibrariesPage';
import { missingCards } from '@/components/home/homeApi';
import { LONG_PRESS_MS } from '@/components/home/useLongPress';

const TMDB = 'https://image.tmdb.org/t/p/w342';
const wymCard = (tid: number, title: string, year: number) =>
  ({ key: `missing-${tid}`, type: 'movie', title, subtitle: String(year), tmdb_id: tid, poster: `${TMDB}/p${tid}.jpg` });
const HOME = (dismiss = true) => ({
  plex: true,
  continue_watching: [{ key: '10', type: 'episode', title: 'Severance', subtitle: 'S1 E2', poster: null, art: null, progress: 0.25 }],
  rails: [
    { id: '1', title: 'Movies', type: 'movie', items: [{ key: '21', type: 'movie', title: 'Heat', subtitle: '1995', poster: null }] },
    { id: 'what_youre_missing', title: "What you're missing", type: 'movie', ...(dismiss ? { dismiss: true } : {}),
      items: [wymCard(101, 'Zodiac', 2007), wymCard(102, 'Prisoners', 2013)] },
  ],
});
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("v254 Home: What you're missing", () => {
  it('is a regular rail in its saved place; empty rails stay hidden', async () => {
    const h = HOME();
    h.rails = [h.rails[1]!, h.rails[0]!, { id: 'what_youre_missing_x', title: 'Empty', type: 'movie', items: [] }];
    vi.stubGlobal('fetch', vi.fn(async () => json(h)));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    await waitFor(() => screen.getByTestId('home-rails'));
    expect(screen.getAllByRole('heading', { level: 2 }).map(x => x.textContent)).toEqual(['Continue Watching', "What you're missing", 'Movies']);
  });

  it('its posters count in the poster decode hold like any rail', () => {
    expect(firstPosterUrls(HOME() as never, 390).filter(u => u.includes('/p10'))).toHaveLength(2);
  });

  it('long press (450 ms) opens the dismiss sheet; confirming removes it at once, then takes the server top-up', async () => {
    let resolve!: (r: Response) => void;
    const fetchMock = vi.fn(async (url: string) => {
      if (String(url).includes('/dismiss')) return new Promise<Response>(r => { resolve = r; });
      return json(HOME());
    });
    vi.stubGlobal('fetch', fetchMock);
    const vibrate = vi.fn();
    Object.defineProperty(navigator, 'vibrate', { value: vibrate, configurable: true });
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    const card = await screen.findByTestId('card-missing-101');
    expect(card.className).toContain('longpress-poster');
    vi.useFakeTimers();
    fireEvent.touchStart(card, { touches: [{ clientX: 10, clientY: 10 }] });
    act(() => { vi.advanceTimersByTime(LONG_PRESS_MS - 1); });
    expect(screen.queryByRole('dialog')).toBeNull();
    act(() => { vi.advanceTimersByTime(1); });
    vi.useRealTimers();
    expect(vibrate).toHaveBeenCalledWith(12);
    const sheet = screen.getByRole('dialog', { name: "Remove from what you're missing" });
    expect(sheet.textContent).toContain("What you're missing");
    expect(sheet.textContent).toContain('Zodiac (2007)');
    fireEvent.click(card);                                   // the click that ends a long press doesn't open it
    expect(modals.openMovie).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: "Don't show again" }));
    await waitFor(() => expect(screen.queryByTestId('card-missing-101')).toBeNull());
    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/dismiss'))!;
    expect(JSON.parse(String((call[1] as RequestInit).body))).toEqual({ tmdb_id: 101 });
    await act(async () => { resolve(json({ ok: true, rail: { movies: [
      { tmdb_id: 102, title: 'Prisoners', year: 2013, poster_path: '/p102.jpg' }, { tmdb_id: 103, title: 'Heat', year: 1995, poster_path: '/p103.jpg' }] } })); });
    await waitFor(() => expect(screen.getByTestId('card-missing-103')).toBeTruthy());
  });

  it('a move over 10 px cancels the long press; right-click opens it; a failed dismiss keeps the removal', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => (String(url).includes('/dismiss') ? json({ error: 'x' }, 500) : json(HOME()))));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    const card = await screen.findByTestId('card-missing-101');
    vi.useFakeTimers();
    fireEvent.touchStart(card, { touches: [{ clientX: 10, clientY: 10 }] });
    fireEvent.touchMove(card, { touches: [{ clientX: 25, clientY: 10 }] });
    act(() => { vi.advanceTimersByTime(600); });
    vi.useRealTimers();
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.contextMenu(card);
    fireEvent.click(screen.getByRole('button', { name: "Don't show again" }));
    await waitFor(() => expect(screen.queryByTestId('card-missing-101')).toBeNull());
    await new Promise(r => setTimeout(r, 0));
    expect(screen.queryByTestId('card-missing-101')).toBeNull();
    expect(screen.getByTestId('card-missing-102')).toBeTruthy();
  });

  it('without the owner session (no dismiss flag) a long press does nothing', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(HOME(false))));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    const card = await screen.findByTestId('card-missing-101');
    expect(card.className).not.toContain('longpress-poster');
    fireEvent.contextMenu(card);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('maps the dismiss answer like the server does', () => {
    expect(missingCards([{ tmdb_id: 5, title: 'A', year: 2001, poster_path: '/a.jpg' }, { tmdb_id: 0 }, { tmdb_id: 6, title: 'B', poster_path: null }]))
      .toEqual([{ key: 'missing-5', type: 'movie', title: 'A', subtitle: '2001', tmdb_id: 5, poster: `${TMDB}/a.jpg` },
        { key: 'missing-6', type: 'movie', title: 'B', subtitle: '', tmdb_id: 6, poster: null }]);
  });

  it('Home screen settings lists it with the other rails, with its switch', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ libraries: [
      { id: '1', title: 'Movies', type: 'movie', enabled: true, source: 'plex' },
      { id: 'what_youre_missing', title: "What you're missing", type: 'movie', enabled: true, source: 'wym' }] })));
    render(<MemoryRouter><LibrariesEditor /></MemoryRouter>);
    await waitFor(() => screen.getByTestId('libraries'));
    expect(screen.getByLabelText("Reorder What you're missing")).toBeTruthy();
    expect(screen.getByRole('switch', { name: "What you're missing" }).getAttribute('aria-checked')).toBe('true');
  });
});

