/**
 * Search API layer — mirrors /web search behavior exactly.
 *
 * Endpoints used:
 *   GET  /api/library/search?q=...       → owned Plex results (movies + tv_shows + people)
 *   GET  /search?q=...&type=all          → TMDb external results (movies + tv_shows + people)
 *   POST /api/imdb/votes                 → batch IMDb vote counts for sorting
 *   GET  /api/check-ownership            → single ownership check on click
 *   POST /api/check-ownership-batch      → batch ownership for person filmography
 *   GET  /api/tmdb/person_by_id/:id/credits → person filmography
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SearchMovie {
  rating_key?: number;
  title: string;
  year: number | null;
  poster_url?: string | null;
  poster_path?: string | null;
  backdrop_url?: string | null;
  backdrop_path?: string | null;
  clearlogo_url?: string | null;
  library_name?: string;
  libraries?: string[];
  added_date?: string | null;
  tmdb_id?: number;
  id?: number; // TMDb ID
  imdb_id?: string;
  media_type?: 'movie' | 'tv';
  release_date?: string;
  first_air_date?: string;
  overview?: string;
  vote_average?: number;
  vote_count?: number;
  name?: string; // TV show name from TMDb
  popularity?: number;
  /** TMDb cross-type relevance position from /search — lower is more relevant. */
  relevance_rank?: number;
  // enriched fields
  isOwned?: boolean;
}

export interface SearchPerson {
  id?: number; // TMDb person ID
  plex_actor_id?: number;
  name: string;
  role?: string;
  known_for_department?: string;
  popularity?: number;
  profile_url?: string | null;
  profile_path?: string | null;
  thumb?: string | null;
}

export interface PersonCredit {
  tmdb_id: number;
  title: string;
  year: number | null;
  rating: number | null;
  vote_count: number;
  poster_url: string | null;
  media_type: 'movie' | 'tv';
  imdb_id: string;
  imdb_votes?: number;
  isOwned?: boolean;
  rating_key?: number;
}

export interface SearchResults {
  owned: SearchMovie[];
  unowned: SearchMovie[];
  ownedPeople: SearchPerson[];
  unownedPeople: SearchPerson[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

import { posterUrl as tmdbPosterUrl } from '@/utils/image';

function titleKey(title: string, year: number | string | null | undefined): string {
  return `${(title || '').toLowerCase()}_${year || ''}`;
}

function getPosterUrl(item: SearchMovie): string | null {
  if (item.poster_url) return item.poster_url;
  return tmdbPosterUrl(item.poster_path, 'w342');
}

function getTitle(item: SearchMovie): string {
  return item.title || item.name || '';
}

function getYear(item: SearchMovie): number | null {
  if (item.year) return item.year;
  const dateStr = item.release_date || item.first_air_date;
  if (dateStr) {
    const y = parseInt(dateStr.substring(0, 4), 10);
    return isNaN(y) ? null : y;
  }
  return null;
}

// ---------------------------------------------------------------------------
// performSearch — parallel library + TMDb, dedup, TMDb relevance order
//
// Ordering is TMDb's, preserved end to end. This used to re-sort everything by
// IMDb vote count once an /api/imdb/votes round-trip came back, which is a pure
// popularity sort with no relevance term: searching "Superman" ranked "Batman v
// Superman" first (790k votes vs 406k). It also made the list visibly reshuffle
// a beat after rendering. The vote counts were never displayed — they existed
// only to drive that sort — so the whole round-trip is gone.
// ---------------------------------------------------------------------------

export async function performSearch(
  query: string,
  signal?: AbortSignal,
): Promise<SearchResults> {
  // Parallel fetch: library (owned) + TMDb (external)
  const [libraryData, tmdbData] = await Promise.all([
    fetch(`/api/library/search?q=${encodeURIComponent(query)}`, { signal })
      .then((r) => r.json())
      .catch(() => ({ movies: [], tv_shows: [], people: [] })),
    fetch(`/search?q=${encodeURIComponent(query)}&type=all`, { signal })
      .then((r) => r.json())
      .catch(() => ({ movies: [], tv_shows: [], people: [] })),
  ]);

  // -- Owned results (from Plex) --
  const ownedMovies: SearchMovie[] = (libraryData.movies ?? []).map(
    (m: SearchMovie) => ({ ...m, media_type: 'movie' as const, isOwned: true }),
  );
  const ownedTV: SearchMovie[] = (libraryData.tv_shows ?? []).map(
    (s: SearchMovie) => ({ ...s, media_type: 'tv' as const, isOwned: true }),
  );
  const owned = [...ownedMovies, ...ownedTV];

  // Build set of owned title keys for dedup
  const ownedKeys = new Set(owned.map((o) => titleKey(getTitle(o), getYear(o))));

  // -- Unowned results (from TMDb), filtering out already-owned --
  const tmdbMovies: SearchMovie[] = (tmdbData.movies ?? [])
    .map((m: SearchMovie) => ({ ...m, media_type: 'movie' as const }))
    .filter((m: SearchMovie) => !ownedKeys.has(titleKey(getTitle(m), getYear(m))));
  const tmdbTV: SearchMovie[] = (tmdbData.tv_shows ?? [])
    .map((s: SearchMovie) => ({ ...s, media_type: 'tv' as const }))
    .filter((s: SearchMovie) => !ownedKeys.has(titleKey(getTitle(s), getYear(s))));
  // Interleave by TMDb's cross-type relevance rank. Plain concatenation put
  // every movie ahead of every TV show — "Severance" listed 12 movies before
  // the show. Ties and older payloads without a rank keep their arrival order.
  const unowned = [...tmdbMovies, ...tmdbTV].sort(
    (a, b) => (a.relevance_rank ?? Number.MAX_SAFE_INTEGER) - (b.relevance_rank ?? Number.MAX_SAFE_INTEGER),
  );

  // -- People: Plex-owned vs TMDb-unowned, sorted by popularity --
  const ownedPeople: SearchPerson[] = libraryData.people ?? [];
  const ownedPeopleNames = new Set(
    ownedPeople.map((p: SearchPerson) => p.name.toLowerCase()),
  );
  const unownedPeople: SearchPerson[] = (tmdbData.people ?? [])
    .filter((p: SearchPerson) => !ownedPeopleNames.has(p.name.toLowerCase()))
    .sort((a: SearchPerson, b: SearchPerson) => {
      // Images first, then by popularity DESC
      const aHasImg = a.profile_path || a.profile_url ? 1 : 0;
      const bHasImg = b.profile_path || b.profile_url ? 1 : 0;
      if (aHasImg !== bHasImg) return bHasImg - aHasImg;
      return (b.popularity ?? 0) - (a.popularity ?? 0);
    });

  return { owned, unowned, ownedPeople, unownedPeople };
}

// ---------------------------------------------------------------------------
// checkOwnership — single item, used on click
// ---------------------------------------------------------------------------

export async function checkOwnership(
  title: string,
  year?: number | null,
  tmdbId?: number,
  imdbId?: string,
): Promise<{ owned: boolean; movie?: any }> {
  const params = new URLSearchParams();
  params.set('title', title);
  if (year) params.set('year', String(year));
  if (tmdbId) params.set('tmdb_id', String(tmdbId));
  if (imdbId) params.set('imdb_id', imdbId);
  const resp = await fetch(`/api/check-ownership?${params}`);
  return resp.json();
}

// ---------------------------------------------------------------------------
// fetchPersonCredits — filmography for a TMDb person
// ---------------------------------------------------------------------------

export async function fetchPersonCredits(
  tmdbPersonId: number,
): Promise<{ credits: PersonCredit[]; person: { name: string; profile_url: string | null } }> {
  const resp = await fetch(`/api/tmdb/person_by_id/${tmdbPersonId}/credits`);
  return resp.json();
}

// ---------------------------------------------------------------------------
// batchCheckOwnership — for person filmography
// ---------------------------------------------------------------------------

export async function batchCheckOwnership(
  payload: {
    movies?: { title: string; year?: number | null; tmdb_id?: number }[];
    tv?: { title: string; year?: number | null; tmdb_id?: number; tvdb_id?: number; imdb_id?: string }[];
    use_media_paths?: boolean;
  },
): Promise<Record<string, { owned: boolean; rating_key?: number }>> {
  const resp = await fetch('/api/check-ownership-batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await resp.json();
  return data.results ?? {};
}

// ---------------------------------------------------------------------------
// Re-export helpers for use in components
// ---------------------------------------------------------------------------

export { getPosterUrl, getTitle, getYear, titleKey };
