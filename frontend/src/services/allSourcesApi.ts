import { apiFetch } from '../apiClient';
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
  /** Tracker type from Settings > Trackers ('iptorrents', 'torznab', ...); '' for Prowlarr rows. */
  trackerType: string;
}

/** A tracker that was not searched or failed, as all-results reports it. */
export interface TrackerStatus {
  name: string;
  status: 'auth' | 'rate' | 'guard' | 'unavailable';
}

/** v185.1 (PM): the test instance's write guard refused the request; never a connection error. */
export const TEST_GUARD_TEXT = 'Blocked by test guard';

/** Short status line for one tracker, e.g. "IPTorrents sign-in failed". */
export function trackerStatusText(t: TrackerStatus): string {
  if (t.status === 'auth') return `${t.name} sign-in failed`;
  if (t.status === 'rate') return `${t.name} search limit reached`;
  if (t.status === 'guard') return `${t.name}: ${TEST_GUARD_TEXT}`;
  return `${t.name} unavailable`;
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
  /** An explicit user retry: the server skips its caches (empty answers too). */
  refresh = false,
  /** Receives the trackers that were not searched or failed. */
  onTrackers?: (trackers: TrackerStatus[]) => void,
): Promise<AllResultsRelease[]> {
  if (!title) throw new Error('fetchAllResults: title is required');
  const params = new URLSearchParams();
  params.set('title', title);
  params.set('media_type', mediaType);
  if (year) params.set('year', String(year));
  if (tmdbId) params.set('tmdb_id', String(tmdbId));
  if (refresh) params.set('refresh', '1');

  const resp = await apiFetch(`/api/sources/all-results?${params.toString()}`, { signal });
  if (!resp.ok) {
    // 503 (no indexer) / 400 (bad request) — degrade to empty rather than
    // blowing up the Arr results the modal already has.
    return [];
  }
  const data = (await resp.json()) as { releases?: Record<string, unknown>[]; trackers?: unknown };
  if (onTrackers) {
    const list = Array.isArray(data.trackers) ? data.trackers : [];
    onTrackers(list
      .filter((t): t is { name: string; status: string } => !!t && typeof (t as { name?: unknown }).name === 'string')
      .map((t) => ({
        name: t.name,
        status: t.status === 'auth' || t.status === 'rate' || t.status === 'guard' ? t.status : 'unavailable',
      })));
  }
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
    trackerType: (r.tracker_type as string) || '',
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
    const resp = await apiFetch(`/api/tmdb/match-search?${params.toString()}`);
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
    const resp = await apiFetch('/api/downloads/add', {
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
    if (!resp.ok) return { success: false, error: data.error || "Couldn't add download" };
    return { success: true, message: data.message };
  } catch {
    return { success: false, error: "Can't reach server" };
  }
}

// ---------------------------------------------------------------------------
// Merge / dedup — the ONE implementation used by both modals
// ---------------------------------------------------------------------------

function alnum(s: string | undefined | null): string {
  return (s ?? '').replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
}

/** Minimal shape a release must expose to participate in merge/dedup. */
export interface MergeableRelease {
  guid?: string;
  title?: string;
  infoHash?: string;
  rejected?: boolean;
}

/**
 * Merge Arr releases with raw indexer releases, de-duplicating by (in priority
 * order):
 *   1. info_hash (lowercased, exact)
 *   2. guid (exact)
 *   3. alnum(title) — non-alphanumerics stripped, lowercased
 *
 * On a collision the Arr release is kept (quality scoring, grab routing),
 * unless the Arr copy is rejected and the tracker copy isn't: a release the
 * Arr profile rejects must not hide the same release from the owner's own
 * trackers. Indexer rows that don't collide are appended.
 */
export function mergeReleases<
  A extends MergeableRelease,
  I extends MergeableRelease,
>(arrReleases: A[], indexerReleases: I[]): (A | I)[] {
  const merged: (A | I)[] = [...arrReleases];
  const byHash = new Map<string, number>();
  const byGuid = new Map<string, number>();
  const byTitle = new Map<string, number>();
  const index = (r: MergeableRelease, i: number) => {
    const h = alnum(r.infoHash);
    if (h && !byHash.has(h)) byHash.set(h, i);
    if (r.guid && !byGuid.has(r.guid)) byGuid.set(r.guid, i);
    const t = alnum(r.title);
    if (t && !byTitle.has(t)) byTitle.set(t, i);
  };
  arrReleases.forEach((a, i) => index(a, i));
  const arrCount = arrReleases.length;

  for (const r of indexerReleases) {
    const h = alnum(r.infoHash);
    const t = alnum(r.title);
    const hit =
      (h ? byHash.get(h) : undefined) ??
      (r.guid ? byGuid.get(r.guid) : undefined) ??
      (t ? byTitle.get(t) : undefined);
    if (hit === undefined) {
      merged.push(r);
      continue;
    }
    if (hit < arrCount && merged[hit]?.rejected && !r.rejected) {
      // Replaced once: later tracker copies of this release are duplicates.
      merged[hit] = r;
    }
  }
  return merged;
}
