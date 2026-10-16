/** Vibe Engine API service */

import type { RawVibeItem, VibeItem, VibeEngineResponse } from '@/types/vibe';
import type { KioskSettings } from '@/services/types';

export interface VibeSettings {
  newerThanYears: number;
  excludedLibraries: string[];
  genres: string[];
  tonightMaxRuntime: number;
  premiumRatingThreshold: number;
  unwatchedBoostWeight: number;
}

function mapVibeItem(raw: RawVibeItem): VibeItem {
  return {
    ratingKey: raw.rating_key,
    title: raw.title,
    year: raw.year,
    type: raw.type,
    runtimeMinutes: raw.runtime_minutes,
    genres: raw.genres ?? [],
    summary: raw.summary ?? '',
    contentRating: raw.content_rating ?? '',
    rating: raw.rating,
    audienceRating: raw.audience_rating,
    posterUrl: raw.poster_url,
    backdropUrl: raw.backdrop_url,
    tmdbId: raw.tmdb_id,
    imdbId: raw.imdb_id,
    viewCount: raw.view_count ?? 0,
    lastViewedAt: raw.last_viewed_at ?? 0,
    addedAt: raw.added_at ?? 0,
    libraryName: raw.library_name ?? '',
    rtCriticScore: raw.rt_critic_score ?? null,
    rtAudienceScore: raw.rt_audience_score ?? null,
  };
}

let _cache: { items: VibeItem[]; ts: number } | null = null;
const CACHE_TTL = 5 * 60 * 1000; // 5 min client-side

const LIBRARY_TIMEOUT_MS = 45_000;
let _inflight: Promise<VibeItem[]> | null = null;

export async function fetchVibeLibrary(): Promise<VibeItem[]> {
  if (_cache && Date.now() - _cache.ts < CACHE_TTL) {
    return _cache.items;
  }
  if (_inflight) return _inflight;

  _inflight = (async () => {
    // A cold server-side index can take several seconds (full Plex walk) — bound it.
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), LIBRARY_TIMEOUT_MS);
    try {
      const resp = await fetch('/api/library/vibe-engine', { signal: ctrl.signal });
      if (!resp.ok) throw new Error(`Vibe API error: ${resp.status}`);
      const data: VibeEngineResponse = await resp.json();
      const items = (data.items ?? []).map(mapVibeItem);
      // Never cache an empty index (Plex hiccup) — the next attempt should refetch.
      if (items.length > 0) _cache = { items, ts: Date.now() };
      return items;
    } finally {
      clearTimeout(timer);
      _inflight = null;
    }
  })();
  return _inflight;
}

export function clearVibeCache(): void {
  _cache = null;
  _settingsCache = null;
}

let _settingsCache: { settings: VibeSettings; ts: number } | null = null;

const DEFAULT_VIBE_SETTINGS: VibeSettings = {
  newerThanYears: 1,
  excludedLibraries: [],
  genres: ['Action', 'Adventure', 'Animation', 'Comedy', 'Crime', 'Documentary', 'Drama', 'Family', 'Fantasy', 'History', 'Horror', 'Music', 'Mystery', 'Romance', 'Sci-Fi', 'Thriller', 'War', 'Western'],
  tonightMaxRuntime: 100,
  premiumRatingThreshold: 7.0,
  unwatchedBoostWeight: 3,
};

export async function fetchVibeSettings(): Promise<VibeSettings> {
  if (_settingsCache && Date.now() - _settingsCache.ts < CACHE_TTL) {
    return _settingsCache.settings;
  }

  try {
    const resp = await fetch('/api/kiosk/settings');
    if (!resp.ok) return DEFAULT_VIBE_SETTINGS;
    const data: KioskSettings = await resp.json();
    const settings: VibeSettings = {
      newerThanYears: data.vibe_newer_than_years ?? 1,
      excludedLibraries: data.vibe_excluded_libraries ?? [],
      genres: data.vibe_genres ?? DEFAULT_VIBE_SETTINGS.genres,
      tonightMaxRuntime: data.vibe_tonight_max_runtime ?? 100,
      premiumRatingThreshold: data.vibe_premium_rating_threshold ?? 7.0,
      unwatchedBoostWeight: data.vibe_unwatched_boost_weight ?? 3,
    };
    _settingsCache = { settings, ts: Date.now() };
    return settings;
  } catch {
    return DEFAULT_VIBE_SETTINGS;
  }
}
