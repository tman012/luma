import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('@/minimal', () => ({ isMinimal: () => true }));

import { WatchlistAddSheet } from '@/components/movies/WatchlistAddSheet';
import { fetchWatchlistTmdbIds, removeFromWatchlist } from '@/services/watchlistApi';

const SETTINGS = { monitoring: false, preferred_groups: [], trackers: [], qualities: [],
  profiles: [{ id: 1, name: 'Any', items: [], cutoff: null, min_mb_per_min: null, max_mb_per_min: null, default: false },
    { id: 7, name: 'TFlix', items: [], cutoff: null, min_mb_per_min: null, max_mb_per_min: null, default: true }] };

afterEach(() => vi.unstubAllGlobals());

function stub() {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    const body = String(url).includes('/api/settings/movies') ? SETTINGS
      : String(url).endsWith('/tmdb-ids') ? { tmdb_ids: [438631] } : { success: true };
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }));
  return calls;
}

describe('v202 the native movie watchlist (test install)', () => {
  it('reads and removes through the native watchlist, never Radarr', async () => {
    const calls = stub();
    expect([...(await fetchWatchlistTmdbIds())]).toEqual([438631]);
    expect((await removeFromWatchlist(438631)).ok).toBe(true);
    expect(calls.map(c => c.url).join(' ')).not.toMatch(/radarr|\/api\/watchlist\//);
    expect(calls[1]!.url).toContain('/api/movies/watchlist/438631');
    expect(calls[1]!.init?.method).toBe('DELETE');
  });

  it('the profile picker defaults to the default profile and adds', async () => {
    const calls = stub();
    const added = vi.fn();
    render(<WatchlistAddSheet tmdbId={693134} title="Dune: Part Two" year={2024} onClose={() => {}} onAdded={added} />);
    await waitFor(() => expect((screen.getByTestId('watchlist-profile') as HTMLSelectElement).value).toBe('7'));
    fireEvent.click(screen.getByTestId('watchlist-add-confirm'));
    await waitFor(() => expect(added).toHaveBeenCalled());
    const post = calls.find(c => c.init?.method === 'POST')!;
    expect(post.url).toContain('/api/movies/watchlist');
    expect(JSON.parse(String(post.init!.body))).toEqual({ tmdb_id: 693134, title: 'Dune: Part Two', year: 2024, profile_id: 7 });
  });
});
