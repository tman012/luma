/** Vibe Engine types */

/** A single library item from /api/library/vibe-engine */
export interface VibeItem {
  ratingKey: number;
  title: string;
  year: number | null;
  type: 'movie' | 'tv';
  runtimeMinutes: number;
  genres: string[];
  summary: string;
  contentRating: string;
  rating: number | null;
  audienceRating: number | null;
  posterUrl: string | null;
  backdropUrl: string | null;
  tmdbId: number | null;
  imdbId: string | null;
  viewCount: number;
  lastViewedAt: number;
  addedAt: number;
  libraryName: string;
  /** TMDb vote count — populated for unowned TMDb items, used for premium sorting. */
  voteCount?: number;
  rtCriticScore: number | null;
  rtAudienceScore: number | null;
}

/** Raw snake_case shape from the API */
export interface RawVibeItem {
  rating_key: number;
  title: string;
  year: number | null;
  type: 'movie' | 'tv';
  runtime_minutes: number;
  genres: string[];
  summary: string;
  content_rating: string;
  rating: number | null;
  audience_rating: number | null;
  poster_url: string | null;
  backdrop_url: string | null;
  tmdb_id: number | null;
  imdb_id: string | null;
  view_count: number;
  last_viewed_at: number;
  added_at: number;
  library_name: string;
  rt_critic_score: number | null;
  rt_audience_score: number | null;
}

export interface VibeEngineResponse {
  items: RawVibeItem[];
  total: number;
  cached_at: number;
}

export type VibeId =
  | 'cozy-comfort'
  | 'dark-moody'
  | 'mind-bending'
  | 'big-adventure'
  | 'laugh-out-loud'
  | 'classic-throwback'
  | 'edge-of-seat'
  | 'easy-background';

export interface VibeDefinition {
  id: VibeId;
  name: string;
  emoji: string;
  description: string;
  color: string; // tailwind-compatible gradient stop
}

export interface VibeShelf {
  vibe: VibeDefinition;
  items: VibeItem[];
}

export type VibeMode = 'home' | 'feel' | 'pick' | 'explore' | 'describe';
