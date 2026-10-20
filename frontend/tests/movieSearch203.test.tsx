import { cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MovieSearchActions } from '@/components/movies/MovieSearchActions';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function stub(answers: Record<string, unknown>) {
  const calls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    calls.push(String(url));
    const k = Object.keys(answers).find(a => String(url).endsWith(a))!;
    return new Response(JSON.stringify(answers[k]), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }));
  return calls;
}

const REL = { title: 'Dune.Part.Two.2024.1080p.WEB-DL-NTb', tracker: 'IPT', size: 10 * 1024 ** 3, seeders: 20, leechers: 1,
  quality: '1080p-web', quality_label: null, published: null, freeleech: false, download_url: '/x', season: null,
  episodes: [], pack: false, rejections: [], grabbable: true };

describe('v203 the movie page search (E2 dialog and list, reused)', () => {
  it('Search automatically shows Grabbed with the release', async () => {
    const calls = stub({ '/693134/search': { ok: true, error: null, picks: [{ title: REL.title, quality: '1080p-web', size: REL.size, grabbed: true }] } });
    render(<MovieSearchActions tmdbId={693134} title="Dune: Part Two" year={2024} />);
    fireEvent.click(screen.getByTestId('movie-search-auto'));
    await waitFor(() => expect(screen.getByTestId('tv-auto-dialog').getAttribute('data-state')).toBe('grabbed'));
    expect(screen.getByTestId('tv-auto-release').textContent).toBe(REL.title);
    expect(calls[0]).toContain('/api/movies/watchlist/693134/search');
  });

  it('none found offers Interactive search, which lists and grabs', async () => {
    const calls = stub({ '/693134/search': { ok: true, error: null, picks: [] },
      '/693134/interactive': { ok: true, searched: 1, releases: [REL], grabs_allowed: true, error: null },
      '/693134/interactive/grab': { ok: true } });
    render(<MovieSearchActions tmdbId={693134} title="Dune: Part Two" year={2024} />);
    fireEvent.click(screen.getByTestId('movie-search-auto'));
    await waitFor(() => expect(screen.getByTestId('tv-auto-dialog').getAttribute('data-state')).toBe('none'));
    fireEvent.click(screen.getByTestId('tv-auto-interactive'));
    await waitFor(() => expect(screen.getAllByTestId('tv-interactive-row')).toHaveLength(1));
    fireEvent.click(screen.getByTestId('tv-interactive-grab'));
    await waitFor(() => expect(screen.getByTestId('tv-auto-dialog').getAttribute('data-state')).toBe('grabbed'));
    expect(calls.some(c => c.endsWith('/693134/interactive/grab'))).toBe(true);
  });
});
