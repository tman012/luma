/**
 * allSourcesApi — canonical client for the "View all results" sources feature.
 *
 * This is the ONE place that talks to the ungated indexer endpoint, the TMDb
 * match-search endpoint, and the grab-to-library pipeline. Both the movie and
 * TV modals route their "all results" logic through here so the two stay
 * consistent (one fetch impl, one merge/dedup impl, one grab impl, one picker
 * data source).
 *
 * Backend contracts (live, verified):
 *   GET  /api/sources/all-results?title=&media_type=&year=&tmdb_id=
 *       (title may be a plain-text override; tmdb_id is identity-only)
 *   GET  /api/tmdb/match-search?query=&media_type=
 *   POST /api/downloads/add
 */

export type MediaType = 'movie' | 'tv';

// ---------------------------------------------------------------------------
// Normalized release shape (camelCase, matches modal conventions)
// ---------------------------------------------------------------------------

export interface AllResultsRelease {
  guid: string;
  indexer: string;
  indexerId: number;
  title: string;
  size: number; // bytes
  seeders: number;
  leechers: number;
  quality: string; // label string
  protocol: string; // "torrent" | "usenet"
  downloadUrl: string;
  infoHash: string;
  pubDate: string | null;
  origin: 'indexer';
  rejected: boolean;
  rejectionReasons: string[];
}

export interface TmdbMatchResult {
  tmdb_id: number;
  media_type: MediaType;
  title: string;
  year: number | null;
  poster_path: string | null;
}

// ---------------------------------------------------------------------------
// Fetch raw indexer results (NO quality gate)
// ---------------------------------------------------------------------------

/**
 * Fetch every raw indexer result for a title (movie + TV/sports categories for
 * TV). No quality gate is applied server-side. Returns a normalized array;
 * throws on missing title (caller guarantees this) but returns [] on a 503
 * (no indexer configured) so the modal degrades gracefully.
 */
export async function fetchAllResults(
  title: string,
  mediaType: MediaType,
  year?: number | null,
  tmdbId?: number,
  signal?: AbortSignal,
): Promise<AllResultsRelease[]> {
  if (!title) throw new Error('fetchAllResults: title is required');
  const params = new URLSearchParams();
  params.set('title', title);
  params.set('media_type', mediaType);
  if (year) params.set('year', String(year));
  if (tmdbId) params.set('tmdb_id', String(tmdbId));

  const resp = await fetch(`/api/sources/all-results?${params.toString()}`, { signal });
  if (!resp.ok) {
    // 503 (no indexer) / 400 (bad request) — degrade to empty rather than
    // blowing up the Arr results the modal already has.
    return [];
  }
  const data = (await resp.json()) as { releases?: Record<string, unknown>[] };
  return (data.releases ?? []).map(normalizeIndexerRelease);
}

function normalizeIndexerRelease(r: Record<string, unknown>): AllResultsRelease {
  const size = (r.size as number) ?? 0;
  return {
    guid: (r.guid as string) ?? '',
    indexer: (r.indexer as string) || 'Unknown',
    indexerId: (r.indexer_id as number) ?? 0,
    title: (r.title as string) ?? '',
    size,
    seeders: (r.seeders as number) ?? 0,
    leechers: (r.leechers as number) ?? 0,
    quality: (r.quality as string) ?? '',
    protocol: (r.protocol as string) ?? 'torrent',
    downloadUrl: (r.download_url as string) ?? '',
    infoHash: (r.info_hash as string) ?? '',
    pubDate: (r.pub_date as string) ?? null,
    origin: 'indexer',
    rejected: (r.rejected as boolean) ?? false,
    rejectionReasons: (r.rejection_reasons as string[]) ?? [],
  };
}

// ---------------------------------------------------------------------------
// TMDb match search (feeds ManualMatchPicker)
// ---------------------------------------------------------------------------

export async function tmdbMatchSearch(
  query: string,
  mediaType?: MediaType,
): Promise<TmdbMatchResult[]> {
  if (!query.trim()) return [];
  const params = new URLSearchParams();
  params.set('query', query);
  if (mediaType) params.set('media_type', mediaType);
  try {
    const resp = await fetch(`/api/tmdb/match-search?${params.toString()}`);
    if (!resp.ok) return [];
    const data = (await resp.json()) as { results?: TmdbMatchResult[] };
    return data.results ?? [];
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Grab to library (app's own qBit → rename → library pipeline)
// ---------------------------------------------------------------------------

export interface GrabToLibraryArgs {
  downloadUrl: string;
  torrentName: string;
  tmdbId?: number;
  mediaType: MediaType;
  title: string;
  year?: number | null;
  imdbId?: string;
}

/**
 * Grab a release into the library. Requires a tmdbId — without a media identity
 * the backend renamer cannot place the file (risk R8), so we refuse rather than
 * downloading something that lands unnamed.
 */
export async function grabToLibrary(
  args: GrabToLibraryArgs,
): Promise<{ success: boolean; message?: string; error?: string }> {
  if (!args.tmdbId) {
    return { success: false, error: 'Pick a title to match before downloading — the file cannot be renamed without a TMDb identity.' };
  }
  if (!args.downloadUrl) {
    return { success: false, error: 'This release has no download link.' };
  }
  try {
    const resp = await fetch('/api/downloads/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        torrent_url: args.downloadUrl,
        torrent_name: args.torrentName,
        tmdb_data: {
          tmdb_id: args.tmdbId,
          media_type: args.mediaType,
          title: args.title,
          year: args.year ?? null,
          ...(args.imdbId ? { imdb_id: args.imdbId } : {}),
        },
      }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) return { success: false, error: data.error || `HTTP ${resp.status}` };
    return { success: true, message: data.message };
  } catch {
    return { success: false, error: 'Network error — could not reach server' };
  }
}

// ---------------------------------------------------------------------------
// Merge / dedup — the ONE implementation used by both modals
// ---------------------------------------------------------------------------

/** Minimal shape a release must expose to participate in merge/dedup. */
export interface MergeableRelease {
  guid?: string;
  title?: string;
  infoHash?: string;
}

function alnum(s: string | undefined | null): string {
  return (s ?? '').replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
}

/**
 * Merge Arr releases with raw indexer releases, de-duplicating by (in priority
 * order):
 *   1. info_hash (lowercased, exact)
 *   2. guid (exact)
 *   3. alnum(title) — non-alphanumerics stripped, lowercased
 *
 * On any collision the Arr release is KEPT (it carries quality-profile scoring,
 * proper grab routing, etc.) and the indexer duplicate is dropped. Indexer rows
 * that don't collide with any Arr row are appended.
 *
 * Generic over both release shapes so the movie modal (ProcessedRelease) and TV
 * modal (TvProcessedRelease) can share this single implementation.
 */
export function mergeReleases<
  A extends MergeableRelease,
  I extends MergeableRelease,
>(arrReleases: A[], indexerReleases: I[]): (A | I)[] {
  const hashKeys = new Set<string>();
  const guidKeys = new Set<string>();
  const titleKeys = new Set<string>();

  for (const a of arrReleases) {
    const h = alnum(a.infoHash);
    if (h) hashKeys.add(h);
    if (a.guid) guidKeys.add(a.guid);
    const t = alnum(a.title);
    if (t) titleKeys.add(t);
  }

  const merged: (A | I)[] = [...arrReleases];
  for (const i of indexerReleases) {
    const h = alnum(i.infoHash);
    if (h && hashKeys.has(h)) continue;
    if (i.guid && guidKeys.has(i.guid)) continue;
    const t = alnum(i.title);
    if (t && titleKeys.has(t)) continue;
    merged.push(i);
  }
  return merged;
}
