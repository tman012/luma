import { apiFetch } from '../apiClient';
/**
 * Library Browse API service.
 *
 * Fetches media paths, library stats, browse items, and genres
 * from existing backend endpoints.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface MediaPath {
  name: string;
  path: string;
  type: string;
  kodi_path?: string;
}

export interface BrowseItem {
  title: string;
  year: number | null;
  rating_key: number;
  tmdb_id: number | null;
  poster_url: string | null;
  art_url: string | null;
  type: string;
  genres: string[];
  rating: number | null;
  added_at: number | null;
}

export interface BrowseResponse {
  items: BrowseItem[];
  total: number;
}

export interface BrowseParams {
  library: string;
  offset?: number;
  limit?: number;
  sort?: string;
  direction?: 'asc' | 'desc';
  genre?: string;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

/** Fetch media.paths from the settings endpoint. */
export async function fetchMediaPaths(): Promise<MediaPath[]> {
  const resp = await apiFetch('/api/settings');
  if (!resp.ok) return [];
  const data = await resp.json();
  return (data?.media?.paths ?? []) as MediaPath[];
}

/** Fetch paginated browse items for a library. */
export async function fetchLibraryBrowse(params: BrowseParams): Promise<BrowseResponse> {
  const qs = new URLSearchParams();
  qs.set('library', params.library);
  if (params.offset != null) qs.set('offset', String(params.offset));
  if (params.limit != null) qs.set('limit', String(params.limit));
  if (params.sort) qs.set('sort', params.sort);
  if (params.direction) qs.set('direction', params.direction);
  if (params.genre) qs.set('genre', params.genre);

  const resp = await fetch(`/api/library/browse?${qs.toString()}`);
  if (!resp.ok) return { items: [], total: 0 };
  const data = await resp.json();
  // Backend returns { movies: [...], total: N } — normalize to BrowseResponse
  return {
    items: (data.movies ?? []) as BrowseItem[],
    total: data.total ?? 0,
  };
}

/** Fetch available genres for a library. */
export async function fetchLibraryGenres(library: string): Promise<string[]> {
  const resp = await fetch(`/api/library/genres?library=${encodeURIComponent(library)}`);
  if (!resp.ok) return [];
  const data = await resp.json();
  return (data?.genres ?? []) as string[];
}
