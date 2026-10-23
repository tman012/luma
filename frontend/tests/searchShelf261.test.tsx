import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const host = { openMovie: vi.fn(), openTv: vi.fn(), openPerson: vi.fn(), close: vi.fn(), openVibeEngine: vi.fn(), registerBackHandler: vi.fn() };
vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => host }));

import { SearchShelf } from '@/components/search/SearchShelf';
import { MinimalSearchHost, openSearch } from '@/components/search/MinimalSearchHost';

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
const LIB = { movies: [{ rating_key: 7, title: 'Interstellar', year: 2014, poster_url: '/api/img/plex?url=%2Fx' }], tv_shows: [], people: [] };
const TMDB = {
  movies: [{ id: 157336, title: 'Interstellar', release_date: '2014-11-05', poster_path: '/i.jpg', media_type: 'movie', relevance_rank: 0 },
           { id: 9, title: 'Interstate 60', release_date: '2002-04-13', poster_path: '/s.jpg', media_type: 'movie', relevance_rank: 2 }],
  tv_shows: [{ id: 2, name: 'Inter Milan', first_air_date: '2020-01-01', poster_path: null, media_type: 'tv', relevance_rank: 1 }],
  people: [{ id: 3, name: 'Matthew McConaughey', profile_path: '/m.jpg', popularity: 9, known_for_department: 'Acting' }],
};
let fetchMock: ReturnType<typeof vi.fn>;

describe('v261 phone search is prod 1b4ea74 SearchShelf', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchMock = vi.fn(async (u: string) => (String(u).includes('/api/library/search') ? json(LIB) : json(TMDB)));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__ = true;
    Object.values(host).forEach((f) => f.mockClear());
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__ = undefined; });

  const type = async (v: string) => {
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: v } });
  };

  it('header, field, pills; 300 ms debounce with a spinner; no search under 2 characters', async () => {
    render(<SearchShelf onClose={() => {}} />);
    expect(screen.getByText('Search')).toBeTruthy();
    expect(screen.getByLabelText('Close')).toBeTruthy();
    expect(screen.getByPlaceholderText('Search for titles, people, and more...')).toBeTruthy();
    for (const p of ['All', 'Movies', 'TV', 'People']) expect(screen.getByRole('button', { name: p })).toBeTruthy();
    await type('i');
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    expect(fetchMock).not.toHaveBeenCalled();
    await type('inter');
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    expect(fetchMock).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('every result, owned first and deduped, then people; prod Title Case count; filters; proxied art', async () => {
    render(<SearchShelf onClose={() => {}} />);
    await type('inter');
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    const rows = await screen.findAllByTestId('search-result');
    expect(rows.map((r) => r.textContent)).toEqual([
      'Interstellar2014 · Movie', 'Inter Milan2020 · TV Show', 'Interstate 602002 · Movie', 'Matthew McConaugheyActing',
    ]);
    expect(screen.getByText('4 Results')).toBeTruthy();
    expect(rows[2]!.querySelector('img')!.getAttribute('src')).toContain('/api/img/tmdb');
    fireEvent.click(screen.getByRole('button', { name: 'People' }));
    expect(screen.getAllByTestId('search-result')).toHaveLength(1);
    expect(screen.getByText('1 Result')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'TV' }));
    expect(screen.getAllByTestId('search-result')[0]!.textContent).toContain('Inter Milan');
  });

  it('a title closes the shelf, then opens its popup with the search text (Sources seed) and the cast handler', async () => {
    const onClose = vi.fn();
    const ps = vi.fn();
    render(<SearchShelf onClose={onClose} onPersonSearch={ps} />);
    await type('inter');
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    fireEvent.click((await screen.findAllByTestId('search-result'))[0]!);
    await act(async () => { await vi.advanceTimersByTimeAsync(330); });
    expect(onClose).toHaveBeenCalled();
    expect(host.openMovie).toHaveBeenCalledWith(expect.objectContaining({ ratingKey: 7, searchQuery: 'inter', onPersonSearch: ps }));
  });

  it('a person opens the person modal', async () => {
    render(<SearchShelf onClose={() => {}} />);
    await type('inter');
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    fireEvent.click((await screen.findAllByTestId('search-result'))[3]!);
    await act(async () => { await vi.advanceTimersByTimeAsync(330); });
    expect(host.openPerson).toHaveBeenCalledWith({ personId: 3, personName: 'Matthew McConaughey' });
  });

  it('empty state; TMDb rate limiting shows the same "No results found"', async () => {
    fetchMock.mockImplementation(async (u: string) => json(String(u).includes('/api/library') ? { movies: [], tv_shows: [], people: [] }
      : { movies: [], tv_shows: [], people: [] }));
    render(<SearchShelf onClose={() => {}} />);
    await type('zzz');
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    expect(await screen.findByText('No results found')).toBeTruthy();
    fetchMock.mockImplementation(async (u: string) => json(String(u).includes('/api/library') ? { movies: [], tv_shows: [], people: [] }
      : { movies: [], tv_shows: [], people: [], rate_limited: true }));
    await type('zzzz');
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    expect(await screen.findByText('No results found')).toBeTruthy();
    expect(screen.queryByText(/TMDB is busy/)).toBeNull();
  });

  it('scrolling the results dismisses the keyboard', async () => {
    render(<SearchShelf onClose={() => {}} />);
    const input = screen.getByRole('searchbox');
    input.focus();
    expect(document.activeElement).toBe(input);
    const scroller = document.querySelector('[data-scroll-container]')!;
    fireEvent.scroll(scroller);
    expect(document.activeElement).not.toBe(input);
  });

  it('the phone host mounts the shelf on open-search; a cast tap with only a name reopens it pre-filled', async () => {
    window.matchMedia = ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} })) as never;
    render(<MinimalSearchHost />);
    expect(screen.queryByTestId('search-shelf')).toBeNull();
    act(() => openSearch());
    expect(screen.getByTestId('search-shelf')).toBeTruthy();
    await type('inter');
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    fireEvent.click((await screen.findAllByTestId('search-result'))[0]!);
    await act(async () => { await vi.advanceTimersByTimeAsync(700); });
    expect(screen.queryByTestId('search-shelf')).toBeNull();
    const cast = host.openMovie.mock.calls[0]![0].onPersonSearch as (n: string, id?: number) => void;
    act(() => cast('Anne Hathaway'));
    expect(host.close).toHaveBeenCalled();
    expect((screen.getByRole('searchbox') as HTMLInputElement).value).toBe('Anne Hathaway');
    act(() => cast('Jessica Chastain', 83002));
    await act(async () => { await vi.advanceTimersByTimeAsync(330); });
    expect(host.openPerson).toHaveBeenCalledWith({ personId: 83002, personName: 'Jessica Chastain' });
  });
});

describe('v262 PM: every shelf string is prod 1b4ea74 verbatim', () => {
  it('copy matches prod', async () => {
    const { readFileSync } = await import('node:fs');
    const { resolve } = await import('node:path');
    const src = readFileSync(resolve(__dirname, '../src/components/search/SearchShelf.tsx'), 'utf8');
    for (const t of ["'All'", "'Movies'", "'TV'", "'People'", '>Search</span>', 'aria-label="Close"',
      'placeholder="Search for titles, people, and more..."', "'Result' : 'Results'", '>No results found</p>',
      "'Person'", "'TV Show'", "'Movie'"]) expect(src).toContain(t);
    expect(src).not.toContain('TMDB_BUSY_TEXT');
    expect(src).not.toMatch(/'TV show'|'results'/);
  });
});
