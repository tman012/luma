import { apiFetch } from '../apiClient';
/**
 * Sonarr TV API — shared service for kiosk TV modal.
 *
 * Endpoints:
 *   GET  /api/sonarr/status/<tvdb_id>       → check if show exists in Sonarr
 *   POST /api/sonarr/add                     → add show to Sonarr
 *   GET  /api/sonarr/quality-profiles         → quality profiles
 *   GET  /api/sonarr/season/search            → season pack releases
 *   GET  /api/sonarr/episode/search           → episode releases
 *   POST /api/sonarr/episode/download         → grab a release
 *   GET  /api/tv/seasons/<tmdb_id>            → TMDb seasons & episodes
 *   GET  /api/tv/ownership/<tvdb_id>/cached   → Plex episode ownership
 *   GET  /api/tv/<tmdb_id>                    → TMDb show details + credits
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SonarrStatus {
  exists: boolean;
  enabled: boolean;
  series?: {
    id: number;
    title: string;
    tvdbId: number;
    tmdbId?: number;
    qualityProfileId?: number;
    monitored?: boolean;
    seasons?: { seasonNumber: number; monitored: boolean }[];
  };
}

export interface QualityProfile {
  id: number;
  name: string;
}

export interface TvSeason {
  season_number: number;
  name: string;
  overview: string;
  air_date: string | null;
  poster_path: string | null;
  episode_count: number;
  episodes: TvEpisode[];
}

export interface TvEpisode {
  episode_number: number;
  name: string;
  overview: string;
  air_date: string | null;
  still_path: string | null;
  runtime: number | null;
}

interface OwnershipEntry {
  owned: boolean;
  rating_key?: number;
  runtime?: number;
}

export interface OwnershipMap {
  [key: string]: OwnershipEntry;
}

interface TvOwnershipResult {
  success: boolean;
  tvdb_id: number;
  episodes: OwnershipMap;
  total_owned: number;
  is_owned: boolean;
}

export interface SonarrRelease {
  guid: string;
  indexer_id: number;
  indexer: string;
  title: string;
  size: number;
  quality: string;
  seeders: number;
  leechers?: number;
  proper?: boolean;
  age: number | null;
  publishDate: string | null;
  ageMinutes?: number;
  fullSeason?: boolean;
}

// ---------------------------------------------------------------------------
// Sonarr status & management
// ---------------------------------------------------------------------------

// Short-TTL caches + in-flight dedup so reopening a TV show doesn't re-fire the
// full modal request set. 60s respects the ownership-accuracy guardrail; only
// successful responses are cached (errors fall through to a fresh fetch).
type SeasonsResult = { success: boolean; tmdb_id: number; tvdb_id: number | null; seasons: TvSeason[] };
const TV_DATA_TTL = 60 * 1000;
const TV_DATA_MAX = 60;
const _statusCache = new Map<string, CacheEntry<SonarrStatus>>();
const _statusInflight = new Map<string, Promise<SonarrStatus>>();
const _seasonsCache = new Map<string, CacheEntry<SeasonsResult>>();
const _seasonsInflight = new Map<string, Promise<SeasonsResult>>();
const _tvOwnCache = new Map<string, CacheEntry<TvOwnershipResult>>();
const _tvOwnInflight = new Map<string, Promise<TvOwnershipResult>>();

/** Clear Sonarr-status + ownership caches after a mutation so the UI's refresh
 *  reflects the new state immediately (seasons are static, left intact). */
function invalidateTvCaches(): void {
  _statusCache.clear();
  _tvOwnCache.clear();
}

export async function checkSonarrStatus(
  tvdbId: number,
  tmdbId?: number,
): Promise<SonarrStatus> {
  const key = `${tvdbId}:${tmdbId ?? ''}`;
  const cached = _statusCache.get(key);
  if (cached && Date.now() - cached.ts < TV_DATA_TTL) return cached.data;
  const inflight = _statusInflight.get(key);
  if (inflight) return inflight;

  const params = tmdbId ? `?tmdb_id=${tmdbId}` : '';
  const promise = (async (): Promise<SonarrStatus> => {
    try {
      const resp = await fetch(`/api/sonarr/status/${tvdbId}${params}`);
      if (!resp.ok) return { exists: false, enabled: false };
      const data: SonarrStatus = await resp.json();
      _statusCache.set(key, { data, ts: Date.now() });
      evictOldest(_statusCache, TV_DATA_MAX);
      return data;
    } catch {
      return { exists: false, enabled: false };
    } finally {
      _statusInflight.delete(key);
    }
  })();
  _statusInflight.set(key, promise);
  return promise;
}

export async function addToSonarr(
  tvdbId: number,
  title: string,
  monitor: string = 'all',
  searchMissing: boolean = true,
  seasons?: number[],
  qualityProfileId?: number,
): Promise<{ success: boolean; message: string; series?: Record<string, unknown> }> {
  const resp = await apiFetch('/api/sonarr/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      tvdb_id: tvdbId,
      title,
      monitor,
      search_missing: searchMissing,
      ...(seasons && seasons.length ? { seasons } : {}),
      ...(qualityProfileId ? { quality_profile_id: qualityProfileId } : {}),
    }),
  });
  invalidateTvCaches();
  return resp.json();
}

export async function fetchQualityProfiles(): Promise<QualityProfile[]> {
  try {
    const resp = await apiFetch('/api/sonarr/quality-profiles');
    if (!resp.ok) return [];
    return resp.json();
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Auto search (Sonarr picks best release by quality profile)
// ---------------------------------------------------------------------------

export async function autoSearchSeason(
  tvdbId: number,
  season: number,
  tmdbId?: number,
): Promise<{ success: boolean; message: string }> {
  try {
    const resp = await apiFetch('/api/sonarr/search/season', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tvdb_id: tvdbId,
        season,
        tmdb_id: tmdbId,
      }),
    });
    invalidateTvCaches();
    return resp.json();
  } catch {
    return { success: false, message: 'Network error' };
  }
}

export async function autoSearchEpisode(
  tvdbId: number,
  season: number,
  episode: number,
  tmdbId?: number,
  title?: string,
): Promise<{ success: boolean; message: string }> {
  try {
    const resp = await apiFetch('/api/sonarr/search/episode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tvdb_id: tvdbId,
        tmdb_id: tmdbId,
        season,
        episode,
        title,
      }),
    });
    invalidateTvCaches();
    return resp.json();
  } catch {
    return { success: false, message: 'Network error' };
  }
}

// ---------------------------------------------------------------------------
// Release search (interactive — returns list for user selection)
// ---------------------------------------------------------------------------

export async function searchSeasonReleases(
  tvdbId: number,
  season: number,
  tmdbId?: number,
): Promise<{ success: boolean; releases: SonarrRelease[]; series_id?: number; season?: number }> {
  const params = new URLSearchParams({
    tvdb_id: String(tvdbId),
    season: String(season),
  });
  if (tmdbId) params.set('tmdb_id', String(tmdbId));
  try {
    const resp = await fetch(`/api/sonarr/season/search?${params}`);
    return resp.json();
  } catch {
    return { success: false, releases: [] };
  }
}

export async function searchEpisodeReleases(
  tvdbId: number,
  season: number,
  episode: number,
  tmdbId?: number,
): Promise<{ success: boolean; releases: SonarrRelease[]; episode_id?: number; series_id?: number }> {
  const params = new URLSearchParams({
    tvdb_id: String(tvdbId),
    season: String(season),
    episode: String(episode),
  });
  if (tmdbId) params.set('tmdb_id', String(tmdbId));
  try {
    const resp = await fetch(`/api/sonarr/episode/search?${params}`);
    return resp.json();
  } catch {
    return { success: false, releases: [] };
  }
}

// ---------------------------------------------------------------------------
// Download
// ---------------------------------------------------------------------------

export async function downloadRelease(
  guid: string,
  indexerId: number,
  opts?: { seriesId?: number; season?: number; episodeId?: number; tmdbId?: number; title?: string },
): Promise<{ success: boolean; message: string }> {
  try {
    const resp = await apiFetch('/api/sonarr/episode/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        guid,
        indexer_id: indexerId,
        series_id: opts?.seriesId,
        season: opts?.season,
        episode_id: opts?.episodeId,
        tmdb_id: opts?.tmdbId,
        title: opts?.title,
      }),
    });
    invalidateTvCaches();
    return resp.json();
  } catch {
    return { success: false, message: 'Network error' };
  }
}

// ---------------------------------------------------------------------------
// TV show data (TMDb + Plex ownership)
// ---------------------------------------------------------------------------

export async function fetchTvSeasons(
  tmdbId: number,
  opts?: { light?: boolean },
): Promise<SeasonsResult> {
  const light = opts?.light === true;
  const key = light ? `light:${tmdbId}` : String(tmdbId);
  // Full result satisfies light consumers; prefer it if already cached.
  if (light) {
    const full = _seasonsCache.get(String(tmdbId));
    if (full && Date.now() - full.ts < TV_DATA_TTL) return full.data;
  }
  const cached = _seasonsCache.get(key);
  if (cached && Date.now() - cached.ts < TV_DATA_TTL) return cached.data;
  const inflight = _seasonsInflight.get(key);
  if (inflight) return inflight;

  const promise = (async (): Promise<SeasonsResult> => {
    try {
      const qs = light ? '?light=1' : '';
      const resp = await fetch(`/api/tv/seasons/${tmdbId}${qs}`);
      if (!resp.ok) return { success: false, tmdb_id: tmdbId, tvdb_id: null, seasons: [] };
      const data: SeasonsResult = await resp.json();
      _seasonsCache.set(key, { data, ts: Date.now() });
      evictOldest(_seasonsCache, TV_DATA_MAX);
      return data;
    } catch {
      return { success: false, tmdb_id: tmdbId, tvdb_id: null, seasons: [] };
    } finally {
      _seasonsInflight.delete(key);
    }
  })();
  _seasonsInflight.set(key, promise);
  return promise;
}

export async function fetchTvOwnership(
  tvdbId: number,
  title?: string,
  year?: number,
  showRatingKey?: number,
): Promise<TvOwnershipResult> {
  const params = new URLSearchParams();
  if (title) params.set('title', title);
  if (year) params.set('year', String(year));
  if (showRatingKey) params.set('show_rating_key', String(showRatingKey));
  const qs = params.toString() ? `?${params}` : '';
  const key = `${tvdbId}${qs}`;
  const cached = _tvOwnCache.get(key);
  if (cached && Date.now() - cached.ts < TV_DATA_TTL) return cached.data;
  const inflight = _tvOwnInflight.get(key);
  if (inflight) return inflight;

  const promise = (async (): Promise<TvOwnershipResult> => {
    try {
      const resp = await fetch(`/api/tv/ownership/${tvdbId}/cached${qs}`);
      if (!resp.ok) return { success: false, tvdb_id: tvdbId, episodes: {}, total_owned: 0, is_owned: false };
      const data: TvOwnershipResult = await resp.json();
      _tvOwnCache.set(key, { data, ts: Date.now() });
      evictOldest(_tvOwnCache, TV_DATA_MAX);
      return data;
    } catch {
      return { success: false, tvdb_id: tvdbId, episodes: {}, total_owned: 0, is_owned: false };
    } finally {
      _tvOwnInflight.delete(key);
    }
  })();
  _tvOwnInflight.set(key, promise);
  return promise;
}

// ---------------------------------------------------------------------------
// Episode metadata — with in-flight dedup + TTL cache
// ---------------------------------------------------------------------------

/** Raw Plex episode response from GET /api/plex/episode/<rk> */
interface PlexEpisodeResponse {
  title: string;
  summary: string;
  duration: number; // ms
  rating: number | null;
  audience_rating: number | null;
  content_rating: string;
  originally_available_at: string | null;
  view_count: number;
  view_offset: number;
  thumb_path: string | null;
  art_path: string | null;
  show_thumb_path: string | null;
  season_number: number;
  episode_number: number;
  show_title: string;
  show_rating_key: number | null;
  rating_key: number;
}

/** Raw TMDb episode response from GET /api/tv/<id>/season/<s>/episode/<e> */
interface TmdbEpisodeResponse {
  name: string;
  overview: string;
  air_date: string | null;
  runtime: number | null; // minutes
  vote_average: number | null;
  vote_count: number | null;
  still_path: string | null;
  season_number: number;
  episode_number: number;
  crew: { id: number; name: string; job: string; department: string; profile_path: string | null }[];
  cast?: { id: number; name: string; character: string; profile_path: string | null }[];
  guest_stars: { id: number; name: string; character: string; profile_path: string | null }[];
  imdb_id?: string | null;
  tvdb_id?: number | null;
}

// -- Episode fetch cache (in-flight dedup + TTL) --
// Plex episode data includes watch state, so keep a short TTL (2 min).
// TMDb episode data is static, so use a longer TTL (10 min).
const PLEX_EP_CACHE_TTL = 2 * 60 * 1000;  // 2 minutes
const TMDB_EP_CACHE_TTL = 10 * 60 * 1000; // 10 minutes
const EPISODE_CACHE_MAX = 100;             // max entries per cache

interface CacheEntry<T> {
  data: T;
  ts: number;
}

const _plexEpCache = new Map<string, CacheEntry<PlexEpisodeResponse | null>>();
const _plexEpInflight = new Map<string, Promise<PlexEpisodeResponse | null>>();

const _tmdbEpCache = new Map<string, CacheEntry<TmdbEpisodeResponse | null>>();
const _tmdbEpInflight = new Map<string, Promise<TmdbEpisodeResponse | null>>();

/** Evict oldest entries when cache exceeds max size. */
function evictOldest<T>(cache: Map<string, CacheEntry<T>>, max: number): void {
  if (cache.size <= max) return;
  // Map iterates in insertion order — delete the first (oldest) entries
  const toDelete = cache.size - max;
  let count = 0;
  for (const key of cache.keys()) {
    if (count >= toDelete) break;
    cache.delete(key);
    count++;
  }
}

/**
 * Fetch episode metadata from Plex by episode rating key (owned episodes).
 * Includes in-flight dedup and 2-minute TTL cache.
 */
export async function fetchPlexEpisode(ratingKey: number): Promise<PlexEpisodeResponse | null> {
  const key = `plex:${ratingKey}`;

  // Check TTL cache
  const cached = _plexEpCache.get(key);
  if (cached && Date.now() - cached.ts < PLEX_EP_CACHE_TTL) {
    return cached.data;
  }

  // Check in-flight dedup
  const inflight = _plexEpInflight.get(key);
  if (inflight) return inflight;

  // New request
  const promise = (async (): Promise<PlexEpisodeResponse | null> => {
    try {
      const resp = await fetch(`/api/plex/episode/${ratingKey}`);
      if (!resp.ok) return null;
      const data: PlexEpisodeResponse = await resp.json();
      _plexEpCache.set(key, { data, ts: Date.now() });
      evictOldest(_plexEpCache, EPISODE_CACHE_MAX);
      return data;
    } catch {
      return null;
    } finally {
      _plexEpInflight.delete(key);
    }
  })();

  _plexEpInflight.set(key, promise);
  return promise;
}

/**
 * Fetch episode metadata from TMDb (unowned episodes).
 * Includes in-flight dedup and 10-minute TTL cache.
 */
export async function fetchTmdbEpisode(
  tmdbShowId: number,
  season: number,
  episode: number,
): Promise<TmdbEpisodeResponse | null> {
  const key = `tmdb:${tmdbShowId}:s${season}e${episode}`;

  // Check TTL cache
  const cached = _tmdbEpCache.get(key);
  if (cached && Date.now() - cached.ts < TMDB_EP_CACHE_TTL) {
    return cached.data;
  }

  // Check in-flight dedup
  const inflight = _tmdbEpInflight.get(key);
  if (inflight) return inflight;

  // New request
  const promise = (async (): Promise<TmdbEpisodeResponse | null> => {
    try {
      const resp = await fetch(`/api/tv/${tmdbShowId}/season/${season}/episode/${episode}`);
      if (!resp.ok) return null;
      const data: TmdbEpisodeResponse = await resp.json();
      _tmdbEpCache.set(key, { data, ts: Date.now() });
      evictOldest(_tmdbEpCache, EPISODE_CACHE_MAX);
      return data;
    } catch {
      return null;
    } finally {
      _tmdbEpInflight.delete(key);
    }
  })();

  _tmdbEpInflight.set(key, promise);
  return promise;
}
