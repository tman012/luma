import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const modals = { openMovie: vi.fn(), openTv: vi.fn(), close: vi.fn() };
vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => modals }));

import { HomeSearchPalette, buildGroups, metaLine, autoExpand, CLOSE_MS } from '@/components/search/HomeSearchPalette';
import { openSearch } from '@/components/search/MinimalSearchHost';
import { _resetModalPrefetch } from '@/services/modalPrefetch';
/** v325: a sheet in the DOM (+ the reveal event); removing it is the popup closing. */
function revealSheet() {
  if (!document.querySelector('[data-modal]')) {
    const m = document.createElement('div'); m.setAttribute('data-modal', 'MovieDetailModal'); m.setAttribute('data-test-sheet', '');
    document.body.appendChild(m);
  }
  window.dispatchEvent(new Event('luma:detail-revealed'));
}


const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
const LIB = { movies: [{ rating_key: 1, title: 'Dune', year: 2021, tmdb_id: 438631 }],
  tv_shows: [{ rating_key: 2, title: 'Dune: Prophecy', year: 2024, tmdb_id: 90228 }], people: [] };
const TM = { movies: [{ id: 841, title: 'Dune', release_date: '1984-12-14' }, { id: 5, title: 'Unrelated Thing', release_date: '2000-01-01' }],
  tv_shows: [], people: [{ id: 137427, name: 'Denis Villeneuve', known_for_department: 'Directing', profile_path: '/x.jpg' }] };
let mode: 'ok' | 'empty' | 'fail' | 'hang' = 'ok';
const fetchMock = vi.fn(async (u: string) => {
  if (mode === 'fail') throw new TypeError('network');
  if (mode === 'hang') return new Promise<Response>(() => {});
  const url = String(u);
  if (mode === 'empty') return json({ movies: [], tv_shows: [], people: [] });
  if (url.includes('/api/library/search')) return json(LIB);
  if (url.includes('/search')) return json(TM);
  return json({});
});

beforeEach(() => { _resetModalPrefetch(); document.querySelectorAll('[data-test-sheet]').forEach(n => n.remove()); vi.useFakeTimers({ shouldAdvanceTime: true }); vi.stubGlobal('fetch', fetchMock); mode = 'ok'; modals.openMovie.mockReset(); modals.openTv.mockReset(); });
afterEach(() => { document.querySelectorAll('[data-test-sheet]').forEach((n) => n.remove()); cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

const mount = () => render(<><button type="button" data-testid="opener">Search</button><HomeSearchPalette /></>);
const type = async (q: string) => {
  fireEvent.change(screen.getByTestId('search-input'), { target: { value: q } });
  await act(async () => { vi.advanceTimersByTime(300); });
};

describe('C2 palette states', () => {
  it('rest: dialog with Search placeholder, no chips before 2 characters', () => {
    mount(); act(() => openSearch());
    const d = screen.getByRole('dialog');
    expect(d.getAttribute('aria-modal')).toBe('true');
    expect(screen.getByTestId('search-input').getAttribute('placeholder')).toBe('Search');
    expect(screen.queryByRole('tablist')).toBeNull();
  });
  it('loading: skeleton only after 150 ms, chips visible', async () => {
    mode = 'hang'; mount(); act(() => openSearch());
    await type('dune');
    expect(screen.queryByTestId('search-reserved')).toBeNull();
    await act(async () => { vi.advanceTimersByTime(150); });
    expect(screen.getByTestId('search-reserved')).toBeTruthy();
    expect(screen.getByTestId('search-palette').dataset.state).toBe('loading');
    expect(screen.getAllByRole('tab').map(t => t.textContent)).toEqual(['All', 'Movies', 'TV', 'People']);
  });
  it('results: groups in order, match highlighted, first row highlighted, meta lines', async () => {
    mount(); act(() => openSearch());
    await type('dune');
    await waitFor(() => screen.getByTestId('search-results'));
    expect([...document.querySelectorAll('.lsp-ghd')].map(e => e.textContent)).toEqual(['In your library', 'People']);
    // Prod 1b4ea74: TMDb titles sit behind a collapsed "Find More" button.
    expect(screen.getByTestId('find-more').textContent).toBe('Find More');
    expect(document.querySelector('.lsp-em')!.textContent).toBe('Dune');
    expect(document.querySelector('[data-row="0"]')!.getAttribute('data-hi')).toBe('true');
    expect([...document.querySelectorAll('.lsp-b')].map(e => e.textContent)).toEqual(['2021 · Movie', '2024 · TV', 'Directing']);
  });
  it('Find More expands to the TMDb titles only (prod), its back button collapses', async () => {
    mount(); act(() => openSearch());
    await type('dune');
    await waitFor(() => screen.getByTestId('search-results'));
    fireEvent.click(screen.getByTestId('find-more'));
    expect(screen.queryByTestId('group-library')).toBeNull();
    expect(screen.queryByTestId('group-people')).toBeNull();
    expect(screen.getByTestId('find-more-back').textContent).toBe('Find More');
    expect([...document.querySelectorAll('.lsp-b')].map(e => e.textContent)).toEqual(['1984 · Movie']);
    fireEvent.click(screen.getByTestId('find-more-back'));
    expect(screen.getByTestId('group-library')).toBeTruthy();
    expect(screen.getByTestId('find-more')).toBeTruthy();
  });
  it('arrow keys + Enter open the movie popup over the palette; closing the popup returns to the palette (v325 motion P1)', async () => {
    mount(); screen.getByTestId('opener').focus(); act(() => openSearch());
    await type('dune');
    await waitFor(() => screen.getByTestId('search-results'));
    fireEvent.click(screen.getByTestId('find-more'));
    const input = screen.getByTestId('search-input');
    fireEvent.keyDown(input, { key: 'ArrowDown' }); fireEvent.keyDown(input, { key: 'ArrowUp' });
    expect(input.getAttribute('aria-activedescendant')).toBe('lsp-row-0');
    fireEvent.keyDown(input, { key: 'Enter' });
    // v324: opens through openWhenReady (Home's path), so the popup follows the warm-up (150 ms budget).
    await act(async () => { vi.advanceTimersByTime(700); });
    await waitFor(() => expect(modals.openMovie).toHaveBeenCalledWith(expect.objectContaining({ tmdbId: 841, ratingKey: undefined })));
    // v325 motion P1: the palette stays under the popup; when the popup closes the palette is back, focused.
    expect(screen.getByTestId('search-palette')).toBeTruthy();
    expect(document.querySelector('.lsp-root')!.getAttribute('data-handoff')).toBe('true');
    await act(async () => { revealSheet(); vi.advanceTimersByTime(CLOSE_MS + 50); });
    expect(screen.getByTestId('search-palette')).toBeTruthy();
    document.querySelectorAll('[data-test-sheet]').forEach((n) => n.remove());
    await waitFor(() => expect(document.querySelector('.lsp-root')!.getAttribute('data-handoff')).not.toBe('true'));
    await waitFor(() => expect(document.activeElement).toBe(input));
  });
  it('chips filter as prod: TV shows only TV rows', async () => {
    mount(); act(() => openSearch());
    await type('dune');
    await waitFor(() => screen.getByTestId('search-results'));
    fireEvent.click(screen.getByRole('tab', { name: 'TV' }));
    expect([...document.querySelectorAll('.lsp-b')].map(e => e.textContent)).toEqual(['2024 · TV']);
  });
  it('empty: "No results" only', async () => {
    mode = 'empty'; mount(); act(() => openSearch());
    await type('zzqx');
    await waitFor(() => screen.getByTestId('search-empty'));
    expect(screen.getByTestId('search-empty').textContent).toBe('No results');
  });
  it('error: Couldn\'t search + Try again reruns the last query', async () => {
    mode = 'fail'; mount(); act(() => openSearch());
    await type('dune');
    await waitFor(() => screen.getByTestId('search-error'));
    expect(screen.getByTestId('search-error').textContent).toBe("Couldn't searchTry again");
    mode = 'ok';
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => screen.getByTestId('search-results'));
  });
  it('Esc with text closes in one press; scrim click closes', async () => {
    mount(); act(() => openSearch());
    await type('dune');
    fireEvent.keyDown(screen.getByTestId('search-input'), { key: 'Escape' });
    await act(async () => { vi.advanceTimersByTime(CLOSE_MS); });
    expect(screen.queryByTestId('search-palette')).toBeNull();
    act(() => openSearch());
    fireEvent.mouseDown(screen.getByTestId('search-scrim'));
    await act(async () => { vi.advanceTimersByTime(CLOSE_MS); });
    expect(screen.queryByTestId('search-palette')).toBeNull();
  });
});

describe('C2 grouping', () => {
  it('Find More: collapsed by default, uncapped when open, auto-opens on Movies/TV with nothing owned (never All)', () => {
    const unowned = Array.from({ length: 8 }, (_, i) => ({ title: `Dune ${i}`, year: 2000 + i, id: i, media_type: 'movie' as const }));
    const res = { owned: [], unowned, ownedPeople: [], unownedPeople: [] };
    expect(buildGroups(res, 'all', 'dune')).toEqual([]);
    expect(buildGroups(res, 'all', 'dune', true)[0]!.rows).toHaveLength(8);
    expect(autoExpand(res, 'all')).toBe(false);
    expect(autoExpand(res, 'movies')).toBe(true);
    expect(autoExpand(res, 'tv')).toBe(false);
    expect(metaLine({ kind: 'person', item: { name: 'X', known_for_department: 'Acting' } })).toBe('Acting');
  });
});

describe('C2 Enter/Space on a focused control is that control only (Tester MED)', () => {
  const setup = async () => {
    mount(); act(() => openSearch());
    await type('dune');
    await waitFor(() => screen.getByTestId('search-results'));
  };
  for (const key of ['Enter', ' ']) {
    const k = key === ' ' ? 'Space' : key;
    it(`${k} on Find More opens no row`, async () => {
      await setup();
      const b = screen.getByTestId('find-more');
      fireEvent.keyDown(b, { key });
      expect(modals.openMovie).not.toHaveBeenCalled(); expect(modals.openTv).not.toHaveBeenCalled();
      expect(screen.getByTestId('search-palette')).toBeTruthy();
    });
    it(`${k} on ‹ Find More opens no row`, async () => {
      await setup();
      fireEvent.click(screen.getByTestId('find-more'));
      fireEvent.keyDown(screen.getByTestId('find-more-back'), { key });
      expect(modals.openMovie).not.toHaveBeenCalled();
      expect(screen.getByTestId('search-palette')).toBeTruthy();
    });
    it(`${k} on a chip opens no row`, async () => {
      await setup();
      fireEvent.keyDown(screen.getByRole('tab', { name: 'Movies' }), { key });
      expect(modals.openMovie).not.toHaveBeenCalled();
      expect(screen.getByTestId('search-palette')).toBeTruthy();
    });
    it(`${k} on Try again opens no row`, async () => {
      mode = 'fail'; mount(); act(() => openSearch());
      await type('dune');
      await waitFor(() => screen.getByTestId('search-error'));
      fireEvent.keyDown(screen.getByRole('button', { name: 'Try again' }), { key });
      expect(modals.openMovie).not.toHaveBeenCalled();
      expect(screen.getByTestId('search-palette')).toBeTruthy();
    });
  }
  it('Enter in the input still opens the highlighted row', async () => {
    await setup();
    fireEvent.keyDown(screen.getByTestId('search-input'), { key: 'Enter' });
    await waitFor(() => expect(modals.openMovie).toHaveBeenCalledWith(expect.objectContaining({ ratingKey: 1 })));
  });
});

describe('C2 Find More focus (Tester)', () => {
  it('focus moves to the button that replaces the pressed one', async () => {
    mount(); act(() => openSearch());
    await type('dune');
    await waitFor(() => screen.getByTestId('search-results'));
    screen.getByTestId('find-more').focus();
    fireEvent.click(screen.getByTestId('find-more'));
    expect(document.activeElement).toBe(screen.getByTestId('find-more-back'));
    fireEvent.click(screen.getByTestId('find-more-back'));
    expect(document.activeElement).toBe(screen.getByTestId('find-more'));
  });
});

