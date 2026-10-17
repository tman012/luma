import { useState, useEffect, useRef } from 'react';
import { pickBestTrailer } from '@/utils/pickBestTrailer';
import { backdropUrl as tmdbBackdrop, logoImg } from '@/utils/image';
import {
  parsePlexResponse,
  parseTmdbResponse,
  extractBelongsToCollection,
  type MediaDetail,
  type Ratings,
  type UltraBlurColors,
} from '@/utils/modalParsers';
import { apiFetch } from '../apiClient';
import { detailFetch, remainingCap } from '@/services/modalPrefetch';
import { useModalColors } from './useModalColors';

// ---------------------------------------------------------------------------
// Return type
// ---------------------------------------------------------------------------

/** How long a Plex-key opening waits for TMDb's HD backdrop. */
export const TMDB_BACKDROP_WAIT_MS = 500;

interface UseMovieDetailResult {
  detail: MediaDetail | null;
  ratings: Ratings | null;
  loading: boolean;
  ratingsLoading: boolean;
  error: boolean;
  trailerKey: string | null | undefined;
  ultraBlurColors: UltraBlurColors | null;
  resolvedTmdbId: number | undefined;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useMovieDetail(
  ratingKey?: number,
  tmdbId?: number,
  /** Bump to refetch everything in place (e.g. after Fix Match). */
  reloadNonce = 0,
): UseMovieDetailResult {
  const [detail, setDetail] = useState<MediaDetail | null>(null);
  const [ratings, setRatings] = useState<Ratings | null>(null);
  const [loading, setLoading] = useState(true);
  const [ratingsLoading, setRatingsLoading] = useState(true);
  const [error, setError] = useState(false);
  const [trailerKey, setTrailerKey] = useState<string | null | undefined>(undefined);

  // Track whether TMDb data was already fetched in the main effect (avoid duplicate fetch)
  const tmdbFetchedRef = useRef(false);

  // -------------------------------------------------------------------------
  // Fetch detail data — fetches Plex + TMDb in parallel when both IDs are
  // available so the HD TMDb backdrop is ready on first paint (no flash).
  // -------------------------------------------------------------------------
  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    tmdbFetchedRef.current = false;

    async function fetchDetail() {
      // A reload keeps the current content on screen until the new data lands.
      // Another title opened: drop the old one so its backdrop never shows.
      if (!reloadNonce) { setLoading(true); setDetail(null); }
      setError(false);

      try {
        if (ratingKey) {
          // Plex path — also fetch TMDb in parallel when tmdbId is known
          const plexPromise = detailFetch(`/api/plex/movie/${ratingKey}`, { signal: controller.signal })
            .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); }) as Promise<Record<string, unknown>>;

          const tmdbPromise: Promise<Record<string, unknown> | null> = tmdbId
            ? detailFetch(`/api/movie/${tmdbId}`, { signal: controller.signal })
                .then((r) => (r.ok ? r.json() : null))
                .catch(() => null)
            : Promise.resolve(null);

          const [plexData, tmdbFirst] = await Promise.all([plexPromise, tmdbPromise]);
          if (cancelled) return;

          const parsed = parsePlexResponse(plexData);
          parsed.ratingKey = ratingKey;
          if (tmdbId) parsed.tmdbId = tmdbId;

          // Opened with only a Plex key (Home rails): get TMDb now, briefly,
          // so the first backdrop shown is the final one (no swap later).
          let tmdbData = tmdbFirst;
          if (!tmdbData && !tmdbId && parsed.tmdbId) {
            let timer = 0;
            tmdbData = await Promise.race([
              detailFetch(`/api/movie/${parsed.tmdbId}`, { signal: controller.signal })
                .then((r) => (r.ok ? (r.json() as Promise<Record<string, unknown>>) : null))
                .catch(() => null),
              new Promise<null>((res) => { timer = window.setTimeout(() => res(null), remainingCap(TMDB_BACKDROP_WAIT_MS)); }),
            ]);
            window.clearTimeout(timer);
            if (cancelled) return;
          }

          // Merge TMDb HD backdrop + trailer immediately (no second flash)
          if (tmdbData) {
            tmdbFetchedRef.current = true;
            if (tmdbData.backdrop_path) {
              parsed.backdropUrl = tmdbBackdrop(tmdbData.backdrop_path as string);
            }
            const videos = (tmdbData.videos as Record<string, unknown> | undefined)?.results;
            setTrailerKey(pickBestTrailer(videos as unknown as Parameters<typeof pickBestTrailer>[0]));

            const coll = extractBelongsToCollection(tmdbData);
            if (coll.collectionId != null) {
              parsed.collectionId = coll.collectionId;
              parsed.collectionName = coll.collectionName;
            }

            // Enrich cast with TMDb person IDs
            const tmdbCredits = tmdbData.credits as Record<string, unknown> | undefined;
            if (tmdbCredits) {
              const tmdbCast = (tmdbCredits.cast ?? []) as Record<string, unknown>[];
              const tmdbCrew = (tmdbCredits.crew ?? []) as Record<string, unknown>[];
              const nameToId = new Map<string, number>();
              const nameToProfile = new Map<string, string>();
              for (const c of tmdbCast) {
                if (c.id && c.name) {
                  const key = (c.name as string).toLowerCase();
                  nameToId.set(key, c.id as number);
                  if (c.profile_path) nameToProfile.set(key, c.profile_path as string);
                }
              }
              for (const c of tmdbCrew) {
                if (c.id && c.name && c.job === 'Director') {
                  const key = (c.name as string).toLowerCase();
                  nameToId.set(key, c.id as number);
                  if (c.profile_path) nameToProfile.set(key, c.profile_path as string);
                }
              }
              parsed.cast = parsed.cast.map((c) => {
                const key = c.name.toLowerCase();
                return c.tmdbPersonId ? c : { ...c, tmdbPersonId: nameToId.get(key), profilePath: c.profilePath ?? nameToProfile.get(key) ?? null };
              });
              parsed.directors = parsed.directors.map((d) => {
                const key = d.name.toLowerCase();
                return { ...d, tmdbPersonId: d.tmdbPersonId ?? nameToId.get(key), profilePath: d.profilePath ?? nameToProfile.get(key) ?? null };
              });
            }
          }

          setDetail(parsed);
        } else if (tmdbId) {
          // TMDb-only path (unowned movies)
          const resp = await detailFetch(`/api/movie/${tmdbId}`, { signal: controller.signal });
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          const data = (await resp.json()) as Record<string, unknown>;
          if (!cancelled) {
            tmdbFetchedRef.current = true;
            const parsed = parseTmdbResponse(data);
            parsed.tmdbId = tmdbId;

            // Set trailer from same response
            const videos = (data.videos as Record<string, unknown> | undefined)?.results;
            setTrailerKey(pickBestTrailer(videos as unknown as Parameters<typeof pickBestTrailer>[0]));

            setDetail(parsed);
          }
        } else {
          throw new Error('No ratingKey or tmdbId provided');
        }
      } catch (err: unknown) {
        const e = err as { name?: string };
        if (e?.name !== 'AbortError' && !cancelled) {
          setError(true);
          setRatingsLoading(false); // Don't block modal visibility on error
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchDetail();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [ratingKey, tmdbId, reloadNonce]);

  // -------------------------------------------------------------------------
  // Per-item ownership check — fast Plex search (no full index build).
  // Returns owned status + libraries in a single lightweight call.
  // -------------------------------------------------------------------------
  const ownershipTitle = detail?.title;
  const ownershipYear = detail?.year;
  const ownershipIsOwned = detail?.isOwned;
  useEffect(() => {
    if (!ownershipTitle || ownershipIsOwned || ratingKey) return;
    let cancelled = false;

    const params = new URLSearchParams({ title: ownershipTitle });
    if (ownershipYear) params.set('year', String(ownershipYear));
    if (tmdbId) params.set('tmdb_id', String(tmdbId));

    apiFetch(`/api/plex/lookup?${params}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data: { owned: boolean; rating_key?: number; libraries?: string[]; library_keys?: Record<string, number> } | null) => {
        if (cancelled || !data?.owned) return;
        setDetail((prev) => prev ? {
          ...prev,
          isOwned: true,
          ratingKey: data.rating_key ?? prev.ratingKey,
          libraries: data.libraries ?? [],
          libraryKeys: data.library_keys ?? {},
        } : prev);
      })
      .catch(() => {});

    return () => { cancelled = true; };
  }, [ownershipTitle, ownershipYear, ownershipIsOwned, ratingKey, tmdbId]);

  // -------------------------------------------------------------------------
  // Fetch all ratings: Plex + OMDb in parallel, merge (Plex takes priority)
  // Waits for detail to be available (need title+year for OMDb).
  // -------------------------------------------------------------------------
  const movieTitle = detail?.title;
  const movieYear = detail?.year;
  useEffect(() => {
    if (!movieTitle) return;

    const controller = new AbortController();
    let cancelled = false;

    async function fetchAllRatings() {
      // Fire Plex + OMDb in parallel
      const plexPromise: Promise<Record<string, unknown> | null> = ratingKey
        ? apiFetch(`/api/plex/ratings/${ratingKey}`, { signal: controller.signal })
            .then((r) => (r.ok ? r.json() : null))
            .catch(() => null)
        : Promise.resolve(null);

      const params = new URLSearchParams();
      if (movieYear) params.set('year', String(movieYear));
      const omdbPromise: Promise<Record<string, unknown> | null> = apiFetch(
        `/api/ratings/${encodeURIComponent(movieTitle!)}?${params}`,
        { signal: controller.signal },
      )
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => (d?.error ? null : d))
        .catch(() => null);

      const [plexRaw, omdb] = await Promise.all([plexPromise, omdbPromise]);
      if (cancelled) return;

      const plex = plexRaw as (Ratings & { tmdb?: { score: number | null } }) | null;

      // Merge: Plex takes priority, OMDb fills gaps
      const merged: Ratings = {
        imdb:
          plex?.imdb?.score != null
            ? plex.imdb
            : (omdb?.imdb as Ratings['imdb']) ?? null,
        rotten_tomatoes_critic:
          plex?.rotten_tomatoes_critic?.score != null
            ? plex.rotten_tomatoes_critic
            : (omdb?.rotten_tomatoes_critic as Ratings['rotten_tomatoes_critic']) ?? null,
        rotten_tomatoes_audience:
          plex?.rotten_tomatoes_audience?.score != null
            ? plex.rotten_tomatoes_audience
            : (omdb?.rotten_tomatoes_audience as Ratings['rotten_tomatoes_audience']) ?? null,
        metacritic:
          plex?.metacritic?.score != null
            ? plex.metacritic
            : (omdb?.metacritic as Ratings['metacritic']) ?? null,
        // Never surface TMDb vote_average as a rating badge.
        tmdb: null,
      };

      setRatings(merged);
      setRatingsLoading(false);

      // Background fill: if RT critic or audience is missing, try RT scraper
      const missingRtCritic = merged.rotten_tomatoes_critic?.score == null;
      const missingRtAudience = merged.rotten_tomatoes_audience?.score == null;
      if (missingRtCritic || missingRtAudience) {
        const rtParams = new URLSearchParams({ title: movieTitle! });
        if (movieYear) rtParams.set('year', String(movieYear));
        apiFetch(`/api/rt/reviews?${rtParams}`, { signal: controller.signal })
          .then((r) => (r.ok ? r.json() : null))
          .then((rt: Record<string, unknown> | null) => {
            if (cancelled || !rt || !rt.success) return;
            setRatings((prev) => {
              if (!prev) return prev;
              const updated = { ...prev };
              if (prev.rotten_tomatoes_critic?.score == null && rt.critic_score != null) {
                updated.rotten_tomatoes_critic = { score: rt.critic_score as number };
              }
              if (prev.rotten_tomatoes_audience?.score == null && rt.audience_score != null) {
                updated.rotten_tomatoes_audience = { score: rt.audience_score as number };
              }
              return updated;
            });
          })
          .catch(() => {});
      }
    }

    fetchAllRatings();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [movieTitle, movieYear, ratingKey, reloadNonce]);

  // -------------------------------------------------------------------------
  // Fetch UltraBlurColors — race Plex (fast) vs TMDb (slow but higher quality)
  // Show Plex colors immediately, upgrade to TMDb if it resolves with data.
  // -------------------------------------------------------------------------
  // Color blur: cached per item (warmed from card hover/tap), settled before
  // the popup leaves its loading state so it fades in with the backdrop.
  const { colors: ultraBlurColors, settled: colorsSettled } = useModalColors('movie', ratingKey, tmdbId);

  // -------------------------------------------------------------------------
  // Fetch clearlogo from TMDb — always prefer TMDb over Plex (Plex sometimes
  // returns title cards with actor names instead of proper clearlogos).
  // Deps: detail?.tmdbId + tmdbId — runs once when IDs are known, won't
  // re-run on unrelated detail mutations (ownership, etc.) so no cancel race.
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (!detail) return;
    const id = tmdbId ?? detail.tmdbId;
    // Settled means a null logo is final ('no logo'), so the title shows as
    // text right away instead of waiting out the reveal cap.
    const settle = (url: string | null) => setDetail((prev) => prev
      ? { ...prev, clearLogoUrl: url ?? prev.clearLogoUrl, logoSettled: true } : prev);
    if (!id) { settle(null); return; }
    let cancelled = false;
    detailFetch(`/api/clearlogo/${id}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => { if (!cancelled) settle(logoImg(data?.clearlogo_url ?? null)); })
      .catch(() => { if (!cancelled) settle(null); });
    return () => { cancelled = true; };
  }, [detail?.tmdbId, tmdbId, reloadNonce]);

  // -------------------------------------------------------------------------
  // Resolve tmdbId for sources: prefer the one from props, then from detail
  // -------------------------------------------------------------------------
  const resolvedTmdbId = tmdbId ?? detail?.tmdbId;

  // -------------------------------------------------------------------------
  // Fetch trailer key + enrich cast with TMDb person IDs
  // Skipped when TMDb data was already fetched in the main detail effect.
  // Only needed when resolvedTmdbId comes from the Plex response (not props).
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (!resolvedTmdbId || tmdbFetchedRef.current) return;
    let cancelled = false;
    detailFetch(`/api/movie/${resolvedTmdbId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        const videos = data.videos?.results;
        setTrailerKey(pickBestTrailer(videos));

        // Always prefer TMDb backdrop over Plex backdrop
        if (data.backdrop_path) {
          const resolvedBackdrop = tmdbBackdrop(data.backdrop_path as string);
          // Only fill a missing backdrop: never swap one that's on screen.
          setDetail((prev) => prev && !prev.backdropUrl ? { ...prev, backdropUrl: resolvedBackdrop } : prev);
        }

        const coll = extractBelongsToCollection(data as Record<string, unknown>);
        if (coll.collectionId != null) {
          setDetail((prev) =>
            prev
              ? { ...prev, collectionId: coll.collectionId, collectionName: coll.collectionName }
              : prev,
          );
        }

        // Enrich Plex-sourced cast with TMDb person IDs for direct filmography navigation
        const tmdbCredits = data.credits as Record<string, unknown> | undefined;
        if (tmdbCredits) {
          const tmdbCast = (tmdbCredits.cast ?? []) as Record<string, unknown>[];
          const tmdbCrew = (tmdbCredits.crew ?? []) as Record<string, unknown>[];
          const nameToId = new Map<string, number>();
          for (const c of tmdbCast) {
            if (c.id && c.name) nameToId.set((c.name as string).toLowerCase(), c.id as number);
          }
          for (const c of tmdbCrew) {
            if (c.id && c.name && c.job === 'Director') nameToId.set((c.name as string).toLowerCase(), c.id as number);
          }
          setDetail((prev) => {
            if (!prev) return prev;
            const needsEnrich = prev.cast.some((c) => !c.tmdbPersonId) || prev.directors.some((d) => !d.tmdbPersonId);
            if (!needsEnrich) return prev;
            return {
              ...prev,
              cast: prev.cast.map((c) => c.tmdbPersonId ? c : { ...c, tmdbPersonId: nameToId.get(c.name.toLowerCase()) }),
              directors: prev.directors.map((d) => d.tmdbPersonId ? d : { ...d, tmdbPersonId: nameToId.get(d.name.toLowerCase()) }),
            };
          });
        }
      })
      .catch(() => {
        if (!cancelled) setTrailerKey(null);
      });
    return () => { cancelled = true; };
  }, [resolvedTmdbId]);

  return {
    detail,
    ratings,
    loading: loading || (!colorsSettled && !error),
    ratingsLoading,
    error,
    trailerKey,
    ultraBlurColors,
    resolvedTmdbId,
  };
}
