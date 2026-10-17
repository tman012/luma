import { apiFetch } from '../../../apiClient';
/**
 * useMovieSourcesData — the ONE implementation of "search sources for a movie".
 *
 * Used by SourcesPanel (compact + expanded views) and by the ExpandedLayout
 * sources shelf. ExpandedLayout previously carried its own inline copy of this
 * logic that had drifted: it awaited the two legs sequentially, and it treated
 * a single failed leg as a total failure, so a Radarr hiccup blanked out
 * indexer rows that had come back fine.
 *
 * ALL results are fetched up front: the Radarr releases AND the ungated indexer
 * sweep (via allSourcesApi) are merged with the shared mergeReleases() dedup
 * (Arr wins on collision), and rejected rows are KEPT. The panel decides
 * client-side whether to hide rejected rows — toggling that does NOT re-search.
 *
 * The two fetches run CONCURRENTLY and fail INDEPENDENTLY. A tracker outage or a
 * slow indexer sweep must never blank out Radarr rows that already came back
 * (and vice versa) — `error` is only set when both sides fail.
 *
 * Both legs are bounded by a client-side timeout. Without one a stalled search
 * is an indefinite spinner: the Radarr endpoint alone can sit for its full 120s
 * server-side budget, which reads as "broken" rather than "slow".
 */

import { useState, useEffect, useRef } from 'react';
import { processRawReleases, extractResolution, type ProcessedRelease } from './sourcesUtils';
import { classifySourceType } from '@/components/media/sourcesShared';
import { fetchAllResults, mergeReleases, type AllResultsRelease } from '@/services/allSourcesApi';

/** Radarr's interactive search gets 120s server-side; allow slack past that. */
const ARR_TIMEOUT_MS = 130_000;
/** The all-results indexer sweep gets 60s server-side. */
const INDEXER_TIMEOUT_MS = 75_000;
/** How long a search may run before we tell the user it is unusually slow. */
const SLOW_AFTER_MS = 10_000;

interface UseMovieSourcesDataOptions {
  tmdbId?: number;
  title: string;
  year: number | null;
  /**
   * Optional plain-text query used ONLY for the ungated indexer / Prowlarr
   * sweep (fetchAllResults). Does not change Arr interactive search or the
   * grab/rename identity — those keep using title + tmdbId.
   */
  indexerQuery?: string | null;
  /**
   * Skip the search entirely while false. Lets a caller that keeps this hook
   * mounted behind a closed shelf (ExpandedLayout) defer the search until the
   * shelf actually opens.
   */
  enabled?: boolean;
}

interface UseMovieSourcesDataResult {
  loading: boolean;
  error: boolean;
  /** The search was cut off by its client-side timeout rather than refused. */
  timedOut: boolean;
  /** Still loading, and past SLOW_AFTER_MS — the UI should say so. */
  slow: boolean;
  releases: ProcessedRelease[];
  movieId: number;
}

interface SourcesResult {
  releases: ProcessedRelease[];
  movieId: number;
  /** Both legs failed — there is nothing to show. */
  failed: boolean;
  timedOut: boolean;
}

/** Adapt a raw all-results indexer release into the ProcessedRelease shape. */
function indexerToProcessed(r: AllResultsRelease): ProcessedRelease {
  const resolution = extractResolution(r.quality, r.title);
  const sourceType = classifySourceType(`${r.quality} ${r.title}`);
  const sizeGb = r.size ? (r.size / (1024 * 1024 * 1024)).toFixed(2) : null;
  return {
    guid: r.guid,
    title: r.title,
    indexer: r.indexer,
    indexerId: r.indexerId,
    size: r.size,
    seeders: r.seeders,
    leechers: r.leechers,
    quality: r.quality,
    customFormatScore: 0,
    age: null,
    protocol: r.protocol,
    resolution,
    sourceType,
    sizeGb,
    rejected: r.rejected,
    rejectionReasons: r.rejectionReasons,
    origin: 'indexer',
    downloadUrl: r.downloadUrl,
    infoHash: r.infoHash,
    trackerType: r.trackerType,
  };
}

/** An abort signal that fires after `ms`, and a way to ask whether it did. */
function deadline(ms: number) {
  const controller = new AbortController();
  let expired = false;
  const timer = setTimeout(() => {
    expired = true;
    controller.abort();
  }, ms);
  return {
    signal: controller.signal,
    clear: () => clearTimeout(timer),
    get expired() {
      return expired;
    },
  };
}

async function loadSources(
  tmdbId: number | undefined,
  title: string,
  year: number | null,
  indexerQuery?: string | null,
  refresh = false,
): Promise<SourcesResult> {
  const arrClock = deadline(ARR_TIMEOUT_MS);
  const indexerClock = deadline(INDEXER_TIMEOUT_MS);
  // Plain-text override replaces only the indexer query string. When overriding,
  // omit year — matched metadata year (often wrong for sports PPVs / fuzzy
  // TMDb hits) turns "aew forbidden door" into "… 2021" and zeroes IPT Sports.
  const indexerTitle = (indexerQuery || '').trim() || title;
  const indexerYear = (indexerQuery || '').trim() ? null : year;

  /** Radarr interactive search. Throws when the call fails or times out. */
  async function fetchArr(): Promise<{ releases: ProcessedRelease[]; movieId: number }> {
    const params = new URLSearchParams();
    if (tmdbId) params.set('tmdb_id', String(tmdbId));
    params.set('title', title);
    if (year) params.set('year', String(year));
    try {
      const resp = await apiFetch(
        `/api/radarr/movie/releases?${params.toString()}`,
        { signal: arrClock.signal },
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = (await resp.json()) as Record<string, unknown>;
      return {
        releases: processRawReleases((data.releases ?? []) as Record<string, unknown>[]),
        movieId: (data.movie_id as number) ?? 0,
      };
    } finally {
      arrClock.clear();
    }
  }

  // Both legs in flight at once — the indexer sweep is the slow one and
  // there's no reason to make Radarr wait behind it.
  const [arrOutcome, indexerOutcome] = await Promise.allSettled([
    fetchArr(),
    fetchAllResults(indexerTitle, 'movie', indexerYear, tmdbId, indexerClock.signal, refresh)
      .finally(() => indexerClock.clear()),
  ]);

  const arr = arrOutcome.status === 'fulfilled' ? arrOutcome.value : null;
  const indexerReleases =
    indexerOutcome.status === 'fulfilled'
      ? indexerOutcome.value.map(indexerToProcessed)
      : [];

  return {
    // Merge whatever came back (Arr wins on collision). Rejected rows are
    // kept — the panel decides client-side whether to show them.
    releases: mergeReleases(arr?.releases ?? [], indexerReleases) as ProcessedRelease[],
    movieId: arr?.movieId ?? 0,
    // Only a total failure is an error. One leg down still renders rows; zero
    // rows from two healthy legs is "no sources", which the panel handles.
    failed: arrOutcome.status === 'rejected' && indexerOutcome.status === 'rejected',
    timedOut:
      (arrOutcome.status === 'rejected' && arrClock.expired) ||
      (indexerOutcome.status === 'rejected' && indexerClock.expired),
  };
}

/**
 * In-flight searches, keyed by the movie being searched.
 *
 * Closing and reopening the sources shelf remounts this hook. Without
 * coalescing that fires a SECOND Radarr interactive search while the first is
 * still running, and Radarr re-queries every indexer on each call — so the
 * duplicate doubles tracker load and can push the original past its own
 * timeout. That is exactly what happened during a library-copy stall: two
 * searches for the same movie, one returning at 90s and the other timing out.
 *
 * Deliberately NOT tied to a consumer's AbortSignal. Aborting the fetch does
 * not stop Radarr's search server-side, so cancelling on unmount buys nothing
 * and would break the shared request for anyone still waiting on it.
 */
const inFlight = new Map<string, Promise<SourcesResult>>();

/** Identity of a search — also what the hook uses to tell searches apart. */
function searchKey(
  tmdbId: number | undefined,
  title: string,
  year: number | null,
  indexerQuery?: string | null,
): string {
  const iq = (indexerQuery || '').trim().toLowerCase();
  return `${tmdbId ?? ''}|${title.toLowerCase()}|${year ?? ''}|iq:${iq}`;
}

function requestSources(
  tmdbId: number | undefined,
  title: string,
  year: number | null,
  indexerQuery?: string | null,
  refresh = false,
): Promise<SourcesResult> {
  const key = searchKey(tmdbId, title, year, indexerQuery);
  const running = inFlight.get(key);
  if (running) return running;

  const request = loadSources(tmdbId, title, year, indexerQuery, refresh).finally(() => {
    inFlight.delete(key);
  });
  inFlight.set(key, request);
  return request;
}

export function useMovieSourcesData({
  tmdbId,
  title,
  year,
  indexerQuery = null,
  enabled = true,
}: UseMovieSourcesDataOptions): UseMovieSourcesDataResult {
  const [error, setError] = useState(false);
  const [timedOut, setTimedOut] = useState(false);
  const [slow, setSlow] = useState(false);
  const [releases, setReleases] = useState<ProcessedRelease[]>([]);
  const [movieId, setMovieId] = useState(0);
  const [settledKey, setSettledKey] = useState<string | null>(null);

  const key = enabled && title ? searchKey(tmdbId, title, year, indexerQuery) : null;

  // Derived rather than a `loading` state: a state flag only flips once the
  // effect runs, which leaves one painted frame where the panel is enabled,
  // has no rows, and is not "loading" — long enough to flash "No sources
  // found" every time the shelf opens.
  const loading = key !== null && settledKey !== key;
  // Override search / clear override is an explicit retry: skip server caches.
  const lastQuery = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    if (key === null) return;

    let cancelled = false;
    setError(false);
    setTimedOut(false);
    setSlow(false);
    // Clear rows and the settled marker from any previous search, so a
    // re-search can never leave the panel showing results that no longer
    // belong to it (and so reopening the shelf reads as loading again).
    setReleases([]);
    setSettledKey(null);

    const slowTimer = setTimeout(() => {
      if (!cancelled) setSlow(true);
    }, SLOW_AFTER_MS);

    const refresh = lastQuery.current !== undefined && lastQuery.current !== (indexerQuery ?? null);
    lastQuery.current = indexerQuery ?? null;
    requestSources(tmdbId, title, year, indexerQuery, refresh)
      .then((result) => {
        if (cancelled) return;
        setReleases(result.releases);
        setMovieId(result.movieId);
        setError(result.failed);
        setTimedOut(result.timedOut);
        setSettledKey(key);
      })
      .catch(() => {
        if (cancelled) return;
        setError(true);
        setSettledKey(key);
      })
      .finally(() => clearTimeout(slowTimer));

    return () => {
      cancelled = true;
      clearTimeout(slowTimer);
    };
  }, [key, tmdbId, title, year, indexerQuery]);

  return { loading, error, timedOut, slow: loading && slow, releases, movieId };
}
