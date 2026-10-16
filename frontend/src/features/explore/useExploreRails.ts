/**
 * Shared data hook for Explore rails.
 *
 * Fetches, caches, and enriches data for all configured Explore rails.
 * Both ExploreKioskPage (expanded) and MobileExplorePage (compact) consume
 * this hook so data-fetching logic is defined in one place.
 *
 * Orchestration is delegated to the shared useRailEngine hook.
 * This file retains only the fetchers, parsers, and enrichment wiring.
 */

import { useMemo, useRef } from 'react';
import type { NormalizedMediaItem } from '@/types/media';
import { enrichOwnership } from '@/services/ownership';
import { fetchWatchlistIds, enrichWatchlistByType, type WatchlistIds } from '@/services/watchlistApi';
import { posterUrl, backdropUrl } from '@/utils/image';
import { fetchWithTimeout } from '@/utils/fetchWithTimeout';
import {
  useRailEngine,
  type RailState,
} from '../rails/createRailEngine';
import {
  type RailId,
  type RailDefinition,
  EXPLORE_RAILS,
  RAIL_CACHE_TTL_MS,
  RAIL_CACHE_PREFIX,
} from './exploreRails';

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type ExploreRailState = RailState<NormalizedMediaItem, RailId>;

interface UseExploreRailsOptions {
  /** Which rails to include. Default: all defined rails. */
  railIds?: RailId[];
  /** Override default params for specific rails (e.g., { now_available: { limit: 1000 } }). */
  paramOverrides?: Partial<Record<RailId, Record<string, string | number>>>;
  /** Run batch ownership enrichment after all fetches. Default: true. */
  enableOwnership?: boolean;
}

// ---------------------------------------------------------------------------
// Parsers
// ---------------------------------------------------------------------------

function parseTmdbItem(item: Record<string, unknown>, mediaType: 'movie' | 'tv' = 'movie'): NormalizedMediaItem {
  const title = (item.title as string) ?? (item.name as string) ?? '';
  const dateStr = (item.release_date as string) ?? (item.first_air_date as string) ?? '';
  const year = dateStr ? parseInt(dateStr.substring(0, 4), 10) || null : null;
  const posterPath = item.poster_path as string | null;
  const backdropPath = item.backdrop_path as string | null;
  const tmdbId = (item.id as number) ?? (item.tmdb_id as number) ?? null;

  return {
    id: `tmdb-${tmdbId}`,
    title,
    year,
    posterUrl: posterUrl(posterPath, 'w342'),
    backdropUrl: backdropUrl(backdropPath),
    mediaType: (item.media_type as 'movie' | 'tv') ?? mediaType,
    tmdbId,
    ratingKey: null,
    owned: false,
    inWatchlist: false,
    overview: (item.overview as string) ?? null,
    voteAverage: (item.imdb_rating as number) ?? (item.rating as number) ?? null,
  };
}

// ---------------------------------------------------------------------------
// Per-rail fetchers
// ---------------------------------------------------------------------------

type FetchResult = NormalizedMediaItem[] | null; // null = conditional rail disabled

async function fetchTraktMovies(params: Record<string, string | number>): Promise<FetchResult> {
  const limit = params.limit ?? 20;
  const resp = await fetchWithTimeout(`/api/trakt/movies/trending?limit=${limit}`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  if (!data.enabled) return null;
  return ((data.movies ?? []) as Record<string, unknown>[]).map((m) => {
    const tmdbId = (m.tmdb_id as number) ?? null;
    const posterPath = m.poster_path as string | null;
    const backdropPath = m.backdrop_path as string | null;
    return {
      id: `trakt-movie-${(m.trakt_id as number) ?? tmdbId ?? m.title}`,
      title: (m.title as string) ?? '',
      year: (m.year as number) ?? null,
      posterUrl: posterUrl(posterPath, 'w342'),
      backdropUrl: backdropUrl(backdropPath),
      mediaType: 'movie' as const,
      tmdbId,
      ratingKey: null,
      owned: false,
      inWatchlist: false,
      overview: (m.overview as string) ?? null,
      voteAverage: (m.imdb_rating as number) ?? (m.rating as number) ?? null,
      traktWatchers: (m.watchers as number) ?? undefined,
    };
  });
}

async function fetchTraktShows(params: Record<string, string | number>): Promise<FetchResult> {
  const limit = params.limit ?? 20;
  const resp = await fetchWithTimeout(`/api/trakt/shows/trending?limit=${limit}`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  if (!data.enabled) return null;
  return ((data.shows ?? []) as Record<string, unknown>[]).map((s) => {
    const tmdbId = (s.tmdb_id as number) ?? null;
    const posterPath = s.poster_path as string | null;
    const backdropPath = s.backdrop_path as string | null;
    return {
      id: `trakt-${(s.trakt_id as number) ?? tmdbId ?? s.title}`,
      title: (s.title as string) ?? '',
      year: (s.year as number) ?? null,
      posterUrl: posterUrl(posterPath, 'w342'),
      backdropUrl: backdropUrl(backdropPath),
      mediaType: 'tv' as const,
      tmdbId,
      tvdbId: (s.tvdb_id as number) ?? null,
      imdbId: (s.imdb_id as string) ?? null,
      ratingKey: null,
      owned: false,
      inWatchlist: false,
      overview: (s.overview as string) ?? null,
      voteAverage: (s.imdb_rating as number) ?? (s.rating as number) ?? null,
      traktWatchers: (s.watchers as number) ?? undefined,
    };
  });
}

async function fetchTraktAnticipatedMovies(params: Record<string, string | number>): Promise<FetchResult> {
  const limit = params.limit ?? 20;
  const resp = await fetchWithTimeout(`/api/trakt/movies/anticipated?limit=${limit}`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  if (!data.enabled) return null;
  return ((data.movies ?? []) as Record<string, unknown>[]).map((m) => {
    const tmdbId = (m.tmdb_id as number) ?? null;
    const posterPath = m.poster_path as string | null;
    const backdropPath = m.backdrop_path as string | null;
    return {
      id: `trakt-antic-movie-${(m.trakt_id as number) ?? tmdbId ?? m.title}`,
      title: (m.title as string) ?? '',
      year: (m.year as number) ?? null,
      posterUrl: posterUrl(posterPath, 'w342'),
      backdropUrl: backdropUrl(backdropPath),
      mediaType: 'movie' as const,
      tmdbId,
      ratingKey: null,
      owned: false,
      inWatchlist: false,
      overview: (m.overview as string) ?? null,
      voteAverage: (m.imdb_rating as number) ?? (m.rating as number) ?? null,
    };
  });
}

async function fetchTraktAnticipatedShows(params: Record<string, string | number>): Promise<FetchResult> {
  const limit = params.limit ?? 20;
  const resp = await fetchWithTimeout(`/api/trakt/shows/anticipated?limit=${limit}`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  if (!data.enabled) return null;
  return ((data.shows ?? []) as Record<string, unknown>[]).map((s) => {
    const tmdbId = (s.tmdb_id as number) ?? null;
    const posterPath = s.poster_path as string | null;
    const backdropPath = s.backdrop_path as string | null;
    return {
      id: `trakt-antic-show-${(s.trakt_id as number) ?? tmdbId ?? s.title}`,
      title: (s.title as string) ?? '',
      year: (s.year as number) ?? null,
      posterUrl: posterUrl(posterPath, 'w342'),
      backdropUrl: backdropUrl(backdropPath),
      mediaType: 'tv' as const,
      tmdbId,
      tvdbId: (s.tvdb_id as number) ?? null,
      imdbId: (s.imdb_id as string) ?? null,
      ratingKey: null,
      owned: false,
      inWatchlist: false,
      overview: (s.overview as string) ?? null,
      voteAverage: (s.imdb_rating as number) ?? (s.rating as number) ?? null,
    };
  });
}

async function fetchInTheaters(params: Record<string, string | number>): Promise<FetchResult> {
  const limit = params.limit ?? 20;
  const resp = await fetchWithTimeout('/api/in-theaters');
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  return ((data.movies ?? []) as Record<string, unknown>[]).slice(0, limit as number).map((m) => parseTmdbItem(m));
}

async function fetchTrackerMovies(params: Record<string, string | number>): Promise<FetchResult> {
  const limit = params.limit ?? 40;
  const resp = await fetchWithTimeout(`/api/explore/cached?limit=${limit}`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  const raw = ((data.movies ?? []) as Record<string, unknown>[]).map((m) => {
    const tmdbId = (m.tmdb_id as number) ?? (typeof m.tmdb_id === 'string' ? parseInt(m.tmdb_id as string, 10) || null : null);
    const rawPosterUrl = (m.poster_url as string) || (m.poster as string) || null;
    const posterPath = (m.poster_path as string) ?? null;
    const backdropPath = (m.backdrop_path as string) ?? null;
    return {
      id: `tracker-${tmdbId ?? m.title}`,
      title: (m.title as string) ?? '',
      year: (m.year as number) ?? (typeof m.year === 'string' ? parseInt(m.year as string, 10) || null : null),
      posterUrl: rawPosterUrl || posterUrl(posterPath, 'w342'),
      backdropUrl: backdropUrl(backdropPath),
      mediaType: 'movie' as const,
      tmdbId,
      ratingKey: null,
      owned: false,
      inWatchlist: false,
      overview: (m.overview as string) ?? null,
      voteAverage: null,
      // Tracker-specific fields
      resolution: (m.resolution as string) || null,
      source: (m.source as string) || null,
      tracker: (m.tracker as string) || null,
      seeders: (m.seeders as number) ?? 0,
      sizeGb: (m.size_gb as number) ?? null,
      pubDate: (m.pub_date as string) || (m.publish_date as string) || null,
      dateAdded: (m.date_added as string) || null,
      createdAt: (m.created_at as string) || null,
      torrentName: (m.torrent_name as string) || (m.name as string) || null,
    };
  });

  // Deduplicate by tmdb_id or title, keeping the entry with most seeders
  const deduped = new Map<string, NormalizedMediaItem>();
  for (const movie of raw) {
    const key = movie.tmdbId
      ? `tmdb_${movie.tmdbId}`
      : movie.title.toLowerCase().replace(/[^\w\s]/g, '').trim();
    const existing = deduped.get(key);
    if (!existing || (movie.seeders ?? 0) > (existing.seeders ?? 0)) {
      deduped.set(key, movie);
    }
  }
  return Array.from(deduped.values());
}

const RAIL_FETCHERS: Record<RailId, (params: Record<string, string | number>) => Promise<FetchResult>> = {
  trakt_movies: fetchTraktMovies,
  trakt_shows: fetchTraktShows,
  trakt_anticipated_movies: fetchTraktAnticipatedMovies,
  trakt_anticipated_shows: fetchTraktAnticipatedShows,
  in_theaters: fetchInTheaters,
  now_available: fetchTrackerMovies,
};

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useExploreRails(options?: UseExploreRailsOptions): ExploreRailState[] {
  const optsRef = useRef(options);

  // Resolve options once at mount time
  const { activeDefs, fetchers, enableOwnership } = useMemo(() => {
    const opts = optsRef.current;
    const ids = opts?.railIds;
    const defs: RailDefinition[] = ids
      ? (EXPLORE_RAILS as unknown as RailDefinition[]).filter((d) => ids.includes(d.id))
      : (EXPLORE_RAILS as unknown as RailDefinition[]);

    const eo = opts?.enableOwnership ?? true;
    const paramOverrides = opts?.paramOverrides;

    // Build fetcher map with params baked in
    const f = {} as Record<RailId, () => Promise<NormalizedMediaItem[] | null>>;
    for (const def of defs) {
      const params = { ...def.defaultParams, ...paramOverrides?.[def.id] };
      f[def.id] = () => RAIL_FETCHERS[def.id](params);
    }

    return { activeDefs: defs, fetchers: f, enableOwnership: eo };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Start watchlist fetch eagerly (runs in parallel with rail fetches)
  const wlRef = useRef<Promise<WatchlistIds> | null>(null);
  if (!wlRef.current) wlRef.current = fetchWatchlistIds();

  return useRailEngine<NormalizedMediaItem, RailId, RailDefinition>({
    definitions: activeDefs,
    fetchers,
    storageKey: RAIL_CACHE_PREFIX,
    snapshotTtlMs: RAIL_CACHE_TTL_MS,
    enrichAll: async (resolved) => {
      const wlIds = await wlRef.current!;
      const allItems = [...resolved.values()].flat();
      const enriched = enableOwnership ? await enrichOwnership(allItems) : allItems;
      const withWatchlist = enrichWatchlistByType(enriched, wlIds);
      const enrichedMap = new Map<string, NormalizedMediaItem>();
      for (const item of withWatchlist) {
        // Mark ownership resolved so tiles render the status orb.
        enrichedMap.set(item.id, { ...item, ownershipKnown: true });
      }
      const out = new Map<RailId, NormalizedMediaItem[]>();
      for (const [railId, items] of resolved) {
        out.set(railId, items.map((item) => enrichedMap.get(item.id) ?? item));
      }
      return out;
    },
  }).rails;
}
