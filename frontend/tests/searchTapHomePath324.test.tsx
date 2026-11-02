import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const host = { openMovie: vi.fn(), openTv: vi.fn(), openPerson: vi.fn(), close: vi.fn(), openVibeEngine: vi.fn(), registerBackHandler: vi.fn() };
vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => host }));
const owr = vi.hoisted(() => ({ calls: [] as unknown[][] }));
vi.mock('@/services/modalPrefetch', async (orig) => {
  const m = await orig<typeof import('@/services/modalPrefetch')>();
  return { ...m, openWhenReady: (...a: Parameters<typeof m.openWhenReady>) => { owr.calls.push(a); return m.openWhenReady(...a); } };
});

import { SearchShelf } from '@/components/search/SearchShelf';
import { _resetModalPrefetch } from '@/services/modalPrefetch';

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
const LIB = { movies: [{ rating_key: 7, tmdb_id: 157336, title: 'Interstellar', year: 2014 }], tv_shows: [], people: [] };
const TMDB = { movies: [], tv_shows: [{ id: 2, name: 'Severance', first_air_date: '2022-02-18', media_type: 'tv', relevance_rank: 1 }], people: [] };

describe('v324 search taps open through Home\'s openWhenReady path', () => {
  beforeEach(() => {
    _resetModalPrefetch(); owr.calls.length = 0;
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    vi.stubGlobal('fetch', vi.fn(async (u: string) => (String(u).includes('/api/library/search') ? json(LIB) : String(u).includes('/search?') ? json(TMDB) : json({}))));
    Object.values(host).forEach((f) => f.mockClear());
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('SearchShelf: pressed row, openWhenReady with the warm ids, popup opens without the old 320 ms close-first delay', async () => {
    const onClose = vi.fn();
    render(<SearchShelf onClose={onClose} initialQuery="inter" />);
    const rows = await screen.findAllByTestId('search-result');
    const t0 = Date.now();
    fireEvent.click(rows[0]!);
    expect(owr.calls[0]!.slice(0, 4)).toEqual(['movie', 7, 157336, rows[0]]);  // v341i: an owned row opens with its Plex key too
    expect(rows[0]!.getAttribute('data-pressed')).toBe('true');
    await waitFor(() => expect(host.openMovie).toHaveBeenCalledWith(expect.objectContaining({ ratingKey: 7, tmdbId: 157336, origin: 'search' })));
    expect(Date.now() - t0).toBeLessThan(320);
    await act(async () => { await new Promise((r) => setTimeout(r, 350)); });
    // v325 motion P1: the shelf stays under the popup; closing the popup returns to it.
    const m = document.createElement('div'); m.setAttribute('data-modal', 'MovieDetailModal'); document.body.appendChild(m);
    await act(async () => { await new Promise((r) => setTimeout(r, 350)); });
    m.remove();
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByTestId('search-shelf')).toBeTruthy();
  });

  it('TV results open the TV popup through the same path', async () => {
    render(<SearchShelf onClose={() => {}} initialQuery="sever" />);
    const rows = await screen.findAllByTestId('search-result');
    fireEvent.click(rows.find((r) => r.textContent?.includes('Severance'))!);
    expect(owr.calls[0]!.slice(0, 3)).toEqual(['tv', undefined, 2]);
    await waitFor(() => expect(host.openTv).toHaveBeenCalledWith(expect.objectContaining({ tmdbId: 2 })));
  });
});
