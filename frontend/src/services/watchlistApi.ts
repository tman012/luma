/**
 * watchlistApi.ts — Canonical watchlist helpers.
 *
 * Fetches Radarr watchlist TMDb IDs and enriches media items with
 * inWatchlist flags. Single source of truth — consumed by
 * useHomeRails, useExploreRails, and NowAvailablePage.
 */

import { fetchWithTimeout } from '@/utils/fetchWithTimeout';
import { apiFetch } from '../apiClient';
import { isMinimal } from '@/minimal';

export async function fetchWatchlistTmdbIds(): Promise<Set<number>> {
  // Minimal (test) builds have no Radarr: v202 the native watchlist instead (movies.db).
  if (isMinimal()) return fetchNativeWatchlistIds();
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

/** v202: the native movie watchlist on the test install (never Radarr). */
export const NATIVE_WATCHLIST_URL = '/api/movies/watchlist';

export async function fetchNativeWatchlistIds(): Promise<Set<number>> {
  try {
    const resp = await fetchWithTimeout(`${NATIVE_WATCHLIST_URL}/tmdb-ids`);
    if (!resp.ok) return new Set();
    const data = await resp.json();
    return new Set((data.tmdb_ids ?? []) as number[]);
  } catch {
    return new Set();
  }
}

/** v231 (Tester): null when the lookup fails (404, error, unreachable), so callers can hide Add instead of
 * treating every title as not in the watchlist. */
export async function fetchNativeWatchlistIdsOrNull(): Promise<Set<number> | null> {
  try {
    const resp = await fetchWithTimeout(`${NATIVE_WATCHLIST_URL}/tmdb-ids`);
    if (!resp.ok) return null;
    const data = await resp.json() as { tmdb_ids?: unknown };
    return Array.isArray(data.tmdb_ids) ? new Set(data.tmdb_ids as number[]) : null;
  } catch {
    return null;
  }
}

export async function addToNativeWatchlist(m: { tmdbId: number; title: string; year?: number | null; profileId?: number | null }):
  Promise<{ ok: boolean; error?: string }> {
  try {
    const resp = await apiFetch(NATIVE_WATCHLIST_URL, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tmdb_id: m.tmdbId, title: m.title, ...(m.year ? { year: m.year } : {}),
        ...(m.profileId != null ? { profile_id: m.profileId } : {}) }),
    });
    if (!resp.ok) return { ok: false, error: "Couldn't add" };
    notifyWatchlistChanged({ tmdbId: m.tmdbId, action: 'added' });
    return { ok: true };
  } catch {
    return { ok: false, error: "Couldn't add" };
  }
}

/** Unmonitor in Radarr (DELETE /api/watchlist/:tmdbId). Does not delete the movie. v202: on the test
 * install (minimal builds) the native watchlist's entry is removed instead; Radarr is never called. */
export async function removeFromWatchlist(tmdbId: number): Promise<{ ok: boolean; error?: string }> {
  if (isMinimal()) {
    try {
      const resp = await apiFetch(`${NATIVE_WATCHLIST_URL}/${tmdbId}`, { method: 'DELETE' });
      if (!resp.ok && resp.status !== 404) return { ok: false, error: "Couldn't remove" };
      notifyWatchlistChanged({ tmdbId, action: 'removed' });
      return { ok: true };
    } catch {
      return { ok: false, error: "Couldn't remove" };
    }
  }
  try {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 10_000);
    const resp = await apiFetch(`/api/watchlist/${tmdbId}`, {
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

