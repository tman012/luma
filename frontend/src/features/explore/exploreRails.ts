/**
 * Shared rail definitions for the Explore page.
 *
 * Both ExploreKioskPage (expanded) and MobileExplorePage (compact) read from
 * these definitions so every rail has a single source of truth for its id,
 * title, endpoint, and conditional-display rules.
 *
 * This file must contain NO React.
 */


// ---------------------------------------------------------------------------
// Rail ID
// ---------------------------------------------------------------------------

export type RailId =
  | 'trakt_movies'
  | 'trakt_shows'
  | 'trakt_anticipated_movies'
  | 'trakt_anticipated_shows'
  | 'in_theaters'
  | 'now_available';

// ---------------------------------------------------------------------------
// Rail definition
// ---------------------------------------------------------------------------

export interface RailDefinition {
  id: RailId;
  title: string;
  source: 'trakt' | 'tmdb' | 'indexer';
  endpoint: string;
  defaultParams: Record<string, string | number>;
  /** When true the rail is hidden until the API confirms the feature is enabled. */
  conditional: boolean;
  mediaType: 'movie' | 'tv';
}

export const EXPLORE_RAILS: readonly RailDefinition[] = [
  {
    id: 'trakt_movies',
    title: 'Trending movies',
    source: 'trakt',
    endpoint: '/api/trakt/movies/trending',
    defaultParams: { limit: 20 },
    conditional: true,
    mediaType: 'movie',
  },
  {
    id: 'trakt_shows',
    title: 'Trending shows',
    source: 'trakt',
    endpoint: '/api/trakt/shows/trending',
    defaultParams: { limit: 20 },
    conditional: true,
    mediaType: 'tv',
  },
  {
    id: 'trakt_anticipated_movies',
    title: 'Anticipated movies',
    source: 'trakt',
    endpoint: '/api/trakt/movies/anticipated',
    defaultParams: { limit: 20 },
    conditional: true,
    mediaType: 'movie',
  },
  {
    id: 'trakt_anticipated_shows',
    title: 'Anticipated shows',
    source: 'trakt',
    endpoint: '/api/trakt/shows/anticipated',
    defaultParams: { limit: 20 },
    conditional: true,
    mediaType: 'tv',
  },
  {
    id: 'in_theaters',
    title: 'In theaters',
    source: 'tmdb',
    endpoint: '/api/in-theaters',
    defaultParams: {},
    conditional: false,
    mediaType: 'movie',
  },
  {
    id: 'now_available',
    title: 'Now available',
    source: 'indexer',
    endpoint: '/api/explore/cached',
    defaultParams: { limit: 40 },
    conditional: false,
    mediaType: 'movie',
  },
];

// ---------------------------------------------------------------------------
// Cache config
// ---------------------------------------------------------------------------

export const RAIL_CACHE_TTL_MS = 5 * 60 * 1000;
export const RAIL_CACHE_PREFIX = 'explore_rail_';

