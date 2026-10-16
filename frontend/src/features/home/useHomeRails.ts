/**
 * Shared data hook for Home rails.
 *
 * Synchronized loading: all rails reveal together (see railReveal.ts).
 * Fetchers throw on failure so the engine retries them in the background. Module-level fetchers + registry pattern
 * (functions outside hook body).
 *
 * Orchestration is delegated to the shared useRailEngine hook.
 * This file retains only the fetchers and enrichment wiring.
 *
 * Consumers: MobileHomePage (compact layout), HomeKioskPage (expanded layout).
 */

import { useMemo, useRef } from 'react';
import type { NormalizedMediaItem } from '@/types/media';
import { fetchWatchlistIds, enrichWatchlistByType, type WatchlistIds } from '@/services/watchlistApi';
import { enrichOwnership } from '@/services/ownership';
import { posterUrl } from '@/utils/image';
import { fetchWithTimeout } from '@/utils/fetchWithTimeout';
import { mapWhatYoureMissingMovies } from './wymDismissApi';
import {
  useRailEngine,
  type RailState,
  type RailEngineResult,
} from '../rails/createRailEngine';
import { withDeadline } from '../rails/railReveal';
import {
  type HomeRailId,
  type HomeRailDefinition,
  HOME_RAILS,
} from './homeRails';

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type HomeRailState = RailState<NormalizedMediaItem, HomeRailId>;

// ---------------------------------------------------------------------------
// Per-rail fetchers
// ---------------------------------------------------------------------------

async function fetchContinueWatching(signal?: AbortSignal): Promise<NormalizedMediaItem[]> {
  const resp = await fetchWithTimeout('/api/plex/continue_watching/cached?limit=25', undefined, signal);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  return ((data.items ?? []) as Record<string, unknown>[]).map((item) => {
    const metadataType = item.metadata_type as number;
    const isEpisode = metadataType === 4;
    const showTitle = item.show_title as string | null;
    const episodeTitle = item.episode_title as string | null;
    const title = isEpisode && showTitle ? showTitle : (item.title as string) ?? '';
    const subtitle = isEpisode && episodeTitle
      ? `S${item.season_number}E${item.episode_number} \u00b7 ${episodeTitle}`
      : item.year ? `(${item.year})` : undefined;

    return {
      id: `cw-${item.rating_key}`,
      title,
      subtitle,
      year: (item.year as number) ?? null,
      posterUrl: (item.poster_url as string) ?? null,
      mediaType: isEpisode ? 'episode' as const : 'movie' as const,
      tmdbId: (item.tmdb_id as number) ?? null,
      ratingKey: (item.rating_key as number) ?? null,
      showRatingKey: (item.show_rating_key as number) ?? null,
      owned: true,
      inWatchlist: false,
      progressPercent: (item.progress_percent as number) ?? undefined,
      seasonNumber: isEpisode ? ((item.season_number as number) ?? null) : null,
      episodeNumber: isEpisode ? ((item.episode_number as number) ?? null) : null,
      episodeRatingKey: isEpisode ? ((item.episode_rating_key as number) ?? null) : null,
    };
  });
}

async function fetchWhatYoureMissing(signal?: AbortSignal): Promise<NormalizedMediaItem[]> {
  const resp = await fetchWithTimeout('/api/rails/what-youre-missing?limit=20', undefined, signal);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  if (data.empty) return [];
  return mapWhatYoureMissingMovies((data.movies ?? []) as Record<string, unknown>[]);
}

function formatUpcomingAirDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  // Sonarr airDate is YYYY-MM-DD (no time). Parse as local calendar date so
  // UTC midnight parsing does not shift the day backward in US timezones.
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  let dt: Date;
  if (dateOnly) {
    dt = new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]));
  } else {
    dt = new Date(iso);
  }
  if (isNaN(dt.getTime())) return null;
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

async function fetchUpcomingTv(signal?: AbortSignal): Promise<NormalizedMediaItem[]> {
  // 14-day Sonarr calendar window; dedupe to one tile per show (next air date).
  // Pull a wider episode window so binge-drop days do not starve later shows.
  const resp = await fetchWithTimeout('/api/sonarr/calendar?days=14&limit=120', undefined, signal);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  if (!data.enabled) return [];
  const seen = new Set<string>();
  const out: Array<NormalizedMediaItem & { _airSort: string }> = [];
  for (const ep of ((data.episodes ?? []) as Record<string, unknown>[])) {
    const tmdbId = (ep.tmdb_id as number) ?? null;
    const tvdbId = (ep.tvdb_id as number) ?? null;
    const key = tmdbId != null ? `tmdb-${tmdbId}` : tvdbId != null ? `tvdb-${tvdbId}` : `title-${ep.series_title}`;
    if (seen.has(key)) continue;
    seen.add(key);

    const season = ep.season as number | null | undefined;
    const episode = ep.episode as number | null | undefined;
    const airUtc = (ep.air_date_utc as string) || '';
    const airLocal = (ep.air_date_local as string) || '';
    // Prefer local calendar date for the subtitle label; fall back to UTC ISO.
    const airLabel = formatUpcomingAirDate(airLocal || airUtc || null);
    // Subtitle under the title: air date first (soonest-airing context).
    const epLabel =
      season != null && episode != null
        ? `S${season}E${String(episode).padStart(2, '0')}`
        : null;
    const subtitle = [airLabel, epLabel].filter(Boolean).join(' · ') || undefined;

    // mediaType 'episode' + season/episode → openModalForItem attaches
    // episodeContext (same path as in-modal episode row taps). Ownership
    // enrichment stamps showRatingKey when the show is in Plex.
    out.push({
      id: `upcoming-tv-${key}`,
      title: (ep.series_title as string) ?? '',
      subtitle,
      year: null,
      posterUrl: posterUrl((ep.poster_url as string) || null, 'w342'),
      mediaType: 'episode' as const,
      tmdbId,
      tvdbId,
      ratingKey: null,
      showRatingKey: null,
      owned: false,
      inWatchlist: false,
      seasonNumber: season ?? null,
      episodeNumber: episode ?? null,
      // Sort key: soonest airing first (UTC when present, else local date).
      _airSort: airUtc || airLocal || '9999',
    });
  }
  // Explicit soonest-airing ascending (do not rely solely on API order).
  out.sort((a, b) => a._airSort.localeCompare(b._airSort));
  return out.map(({ _airSort: _drop, ...item }) => item);
}

async function fetchWatchlistRail(signal?: AbortSignal): Promise<NormalizedMediaItem[]> {
  // Same /api/watchlist as WatchlistPage/Sheet. API sorts soonest US
  // digital first (missing dates last). Preserve order; pass badge fields.
  const resp = await fetchWithTimeout('/api/watchlist', undefined, signal);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  const raw = (Array.isArray(data) ? data : []) as Record<string, unknown>[];
  return raw.slice(0, 25).map((m) => {
    const tmdbId = (m.tmdb_id as number) ?? null;
    const pp = (m.poster_path as string) || null;
    return {
      id: `home-wl-${tmdbId ?? m.title}`,
      title: (m.title as string) ?? '',
      year: (m.year as number) ?? null,
      posterUrl: posterUrl(pp, 'w342'),
      mediaType: 'movie' as const,
      tmdbId,
      ratingKey: null,
      owned: false,
      inWatchlist: true,
      digitalReleaseDate: (m.digital_release_date as string) || null,
      digitalReleaseLabel: (m.digital_release_label as string) || null,
    };
  });
}

async function fetchRecentMovies(signal?: AbortSignal): Promise<NormalizedMediaItem[]> {
  const resp = await fetchWithTimeout('/api/library/recent/cached?type=movie&limit=25', undefined, signal);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  const items = (data.items ?? data.movies ?? []) as Record<string, unknown>[];
  return items.map((m) => ({
    id: `recent-movie-${m.rating_key}`,
    title: (m.title as string) ?? '',
    year: (m.year as number) ?? null,
    posterUrl: (m.poster_url as string) ?? null,
    mediaType: 'movie' as const,
    tmdbId: (m.tmdb_id as number) ?? null,
    ratingKey: (m.rating_key as number) ?? null,
    owned: true,
    inWatchlist: false,
  }));
}

async function fetchRecentTv(signal?: AbortSignal): Promise<NormalizedMediaItem[]> {
  const resp = await fetchWithTimeout('/api/library/episodes/cached?limit=20', undefined, signal);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  // The endpoint returns individual episodes (newest first), so several
  // episodes of the same show collapse to one tile — dedupe by show so we
  // don't render duplicate tiles with colliding React keys.
  const seen = new Set<number>();
  const out: NormalizedMediaItem[] = [];
  for (const ep of ((data.episodes ?? []) as Record<string, unknown>[])) {
    const rk = ep.show_rating_key as number | undefined;
    if (rk != null && seen.has(rk)) continue;
    if (rk != null) seen.add(rk);
    out.push({
      id: `recent-tv-${ep.show_rating_key}`,
      title: (ep.show_title as string) ?? '',
      year: (ep.show_year as number) ?? null,
      posterUrl: (ep.poster_url as string) ?? null,
      mediaType: 'tv' as const,
      tmdbId: (ep.tmdb_id as number) ?? null,
      ratingKey: (ep.show_rating_key as number) ?? null,
      showRatingKey: (ep.show_rating_key as number) ?? null,
      owned: true,
      inWatchlist: false,
    });
  }
  return out;
}

async function fetchRecentlyWatched(signal?: AbortSignal): Promise<NormalizedMediaItem[]> {
  const resp = await fetchWithTimeout('/api/plex/recently_watched?limit=25', undefined, signal);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  return ((data.items ?? []) as Record<string, unknown>[]).map((item) => {
    const isEpisode = (item.metadata_type as number) === 4;
    const showRatingKey = (item.show_rating_key as number) ?? null;
    return {
      // Keyed on the tile's identity (show for episodes) — the backend already
      // collapsed a binge to one row, so this can't collide.
      id: `watched-${isEpisode ? `show-${showRatingKey}` : `movie-${item.rating_key}`}`,
      title: (item.title as string) ?? '',
      subtitle: (item.subtitle as string) ?? undefined,
      year: (item.year as number) ?? null,
      posterUrl: (item.poster_url as string) ?? null,
      // 'tv' rather than 'episode' on purpose: you already finished the episode
      // in the subtitle, so the show modal (pick what's next) is the useful
      // destination. Continue Watching is the rail that resumes an episode.
      mediaType: isEpisode ? ('tv' as const) : ('movie' as const),
      tmdbId: null,
      ratingKey: isEpisode ? showRatingKey : ((item.rating_key as number) ?? null),
      showRatingKey,
      owned: true,
      inWatchlist: false,
    };
  });
}

// ---------------------------------------------------------------------------
// Fetcher registry
// ---------------------------------------------------------------------------

const HOME_RAIL_FETCHERS: Record<HomeRailId, (signal?: AbortSignal) => Promise<NormalizedMediaItem[]>> = {
  continue_watching: fetchContinueWatching,
  what_youre_missing: fetchWhatYoureMissing,
  upcoming_tv: fetchUpcomingTv,
  watchlist: fetchWatchlistRail,
  recent_movies: fetchRecentMovies,
  recent_tv: fetchRecentTv,
  recently_watched: fetchRecentlyWatched,
};

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/** Snapshot lifetime: coming back to Home within this window is instant. */
const HOME_SNAPSHOT_TTL_MS = 10 * 60 * 1000;
const HOME_STORAGE_KEY = 'luma.home.rails.';
const ENRICH_DEADLINE_MS = 3000;

export function useHomeRails(): RailEngineResult<NormalizedMediaItem, HomeRailId> {
  // Start watchlist fetch eagerly (runs in parallel with rail fetches)
  const wlRef = useRef<Promise<WatchlistIds> | null>(null);
  if (!wlRef.current) wlRef.current = fetchWatchlistIds();

  // Enrichment (ownership + watchlist) is part of each rail's fetch, so the
  // badges are already there at the reveal instead of popping in later.
  const fetchers = useMemo(() => {
    const out = {} as Record<HomeRailId, (signal: AbortSignal) => Promise<NormalizedMediaItem[]>>;
    for (const def of HOME_RAILS) {
      const base = HOME_RAIL_FETCHERS[def.id];
      out[def.id] = !def.enrich ? base : async (signal) => {
        const items = await base(signal);
        if (items.length === 0) return items;
        const enrich = Promise.all([wlRef.current!, enrichOwnership(items)])
          .then(([wlIds, owned]) => enrichWatchlistByType(owned, wlIds).map((it) => ({ ...it, ownershipKnown: true })));
        return withDeadline(enrich, ENRICH_DEADLINE_MS, items);
      };
    }
    return out;
  }, []);

  return useRailEngine<NormalizedMediaItem, HomeRailId, HomeRailDefinition>({
    definitions: HOME_RAILS,
    fetchers,
    storageKey: HOME_STORAGE_KEY,
    snapshotTtlMs: HOME_SNAPSHOT_TTL_MS,
  });
}
