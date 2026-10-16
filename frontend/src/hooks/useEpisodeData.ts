/**
 * useEpisodeData — fetches and normalizes episode metadata for episode-mode
 * display in TvDetailModal.
 *
 * When episodeContext has an episodeRatingKey, fetches from Plex (owned).
 * Otherwise, fetches from TMDb using the show's tmdbId + season/episode numbers.
 *
 * The season grid is built from the library (Plex ∪ Sonarr), which is
 * TVDB-numbered, so an unowned episode's coordinates don't always exist on
 * TMDb — Hard Knocks' upcoming S27 episodes are season 21 upstream, and the
 * by-coordinate fetch 404s. `gridEpisode` is the already-resolved row from
 * that grid, used as the fallback so those episodes still render.
 *
 * Returns null when not in episode mode (no episodeContext provided).
 */

import { useState, useEffect } from 'react';
import type { EpisodeContext } from '@/contexts/ModalHostContext';
import type { EpisodeData } from '@/components/media/TvDetailModal/types';
import type { CastMember } from '@/utils/modalParsers';
import { fetchPlexEpisode, fetchTmdbEpisode } from '@/services/sonarrApi';
import { posterUrl } from '@/utils/image';
import { apiUrl } from '@/urlHelpers';

interface UseEpisodeDataResult {
  episodeData: EpisodeData | null;
  episodeLoading: boolean;
}

/** Minimal shape of a season-grid episode row (see /api/tv/seasons). */
export interface GridEpisode {
  episode_number: number;
  name?: string | null;
  overview?: string | null;
  air_date?: string | null;
  still_path?: string | null;
  runtime?: number | null;
}

function mapEpisodeCast(
  cast: { id: number; name: string; character: string; profile_path: string | null }[] | undefined,
  guests: { id: number; name: string; character: string; profile_path: string | null }[] | undefined,
  crew: { id: number; name: string; job: string; department: string; profile_path: string | null }[] | undefined,
): CastMember[] {
  const out: CastMember[] = [];
  const seen = new Set<string>();

  const push = (m: CastMember) => {
    const key = (m.tmdbPersonId != null ? `id:${m.tmdbPersonId}` : `n:${m.name.toLowerCase()}`);
    if (!m.name || seen.has(key)) return;
    seen.add(key);
    out.push(m);
  };

  for (const c of cast ?? []) {
    push({
      name: c.name ?? '',
      character: c.character ?? '',
      profilePath: c.profile_path ?? null,
      tmdbPersonId: c.id,
    });
  }
  for (const g of guests ?? []) {
    push({
      name: g.name ?? '',
      character: g.character ?? '',
      profilePath: g.profile_path ?? null,
      tmdbPersonId: g.id,
    });
  }
  // Key crew first-ish after cast: Director / Writer
  for (const c of crew ?? []) {
    if (c.job !== 'Director' && c.job !== 'Writer' && c.job !== 'Screenplay') continue;
    push({
      name: c.name ?? '',
      character: c.job ?? '',
      role: c.job ?? '',
      profilePath: c.profile_path ?? null,
      tmdbPersonId: c.id,
    });
  }
  return out.slice(0, 30);
}

export function useEpisodeData(
  episodeContext: EpisodeContext | undefined,
  tmdbId: number | undefined,
  gridEpisode?: GridEpisode | null,
): UseEpisodeDataResult {
  const [episodeData, setEpisodeData] = useState<EpisodeData | null>(null);
  const [episodeLoading, setEpisodeLoading] = useState(false);

  useEffect(() => {
    if (!episodeContext) {
      setEpisodeData(null);
      setEpisodeLoading(false);
      return;
    }

    let cancelled = false;
    setEpisodeLoading(true);

    async function fetchEpisode() {
      let plexBase: EpisodeData | null = null;

      // Prefer Plex endpoint if we have an episode rating key (owned)
      if (episodeContext!.episodeRatingKey) {
        const plex = await fetchPlexEpisode(episodeContext!.episodeRatingKey);
        if (cancelled) return;

        if (plex) {
          const runtimeMinutes = plex.duration ? Math.round(plex.duration / 60000) : null;
          plexBase = {
            title: plex.title,
            overview: plex.summary,
            airDate: plex.originally_available_at ?? null,
            runtime: runtimeMinutes,
            contentRating: plex.content_rating || null,
            rating: plex.rating ?? plex.audience_rating ?? null,
            voteCount: null,
            seasonNumber: plex.season_number,
            episodeNumber: plex.episode_number,
            isOwned: true,
            viewOffset: plex.view_offset || undefined,
            duration: plex.duration || undefined,
            thumbPath: plex.thumb_path
              ? apiUrl(`/api/img/plex?url=${plex.thumb_path}`)
              : undefined,
            cast: [],
          };
        }
      }

      // TMDb fetch — always try when we have tmdbId so episode cast/guest
      // stars land even for owned (Plex) episodes.
      if (tmdbId) {
        const tmdb = await fetchTmdbEpisode(
          tmdbId,
          episodeContext!.seasonNumber,
          episodeContext!.episodeNumber,
        );
        if (cancelled) return;

        if (tmdb) {
          const cast = mapEpisodeCast(tmdb.cast, tmdb.guest_stars, tmdb.crew);
          if (plexBase) {
            setEpisodeData({
              ...plexBase,
              // Prefer Plex overview/title when present; fill gaps from TMDb.
              overview: plexBase.overview || (tmdb.overview ?? ''),
              stillPath: plexBase.thumbPath ? undefined : (posterUrl(tmdb.still_path) ?? undefined),
              cast,
            });
            setEpisodeLoading(false);
            return;
          }

          setEpisodeData({
            title: tmdb.name ?? '',
            overview: tmdb.overview ?? '',
            airDate: tmdb.air_date ?? null,
            runtime: tmdb.runtime ?? null,
            contentRating: null,
            rating: tmdb.vote_average ?? null,
            voteCount: tmdb.vote_count ?? null,
            seasonNumber: tmdb.season_number,
            episodeNumber: tmdb.episode_number,
            isOwned: !!episodeContext!.episodeRatingKey,
            stillPath: posterUrl(tmdb.still_path) ?? undefined,
            cast,
          });
          setEpisodeLoading(false);
          return;
        }
      }

      if (plexBase) {
        if (!cancelled) {
          setEpisodeData(plexBase);
          setEpisodeLoading(false);
        }
        return;
      }

      // Neither source could be keyed by these coordinates — fall back to the
      // grid row, which already carries this episode's metadata.
      if (!cancelled) {
        setEpisodeData(gridEpisode ? {
          title: gridEpisode.name ?? '',
          overview: gridEpisode.overview ?? '',
          airDate: gridEpisode.air_date ?? null,
          runtime: gridEpisode.runtime ?? null,
          contentRating: null,
          rating: null,
          voteCount: null,
          seasonNumber: episodeContext!.seasonNumber,
          episodeNumber: episodeContext!.episodeNumber,
          isOwned: !!episodeContext!.episodeRatingKey,
          stillPath: posterUrl(gridEpisode.still_path ?? null) ?? undefined,
          cast: [],
        } : null);
        setEpisodeLoading(false);
      }
    }

    fetchEpisode();
    return () => { cancelled = true; };
  }, [
    episodeContext?.episodeRatingKey,
    episodeContext?.seasonNumber,
    episodeContext?.episodeNumber,
    tmdbId,
    gridEpisode?.name,
    gridEpisode?.air_date,
  ]);

  return { episodeData, episodeLoading };
}
