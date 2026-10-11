/**
 * watchlistApi.ts — Canonical watchlist helpers.
 *
 * Fetches Radarr watchlist TMDb IDs and enriches media items with
 * inWatchlist flags. Single source of truth — consumed by
 * useHomeRails, useExploreRails, and NowAvailablePage.
 */

import { fetchWithTimeout } from '@/utils/fetchWithTimeout';

export async function fetchWatchlistTmdbIds(): Promise<Set<number>> {
  try {
    const resp = await fetchWithTimeout('/api/radarr/watchlist/tmdb-ids');
    if (!resp.ok) return new Set();
    const data = await resp.json();
    return new Set((data.tmdb_ids ?? []) as number[]);
  } catch {
    return new Set();
  }
}

async function fetchSonarrWatchlistTmdbIds(): Promise<Set<number>> {
  try {
    const resp = await fetchWithTimeout('/api/sonarr/watchlist/tmdb-ids');
    if (!resp.ok) return new Set();
    const data = await resp.json();
    return new Set((data.tmdb_ids ?? []) as number[]);
  } catch {
    return new Set();
  }
}

export interface WatchlistIds {
  /** TMDb IDs of monitored Radarr movies. */
  movies: Set<number>;
  /** TMDb IDs of monitored Sonarr series. */
  tv: Set<number>;
}

/** Fetch both Radarr (movie) and Sonarr (TV) watchlist TMDb IDs in parallel. */
export async function fetchWatchlistIds(): Promise<WatchlistIds> {
  const [movies, tv] = await Promise.all([
    fetchWatchlistTmdbIds(),
    fetchSonarrWatchlistTmdbIds(),
  ]);
  return { movies, tv };
}

/**
 * Media-type-aware watchlist enrichment: TV items are matched against the
 * Sonarr set, everything else against the Radarr set. TMDb IDs are not unique
 * across movies vs. shows, so the sets must be checked per media type.
 */
export function enrichWatchlistByType<
  T extends { tmdbId: number | null; mediaType: 'movie' | 'tv' | 'episode'; inWatchlist: boolean },
>(items: T[], ids: WatchlistIds): T[] {
  if (ids.movies.size === 0 && ids.tv.size === 0) return items;
  return items.map((item) => {
    if (!item.tmdbId) return item;
    const set = (item.mediaType === 'tv' || item.mediaType === 'episode') ? ids.tv : ids.movies;
    return set.has(item.tmdbId) ? { ...item, inWatchlist: true } : item;
  });
}

export function enrichWatchlist<T extends { tmdbId: number | null; inWatchlist: boolean }>(
  items: T[],
  watchlistIds: Set<number>,
): T[] {
  if (watchlistIds.size === 0) return items;
  return items.map((item) =>
    item.tmdbId && watchlistIds.has(item.tmdbId)
      ? { ...item, inWatchlist: true }
      : item,
  );
}

/** Dispatch after add/remove so Home rails + Watchlist sheets can refresh. */
export function notifyWatchlistChanged(
  detail: { tmdbId: number; action: 'added' | 'removed' },
): void {
  try {
    window.dispatchEvent(new CustomEvent('watchlist-changed', { detail }));
  } catch {
    // ignore
  }
}

/** Unmonitor in Radarr (DELETE /api/watchlist/:tmdbId). Does not delete the movie. */
export async function removeFromWatchlist(tmdbId: number): Promise<{ ok: boolean; error?: string }> {
  try {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 10_000);
    const resp = await fetch(`/api/watchlist/${tmdbId}`, {
      method: 'DELETE',
      signal: controller.signal,
    }).finally(() => window.clearTimeout(timer));
    const data = (await resp.json().catch(() => ({}))) as { success?: boolean; error?: string; message?: string };
    if (!resp.ok || data.success === false) {
      return { ok: false, error: data.error || data.message || `HTTP ${resp.status}` };
    }
    notifyWatchlistChanged({ tmdbId, action: 'removed' });
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'Network error' };
  }
}

