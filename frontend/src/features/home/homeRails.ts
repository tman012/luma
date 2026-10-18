/**
 * Shared rail definitions for the Home page.
 *
 * Both DesktopHome (expanded) and MobileHomePage (compact) read from
 * these definitions so every rail has a single source of truth for its id,
 * title, endpoint, and behavioral rules.
 *
 * This file must contain NO React.
 */


// ---------------------------------------------------------------------------
// Rail ID
// ---------------------------------------------------------------------------

export type HomeRailId =
  | 'continue_watching'
  | 'what_youre_missing'
  | 'upcoming_tv'
  | 'watchlist'
  | 'recent_movies'
  | 'recent_tv'
  | 'recently_watched';

// ---------------------------------------------------------------------------
// Rail definition
// ---------------------------------------------------------------------------

export interface HomeRailDefinition {
  id: HomeRailId;
  title: string;
  endpoint: string;
  defaultParams: Record<string, string | number>;
  /** When true the rail is hidden until the fetch returns items (no skeleton). */
  conditional: boolean;
  /** Whether items need watchlist enrichment (external/unowned sources only). */
  enrich: boolean;
}

export const HOME_RAILS: readonly HomeRailDefinition[] = [
  {
    id: 'continue_watching',
    title: 'Continue watching',
    endpoint: '/api/plex/continue_watching/cached',
    defaultParams: { limit: 25 },
    conditional: false,
    enrich: false,
  },
  {
    // Daily popular blind spots — TOP discovery rail.
    id: 'what_youre_missing',
    title: "What you're missing",
    endpoint: '/api/rails/what-youre-missing',
    defaultParams: { limit: 20 },
    conditional: true, // hide entirely when the pool is thin
    enrich: true,
  },
  {
    // Sonarr calendar — monitored episodes airing in the next ~2 weeks.
    id: 'upcoming_tv',
    title: 'Upcoming TV',
    endpoint: '/api/sonarr/calendar',
    defaultParams: { days: 14, limit: 120 },
    conditional: true,
    enrich: true,
  },
  {
    // Radarr-monitored movies waiting to land (existing watchlist API).
    id: 'watchlist',
    title: 'Watchlist',
    endpoint: '/api/watchlist',
    defaultParams: {},
    conditional: true,
    enrich: true,
  },
  {
    id: 'recent_movies',
    title: 'Recently added movies',
    endpoint: '/api/library/recent/cached',
    defaultParams: { type: 'movie', limit: 25 },
    conditional: false,
    enrich: false,
  },
  {
    id: 'recent_tv',
    title: 'Recently added TV',
    endpoint: '/api/library/episodes/cached',
    defaultParams: { limit: 20 },
    conditional: false,
    enrich: false,
  },
  {
    // Conditional: a fresh server with no watch history should show nothing
    // here rather than a skeleton that never fills in.
    id: 'recently_watched',
    title: 'Recently watched',
    endpoint: '/api/plex/recently_watched',
    defaultParams: { limit: 25 },
    conditional: true,
    enrich: false,
  },
];

// ---------------------------------------------------------------------------
// Item type — re-export from shared NormalizedMediaItem
// ---------------------------------------------------------------------------

export type { NormalizedMediaItem } from '@/types/media';

