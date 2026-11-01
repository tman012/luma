import { useState, useEffect } from 'react';
import { pickBestTrailer } from '@/utils/pickBestTrailer';
import {
  parseTmdbTvResponse,
  parsePlexTvResponse,
  type TvShowDetail,
  type UltraBlurColors,
} from '@/utils/modalParsers';
import {
  checkSonarrStatus,
  fetchTvSeasons,
  fetchTvOwnership,
  type SonarrStatus,
  type TvSeason,
  type OwnershipMap,
} from '@/services/sonarrApi';
import { resolveOwnership, mediaKey } from '@/services/ownership';
import { detailFetch, remainingCap } from '@/services/modalPrefetch';
import { TMDB_BACKDROP_WAIT_MS } from './useMovieDetail';
import { useModalColors } from './useModalColors';
import { logoImg } from '@/utils/image';

// ---------------------------------------------------------------------------
// Return type
// ---------------------------------------------------------------------------

interface UseTvDetailResult {
  show: TvShowDetail | null;
  seasons: TvSeason[];
  tvdbId: number | null;
  ownership: OwnershipMap;
  showOwned: boolean;
  sonarrStatus: SonarrStatus | null;
  loading: boolean;
  error: boolean;
  seasonsLoading: boolean;
  trailerKey: string | null | undefined;
  ultraBlurColors: UltraBlurColors | null;
  resolvedTmdbId: number | undefined;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useTvDetail(
  ratingKey?: number,
  tmdbId?: number,
): UseTvDetailResult {
  // Show-level data
  const [show, setShow] = useState<TvShowDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  // Seasons & episodes
  const [seasons, setSeasons] = useState<TvSeason[]>([]);
  const [seasonsLoading, setSeasonsLoading] = useState(true);

  // Ownership
  const [tvdbId, setTvdbId] = useState<number | null>(null);
  const [ownership, setOwnership] = useState<OwnershipMap>({});
  const [showOwned, setShowOwned] = useState(false);

  // Sonarr status
  const [sonarrStatus, setSonarrStatus] = useState<SonarrStatus | null>(null);

  // Trailer
  const [trailerKey, setTrailerKey] = useState<string | null | undefined>(undefined);

  // UltraBlur

  // With a Plex key, the tmdbId prop (from search) is only a hint used to
  // start /api/tv in parallel; Plex's validated tmdb_id decides. undefined
  // until /api/plex/tv answers, then the id (or null for none / rejected).
  const [plexTmdb, setPlexTmdb] = useState<number | null | undefined>(undefined);

  // Resolved tmdbId (from props or from show data)
  const resolvedTmdbId = ratingKey ? (plexTmdb ?? undefined) : (tmdbId ?? show?.tmdbId);

  // -------------------------------------------------------------------------
  // Extract trailer key + enrich cast from an already-fetched TMDb response.
  // Called inline from the show-details effect to avoid a duplicate /api/tv/
  // network request.
  // -------------------------------------------------------------------------
  /** Seed season posters/names from the already-fetched TMDb show payload so
   *  the Seasons button does not wait on /api/tv/seasons (1+N). Full episode
   *  data still arrives via the seasons effect below. */
  function seedSeasonsFromShowData(data: Record<string, unknown>) {
    const raw = (data.seasons ?? []) as Record<string, unknown>[];
    if (!raw.length) return;
    const mapped: TvSeason[] = raw.map((s) => ({
      season_number: (s.season_number as number) ?? 0,
      name: (s.name as string) ?? '',
      overview: (s.overview as string) ?? '',
      air_date: (s.air_date as string) ?? null,
      poster_path: (s.poster_path as string) ?? null,
      episode_count: (s.episode_count as number) ?? 0,
      episodes: [],
    }));
    const regular = mapped.filter((s) => s.season_number > 0);
    const display = regular.length > 0 ? regular : mapped;
    setSeasons((prev) => {
      // Don't clobber a fuller fetch that already landed.
      if (prev.some((s) => (s.episodes?.length ?? 0) > 0)) return prev;
      return display;
    });
    setSeasonsLoading(false);
  }

  function extractTrailerAndEnrich(data: Record<string, unknown>) {
    seedSeasonsFromShowData(data);
    const videos = data.videos as { results?: { key: string; site: string; type: string; name: string; official?: boolean; size?: number }[] } | undefined;
    setTrailerKey(pickBestTrailer(videos?.results));

    // Enrich Plex-sourced cast with TMDb person IDs for direct filmography
    const tmdbCredits = data.credits as Record<string, unknown> | undefined;
    const createdBy = (data.created_by ?? []) as Record<string, unknown>[];
    if (tmdbCredits || createdBy.length > 0) {
      const tmdbCast = ((tmdbCredits?.cast ?? []) as Record<string, unknown>[]);
      const tmdbCrew = ((tmdbCredits?.crew ?? []) as Record<string, unknown>[]);
      const nameToId = new Map<string, number>();
      for (const c of tmdbCast) {
        if (c.id && c.name) nameToId.set((c.name as string).toLowerCase(), c.id as number);
      }
      for (const c of tmdbCrew) {
        if (c.id && c.name) nameToId.set((c.name as string).toLowerCase(), c.id as number);
      }
      for (const c of createdBy) {
        if (c.id && c.name) nameToId.set((c.name as string).toLowerCase(), c.id as number);
      }
      setShow((prev) => {
        if (!prev) return prev;
        // If series cast is empty (e.g. Plex Roles missing + cache without credits
        // previously), populate from TMDb tv credits so Cast & Crew always loads.
        let nextCast = prev.cast;
        if (nextCast.length === 0 && tmdbCast.length > 0) {
          nextCast = tmdbCast.slice(0, 30).map((c) => ({
            name: (c.name as string) ?? '',
            character: (c.character as string) ?? '',
            profilePath: (c.profile_path as string) ?? null,
            tmdbPersonId: (c.id as number) ?? undefined,
          }));
        }
        const needsEnrich =
          nextCast.some((c) => !c.tmdbPersonId) ||
          prev.creators.some((d) => !d.tmdbPersonId) ||
          nextCast !== prev.cast;
        if (!needsEnrich) return prev;
        return {
          ...prev,
          cast: nextCast.map((c) =>
            c.tmdbPersonId ? c : { ...c, tmdbPersonId: nameToId.get(c.name.toLowerCase()) },
          ),
          creators: prev.creators.map((d) =>
            d.tmdbPersonId ? d : { ...d, tmdbPersonId: nameToId.get(d.name.toLowerCase()) },
          ),
        };
      });
    }
  }

  // -------------------------------------------------------------------------
  // Fetch show details
  // -------------------------------------------------------------------------
  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    async function fetchClearlogo(id: number, signal: AbortSignal): Promise<string | null> {
      try {
        const resp = await detailFetch(`/api/clearlogo/${id}?type=tv`, { signal });
        if (!resp.ok) return null;
        const data = (await resp.json()) as { clearlogo_url?: string };
        return logoImg(data.clearlogo_url);
      } catch {
        return null;
      }
    }

    async function fetchDetail() {
      // Another show opened: drop the old one so its backdrop never shows.
      setLoading(true);
      setShow(null);
      setError(false);
      setPlexTmdb(undefined);

      try {
        if (tmdbId && !ratingKey) {
          // Fetch from TMDb
          const resp = await detailFetch(`/api/tv/${tmdbId}`, { signal: controller.signal });
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          const data = (await resp.json()) as Record<string, unknown>;
          if (!cancelled) {
            const parsed = parseTmdbTvResponse(data);
            parsed.tmdbId = tmdbId;
            // Show modal immediately, fetch clearlogo in background
            setShow(parsed);
            // Unblock ownership + Sonarr fetches immediately (no longer waits for seasons)
            if (parsed.tvdbId) setTvdbId(parsed.tvdbId);
            extractTrailerAndEnrich(data);
            // Fire-and-forget clearlogo fetch (no longer blocks modal render)
            if (!parsed.clearLogoUrl) {
              fetchClearlogo(tmdbId, controller.signal).then((logo) => {
                if (!cancelled) setShow((prev) => prev ? { ...prev, clearLogoUrl: logo ?? prev.clearLogoUrl, logoSettled: true } : prev);
              });
            }
          }
        } else if (ratingKey) {
          // Fetch from Plex metadata API (works for any item type). A tmdbId
          // hint starts /api/tv now; detailFetch shares it with the request
          // below when Plex confirms the same id.
          if (tmdbId) void detailFetch(`/api/tv/${tmdbId}`).catch(() => null);
          const plexResp = await detailFetch(`/api/plex/tv/${ratingKey}`, { signal: controller.signal });
          if (!plexResp.ok) throw new Error('Could not fetch show details from Plex');
          const plexData = (await plexResp.json()) as Record<string, unknown>;
          const plexTmdbId = (plexData.tmdb_id as number | null) || null;
          if (!cancelled) setPlexTmdb(plexTmdbId);

          // With a TMDb id, give TMDb a moment (usually cached by the card
          // prefetch) so the first backdrop shown is the final one; without
          // it, render from Plex right away. Either way nothing swaps later.
          let earlyTmdb: Response | null = null;
          if (plexTmdbId) {
            let timer = 0;
            earlyTmdb = await Promise.race([
              detailFetch(`/api/tv/${plexTmdbId}`, { signal: controller.signal }).catch(() => null),
              new Promise<null>((res) => { timer = window.setTimeout(() => res(null), remainingCap(TMDB_BACKDROP_WAIT_MS)); }),
            ]);
            window.clearTimeout(timer);
            if (cancelled) return;
          }
          const plexBackdropShown = !earlyTmdb;
          if (!cancelled && plexBackdropShown) {
            const plexParsed = parsePlexTvResponse(plexData);
            plexParsed.ratingKey = ratingKey;
            plexParsed.isOwned = true;
            plexParsed.backdropPending = !!plexTmdbId;
            setShow(plexParsed);
            setLoading(false);
            const plexTvdbEarly = plexData.tvdb_id as number | null;
            if (plexTvdbEarly) setTvdbId(plexTvdbEarly);
          }

          if (plexTmdbId && !cancelled) {
            // We got a TMDb ID from Plex guids — fetch rich details from TMDb
            // A TMDb network error falls back to the Plex details, as a TMDb error status does.
            const tmdbResp = earlyTmdb ?? await detailFetch(`/api/tv/${plexTmdbId}`, { signal: controller.signal })
              .catch((e: unknown) => { if ((e as { name?: string })?.name === 'AbortError') throw e; return null; });
            if (tmdbResp?.ok) {
              const tmdbData = (await tmdbResp.json()) as Record<string, unknown>;
              if (!cancelled) {
                const parsed = parseTmdbTvResponse(tmdbData);
                parsed.ratingKey = ratingKey;
                parsed.tmdbId = plexTmdbId;
                parsed.isOwned = true;
                // Plex art can't be checked for baked-in title text: with a logo, TMDb's text-free backdrop is the pick
                // (the sheet takes it only if its one backdrop hasn't shown yet; see useSheetBackdrop).
                const textless = tmdbData.has_logo && tmdbData.backdrop_textless ? parsed.backdropUrl : null;
                setShow((prev) => (plexBackdropShown && prev?.backdropUrl
                  ? { ...parsed, backdropUrl: prev.backdropUrl, backdropTextlessUrl: textless && textless !== prev.backdropUrl ? textless : null }
                  : parsed));
                setLoading(false);
                if (parsed.tvdbId) setTvdbId(parsed.tvdbId);
                extractTrailerAndEnrich(tmdbData);
                if (!parsed.clearLogoUrl) {
                  fetchClearlogo(plexTmdbId, controller.signal).then((logo) => {
                    if (!cancelled) setShow((prev) => prev ? { ...prev, clearLogoUrl: logo ?? prev.clearLogoUrl, logoSettled: true } : prev);
                  });
                }
              }
            } else if (!cancelled) {
              // TMDb fetch failed, fall back to Plex-only data
              const parsed = parsePlexTvResponse(plexData);
              parsed.ratingKey = ratingKey;
              parsed.isOwned = true;
              setShow(parsed);
              setLoading(false);
              setTrailerKey(null);
              if (!parsed.clearLogoUrl && plexTmdbId) {
                fetchClearlogo(plexTmdbId, controller.signal).then((logo) => {
                  if (!cancelled) setShow((prev) => prev ? { ...prev, clearLogoUrl: logo ?? prev.clearLogoUrl, logoSettled: true } : prev);
                });
              }
            }
          } else if (!cancelled) {
            // No TMDb ID available, use Plex data directly
            const parsed = parsePlexTvResponse(plexData);
            parsed.ratingKey = ratingKey;
            parsed.logoSettled = true; // no TMDb id: Plex's logo (or none) is final
            setShow(parsed);
            setTrailerKey(null); // No TMDb data, no trailer

            // If Plex provided a tvdb_id directly, use it for ownership/Sonarr
            const plexTvdbId = plexData.tvdb_id as number | null;
            if (plexTvdbId) {
              setTvdbId(plexTvdbId);
            }
          }
        } else {
          throw new Error('No tmdbId or ratingKey provided');
        }
      } catch (err: unknown) {
        const e = err as { name?: string };
        if (e?.name !== 'AbortError' && !cancelled) {
          setError(true);
          setTrailerKey(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchDetail();
    return () => { cancelled = true; controller.abort(); };
  }, [ratingKey, tmdbId]);

  // -------------------------------------------------------------------------
  // Per-item ownership check — when modal opens with only tmdbId (no ratingKey),
  // check if the show is actually owned in Plex. Uses the session-level
  // ownership cache so it's instant if the batch check already ran.
  // -------------------------------------------------------------------------
  const ownershipTitle = show?.title;
  const ownershipYear = show?.year;
  const ownershipIsOwned = show?.isOwned;
  useEffect(() => {
    if (!ownershipTitle || ownershipIsOwned || ratingKey) return;
    let cancelled = false;

    resolveOwnership([{
      title: ownershipTitle,
      year: ownershipYear ?? null,
      tmdbId: tmdbId ?? null,
      ratingKey: null,
      mediaType: 'tv',
    }]).then((results) => {
      if (cancelled) return;
      const key = mediaKey({ title: ownershipTitle, year: ownershipYear ?? null, mediaType: 'tv' });
      const match = results.get(key);
      if (match?.owned) {
        setShow((prev) => prev ? {
          ...prev,
          isOwned: true,
          ratingKey: match.ratingKey ?? prev.ratingKey,
        } : prev);
        setShowOwned(true);
      }
    });

    return () => { cancelled = true; };
  }, [ownershipTitle, ownershipYear, ownershipIsOwned, ratingKey, tmdbId]);

  // -------------------------------------------------------------------------
  // Fetch seasons (once we have tmdbId)
  // Light summary first (1 TMDb call) so the Seasons button/shelf appear
  // immediately; full episode grid follows in the background.
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (!resolvedTmdbId) return;
    let cancelled = false;

    function applySeasons(result: Awaited<ReturnType<typeof fetchTvSeasons>>, { clearLoading }: { clearLoading: boolean }) {
      if (cancelled) return;
      // A response without a seasons list (an error body, a partial mock) is no seasons, not a page error.
      const all = Array.isArray(result?.seasons) ? result.seasons : [];
      const regularSeasons = all.filter((s) => s.season_number > 0);
      const displaySeasons = regularSeasons.length > 0 ? regularSeasons : all;
      if (displaySeasons.length > 0) setSeasons(displaySeasons);
      if (result?.tvdb_id) setTvdbId(result.tvdb_id);
      if (clearLoading) setSeasonsLoading(false);
    }

    async function fetchSeasons() {
      // Phase 1: metadata only — unblocks Seasons control for long-running shows
      const light = await fetchTvSeasons(resolvedTmdbId!, { light: true });
      applySeasons(light, { clearLoading: true });

      // Phase 2: full episodes + library grid (may take a while for many seasons)
      const full = await fetchTvSeasons(resolvedTmdbId!);
      applySeasons(full, { clearLoading: true });
    }

    fetchSeasons();
    return () => { cancelled = true; };
  }, [resolvedTmdbId]);

  // -------------------------------------------------------------------------
  // Fetch ownership (once we have tvdbId + show title)
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (!tvdbId) return;
    let cancelled = false;

    async function fetchOwnership() {
      const result = await fetchTvOwnership(tvdbId!, show?.title, show?.year ?? undefined, ratingKey);
      if (!cancelled) {
        setOwnership(result.episodes);
        setShowOwned(result.is_owned);
      }
    }

    fetchOwnership();
    return () => { cancelled = true; };
  }, [tvdbId, show?.title, show?.year, ratingKey]);

  // -------------------------------------------------------------------------
  // Fetch Sonarr status (once we have tvdbId)
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (!tvdbId) return;
    let cancelled = false;

    async function fetchStatus() {
      const status = await checkSonarrStatus(tvdbId!, resolvedTmdbId ?? undefined);
      if (!cancelled) setSonarrStatus(status);
    }

    fetchStatus();
    return () => { cancelled = true; };
  }, [tvdbId, resolvedTmdbId]);

  // -------------------------------------------------------------------------
  // Color blur: cached per item, settled before the popup leaves its loading
  // state. With only a rating key, wait for the show (its TMDb id) first so
  // the palette is fetched once with the final ids.
  // -------------------------------------------------------------------------
  const colorsEnabled = !!tmdbId || (!loading && (!!show || error));
  const colorsTmdbId = ratingKey ? (plexTmdb === undefined ? tmdbId : (plexTmdb ?? undefined)) : resolvedTmdbId;
  const { colors: ultraBlurColors, settled: colorsSettled } =
    useModalColors('tv', ratingKey, colorsTmdbId, colorsEnabled);

  return {
    show,
    seasons,
    tvdbId,
    ownership,
    showOwned,
    sonarrStatus,
    loading: loading || (!colorsSettled && !error),
    error,
    seasonsLoading,
    trailerKey,
    ultraBlurColors,
    resolvedTmdbId,
  };
}
