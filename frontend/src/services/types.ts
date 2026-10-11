// Hero movies from /api/hero/content and /api/library/recent
export interface HeroMovie {
  ratingKey: number;
  title: string;
  year: number | null;
  tmdbId: number | null;
  imdbId: string | null;
  summary: string;
  contentRating: string;
  duration: number;
  rating: number | null;
  audienceRating: number | null;
  genres: string[];
  posterUrl: string | null;
  backdropUrl: string | null;
  tmdbBackdropUrl: string | null;
  clearlogoUrl: string | null;
  addedAt: number;
  addedDate: string | null;
  libraryName: string;
}

export interface HeroContentResponse {
  success: boolean;
  movies: RawHeroMovie[];
}

export interface LibraryRecentResponse {
  movies: RawHeroMovie[];
}

// Raw API response (snake_case) — mapped to HeroMovie in hooks
export interface RawHeroMovie {
  rating_key: number;
  title: string;
  year: number | null;
  tmdb_id: number | null;
  imdb_id: string | null;
  summary?: string;
  content_rating?: string;
  duration?: number;
  rating?: number | null;
  audience_rating?: number | null;
  rt_score?: number | null;
  genres?: string[];
  poster_url?: string | null;
  backdrop_url?: string | null;
  tmdb_backdrop_url?: string | null;
  clearlogo_url?: string | null;
  clearart_url?: string | null;
  added_at?: number;
  added_date?: string | null;
  library_name?: string;
  is_owned?: boolean;
}

// Explore movies from /api/explore/cached
export interface ExploreMovie {
  title: string;
  year: number;
  tmdbId: number;
  posterUrl: string | null;
  backdropUrl: string | null;
  seeders: number;
  sizeBytes: number;
  resolution: string | null;
  tracker: string | null;
  publishDate: string | null;
  imdbId: string | null;
  overview: string | null;
}

export interface RawExploreMovie {
  title: string;
  year: number;
  tmdb_id: number;
  poster_path?: string | null;
  backdrop_path?: string | null;
  seeders: number;
  size_bytes: number;
  resolution?: string | null;
  tracker?: string | null;
  publish_date?: string | null;
  imdb_id?: string | null;
  overview?: string | null;
}

export interface ExploreResponse {
  movies: RawExploreMovie[];
  total: number;
  cached: boolean;
  cache_info?: {
    age_minutes: number;
    last_updated: string;
  };
}

// Plex health response
export interface PlexLibrary {
  id: number;
  title: string;
  type: string;
}

export interface PlexHealthResponse {
  success: boolean;
  server_name: string | null;
  libraries: PlexLibrary[];
  default_movie_section_id: number | null;
}

// Kiosk settings
export interface KioskSettings {
  kiosk_mode: 'movie' | 'daily';
  hero_library_section_id: number;
  hero_limit: number;
  backdrop_source: 'plex' | 'tmdb';
  boost_popular_titles: boolean;
  popularity_source: 'imdb' | 'tmdb';
  screensaver_enabled: boolean;
  screensaver_type: 'orb' | 'trivia' | 'kiosk';
  screensaver_idle_timeout_minutes: number;
  screensaver_orb_count: number;
  screensaver_tablet_orb_count: number;
  screensaver_source: 'hero' | 'plex_library';
  vibe_newer_than_years: number;
  vibe_excluded_libraries: string[];
  vibe_genres: string[];
  vibe_tonight_max_runtime: number;
  vibe_premium_rating_threshold: number;
  vibe_unwatched_boost_weight: number;
}
