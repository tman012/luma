import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const modals = { openMovie: vi.fn(), openTv: vi.fn(), close: vi.fn() };
vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => modals }));

import { HomeSearchPalette } from '@/components/search/HomeSearchPalette';
import { openSearch } from '@/components/search/MinimalSearchHost';
import { _resetModalPrefetch, openWhenReady, REVEALED_EVENT } from '@/services/modalPrefetch';

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
const LIB = { movies: [{ rating_key: 1, title: 'Dune', year: 2021, tmdb_id: 438631 }], tv_shows: [], people: [] };
const sheet = () => { const m = document.createElement('div'); m.setAttribute('data-modal', 'MovieDetailModal'); m.setAttribute('data-test-sheet', ''); document.body.appendChild(m); };
let reduce = false;

beforeEach(() => {
  _resetModalPrefetch(); reduce = false;
  modals.openMovie.mockReset(); modals.openTv.mockReset();
  vi.stubGlobal('fetch', vi.fn(async (u: string) => json(String(u).includes('/api/library/search') ? LIB : { movies: [], tv_shows: [], people: [] })));
  window.matchMedia = ((q: string) => ({ matches: reduce && q.includes('reduce'), media: q, addEventListener() {}, removeEventListener() {} })) as never;
});
afterEach(() => { document.querySelectorAll('[data-test-sheet]').forEach(n => n.remove()); cleanup(); vi.unstubAllGlobals(); });

describe('v325 motion P1 search: stays under the sheet, one open per tap, instant reduced motion', () => {
  it('openWhenReady: a second tap while the sheet is up is ignored; once it closes a new tap opens', async () => {
    const open = vi.fn(); const closed = vi.fn();
    await openWhenReady('movie', undefined, 1, null, () => { open(); sheet(); }, closed);
    await openWhenReady('movie', undefined, 1, null, open, closed);
    expect(open).toHaveBeenCalledTimes(1);
    await new Promise((r) => setTimeout(r, 20));
    expect(closed).not.toHaveBeenCalled();
    document.querySelectorAll('[data-test-sheet]').forEach(n => n.remove());
    await waitFor(() => expect(closed).toHaveBeenCalledTimes(1));
    await openWhenReady('movie', undefined, 1, null, open);
    expect(open).toHaveBeenCalledTimes(2);
  });

  const setup = async () => {
    render(<HomeSearchPalette />); act(() => openSearch());
    fireEvent.change(screen.getByTestId('search-input'), { target: { value: 'dune' } });
    await waitFor(() => screen.getByTestId('search-results'));
    return document.querySelector<HTMLElement>('.lsp-row')!;
  };

  it('Palette: a double click opens (and pushes) the popup once; the palette stays under it and comes back when it closes', async () => {
    const row = await setup();
    fireEvent.click(row); fireEvent.click(row);
    await waitFor(() => expect(modals.openMovie).toHaveBeenCalled());
    fireEvent.click(row);
    expect(modals.openMovie).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('search-palette')).toBeTruthy();
    expect(document.querySelector('.lsp-root')!.getAttribute('data-handoff')).toBe('true');
    sheet(); window.dispatchEvent(new Event(REVEALED_EVENT));
    await new Promise((r) => setTimeout(r, 300));
    expect(screen.getByTestId('search-palette')).toBeTruthy();
    document.querySelectorAll('[data-test-sheet]').forEach(n => n.remove());
    await waitFor(() => expect(document.querySelector('.lsp-root')!.getAttribute('data-handoff')).not.toBe('true'));
    expect(document.activeElement).toBe(screen.getByTestId('search-input'));
  });

  it('Palette: reduced motion closes in one step (no closing fade)', async () => {
    reduce = true;
    await setup();
    fireEvent.keyDown(screen.getByTestId('search-input'), { key: 'Escape' });
    expect(screen.queryByTestId('search-palette')).toBeNull();
    expect(document.querySelector('.lsp-root[data-closing="true"]')).toBeNull();
  });
});
