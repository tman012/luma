// ---------------------------------------------------------------------------
// Shared types, parsers, and constants for media detail modals.
// Extracted from KioskMovieDetailModal and KioskTvDetailModal so the same
// data-fetching logic can be reused by Web and Mobile modals.
// ---------------------------------------------------------------------------

import { posterUrl as tmdbPoster, backdropUrl as tmdbBackdrop, plexBackdrop, logoImg, castThumb } from '@/utils/image';
import { apiUrl } from '@/urlHelpers';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface CastMember {
  name: string;
  character?: string;
  role?: string;
  thumb?: string | null;
  profilePath?: string | null;
  tmdbPersonId?: number;
}

export interface Director {
  name: string;
  thumb?: string | null;
  profilePath?: string | null;
  tmdbPersonId?: number;
}

export interface MediaDetail {
  title: string;
  year: number | null;
  summary: string;
  contentRating: string | null;
  rating: number | null;
  durationMs: number;
  backdropUrl: string | null;
  plexBackdropUrl?: string | null;
  posterUrl: string | null;
  clearLogoUrl: string | null;
  /** True once the logo lookup has finished (a null clearLogoUrl then means
   *  'no logo', not 'still loading'). */
  logoSettled?: boolean;
  genres: string[];
  cast: CastMember[];
  directors: Director[];
  libraryName?: string;
  ratingKey?: number;
  tmdbId?: number;
  isOwned: boolean;
  libraries?: string[];
  libraryKeys?: Record<string, number>;
  /** TMDb franchise collection id when belongs_to_collection is set. */
  collectionId?: number | null;
  /** TMDb franchise collection name (e.g. "Twilight Collection"). */
  collectionName?: string | null;
}

export interface TvShowDetail {
  title: string;
  year: number | null;
  summary: string;
  contentRating: string | null;
  rating: number | null;
  backdropUrl: string | null;
  /** TMDb's text-free backdrop when Plex art came first and the title has a logo: the sheet's pick if its backdrop
   * hasn't shown yet (useSheetBackdrop). Never swapped over art already on screen. */
  backdropSwapUrl?: string | null;
  /** True while a better backdrop (TMDb) may still arrive: the sheet waits for it, up to its cap. */
  backdropPending?: boolean;
  posterUrl: string | null;
  clearLogoUrl: string | null;
  /** True once the logo lookup has finished (a null clearLogoUrl then means
   *  'no logo', not 'still loading'). */
  logoSettled?: boolean;
  genres: string[];
  cast: CastMember[];
  creators: Director[];
  network: string | null;
  numberOfSeasons: number;
  tmdbId?: number;
  tvdbId?: number;
  ratingKey?: number;
  isOwned: boolean;
  /** TMDb production status, e.g. "Returning Series", "Ended", "Canceled". */
  status?: string | null;
  /** TMDb flag — true while the show is actively in production. */
  inProduction?: boolean;
  /** Total ordered episodes per TMDb (includes unaired). */
  numberOfEpisodes?: number;
  /** Next episode TMDb expects to air, if the show is ongoing. */
  nextEpisodeToAir?: {
    airDate: string | null;
    seasonNumber: number | null;
    episodeNumber: number | null;
  } | null;
}

export interface UltraBlurColors {
  topLeft: string | null;
  topRight: string | null;
  bottomRight: string | null;
  bottomLeft: string | null;
  /** When set to 'palette', the `blobs` array is used directly instead of corner mapping. */
  format?: 'palette';
  /** Pre-computed blob positions and colors (used when format === 'palette'). */
  blobs?: Array<{ color: string; x: number; y: number; weight: number }>;
}

export interface Ratings {
  imdb: { score: string | number | null } | null;
  rotten_tomatoes_critic: { score: string | number | null } | null;
  rotten_tomatoes_audience: { score: string | number | null } | null;
  metacritic: { score: string | number | null } | null;
  tmdb: { score: string | number | null } | null;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const CARD_BG = 'var(--color-kiosk-black)';

/** Person silhouette SVG fallback for cast/crew with no photo */
export const PERSON_FALLBACK_SVG = `data:image/svg+xml,${encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 110 110" fill="none">
    <rect width="110" height="110" rx="55" fill="#2a2a2a"/>
    <circle cx="55" cy="40" r="18" fill="#555"/>
    <ellipse cx="55" cy="90" rx="28" ry="22" fill="#555"/>
  </svg>`,
)}`;

// ---------------------------------------------------------------------------
// Cast / crew dedup — merges directors/creators who also appear as cast
// ---------------------------------------------------------------------------

/**
 * Merges crew (directors/creators) with cast, deduplicating by name.
 * If a person appears in both lists, they get a single entry with a combined
 * subtitle like "Director / Tony Stark" or "Creator / Jon Snow".
 *
 * @param crew Array of Director entries (creators or directors)
 * @param cast Array of CastMember entries
 * @param crewLabel Label for crew role (e.g. "Director" or "Creator")
 * @returns Deduplicated CastMember[] with crew-first ordering
 */
export function deduplicateCastAndCrew(
  crew: Director[],
  cast: CastMember[],
  crewLabel: string = 'Director',
): CastMember[] {
  const crewByName = new Map<string, Director>();
  for (const c of crew) {
    const key = c.name.toLowerCase().trim();
    if (!crewByName.has(key)) crewByName.set(key, c);
  }

  // Track which crew members were found in cast
  const mergedCrewNames = new Set<string>();

  // Process cast — merge if they're also crew
  const dedupedCast: CastMember[] = cast.map((member) => {
    const key = member.name.toLowerCase().trim();
    const crewMatch = crewByName.get(key);
    if (crewMatch) {
      mergedCrewNames.add(key);
      const character = member.character || member.role || '';
      return {
        ...member,
        character: character ? `${crewLabel} / ${character}` : crewLabel,
        role: character ? `${crewLabel} / ${character}` : crewLabel,
        // Prefer crew's image if cast has none
        thumb: member.thumb || crewMatch.thumb,
        profilePath: member.profilePath || crewMatch.profilePath,
        tmdbPersonId: member.tmdbPersonId || crewMatch.tmdbPersonId,
      };
    }
    return member;
  });

  // Add crew members who were NOT in cast (pure crew) at the front
  const pureCrew: CastMember[] = crew
    .filter((c) => !mergedCrewNames.has(c.name.toLowerCase().trim()))
    .map((c) => ({
      name: c.name,
      character: crewLabel,
      role: crewLabel,
      thumb: c.thumb ?? undefined,
      profilePath: c.profilePath ?? undefined,
      tmdbPersonId: c.tmdbPersonId,
    }));

  return [...pureCrew, ...dedupedCast];
}

// ---------------------------------------------------------------------------
// Movie parsers
// ---------------------------------------------------------------------------

export function parsePlexResponse(data: Record<string, unknown>): MediaDetail {
  const rawCast = (data.cast ?? []) as Record<string, unknown>[];
  const cast: CastMember[] = rawCast.map((c) => {
    let thumb: string | null = null;
    if (c.thumb && typeof c.thumb === 'string' && c.thumb.startsWith('http')) {
      thumb = c.thumb;
    }
    return {
      name: (c.name as string) ?? '',
      character: (c.character as string) ?? (c.role as string) ?? '',
      thumb,
    };
  });

  const rawDirectors = (data.directors ?? []) as Record<string, unknown>[];
  const directors: Director[] = rawDirectors.map((d) => {
    let thumb: string | null = null;
    if (d.thumb && typeof d.thumb === 'string' && d.thumb.startsWith('http')) {
      thumb = d.thumb;
    }
    return {
      name: (d.name as string) ?? '',
      thumb,
    };
  });

  const rawGenres = (data.genres ?? []) as (string | Record<string, unknown>)[];
  const genres: string[] = rawGenres.map((g) =>
    typeof g === 'string' ? g : ((g.tag as string) ?? ''),
  );

  return {
    title: (data.title as string) ?? '',
    year: (data.year as number) ?? null,
    summary: (data.summary as string) ?? '',
    contentRating: (data.content_rating as string) ?? null,
    rating: (data.rating as number) ?? null,
    durationMs: (data.duration as number) ?? 0,
    // Prefer TMDb backdrop (set by useMovieDetail enrichment) over Plex proxy
    // When tmdb_id exists, skip Plex backdrop — TMDb original is higher quality
    backdropUrl: data.tmdb_id ? null : plexBackdrop((data.backdrop_url as string) ?? null),
    plexBackdropUrl: (data.backdrop_url as string) ?? null,
    posterUrl: (data.poster_url as string) ?? null,
    clearLogoUrl: logoImg((data.clearlogo_url as string) ?? null),
    genres,
    cast,
    directors,
    libraryName: (data.library_name as string) ?? undefined,
    ratingKey: (data.rating_key as number) ?? undefined,
    tmdbId: data.tmdb_id ? Number(data.tmdb_id) : undefined,
    isOwned: true,
    libraries: (data.libraries as string[]) ?? undefined,
    libraryKeys: (data.library_keys as Record<string, number>) ?? undefined,
  };
}


/** Pull TMDb belongs_to_collection id/name from a raw movie payload. */
export function extractBelongsToCollection(
  data: Record<string, unknown> | null | undefined,
): { collectionId: number | null; collectionName: string | null } {
  const coll = data?.belongs_to_collection;
  if (!coll || typeof coll !== 'object') {
    return { collectionId: null, collectionName: null };
  }
  const raw = coll as Record<string, unknown>;
  const id = raw.id;
  const collectionId =
    typeof id === 'number' ? id : typeof id === 'string' && /^\d+$/.test(id) ? parseInt(id, 10) : null;
  const collectionName = typeof raw.name === 'string' ? raw.name : null;
  return { collectionId, collectionName };
}

export function parseTmdbResponse(data: Record<string, unknown>): MediaDetail {
  const credits = (data.credits ?? {}) as Record<string, unknown>;

  const rawCast = (credits.cast ?? []) as Record<string, unknown>[];
  const cast: CastMember[] = rawCast.slice(0, 30).map((c) => ({
    name: (c.name as string) ?? '',
    character: (c.character as string) ?? '',
    profilePath: (c.profile_path as string) ?? null,
    tmdbPersonId: (c.id as number) ?? undefined,
  }));

  const rawCrew = (credits.crew ?? []) as Record<string, unknown>[];
  const directors: Director[] = rawCrew
    .filter((c) => c.job === 'Director')
    .map((d) => ({
      name: (d.name as string) ?? '',
      profilePath: (d.profile_path as string) ?? null,
      tmdbPersonId: (d.id as number) ?? undefined,
    }));

  const rawGenres = (data.genres ?? []) as Record<string, unknown>[];
  const genres: string[] = rawGenres.map((g) => (g.name as string) ?? '');

  const releaseDate = data.release_date as string | undefined;
  const year = releaseDate
    ? parseInt(releaseDate.substring(0, 4), 10) || null
    : null;

  // TMDb runtime is in minutes; convert to ms for formatRuntime
  const runtime = data.runtime as number | undefined;
  const durationMs = runtime ? runtime * 60_000 : 0;

  // Extract US MPAA rating from release_dates
  let contentRating: string | null = null;
  const releaseDates = (data.release_dates ?? {}) as Record<string, unknown>;
  const rdResults = (releaseDates.results ?? []) as Record<string, unknown>[];
  const usEntry = rdResults.find((r) => r.iso_3166_1 === 'US');
  if (usEntry) {
    const dates = (usEntry.release_dates ?? []) as Record<string, unknown>[];
    const withCert = dates.find((d) => !!(d.certification as string));
    if (withCert) contentRating = (withCert.certification as string) ?? null;
  }

  return {
    title: (data.title as string) ?? (data.name as string) ?? '',
    year,
    summary: (data.overview as string) ?? '',
    contentRating,
    rating: (data.vote_average as number) ?? null,
    durationMs,
    backdropUrl: tmdbBackdrop(data.backdrop_path as string | null),
    posterUrl: tmdbPoster(data.poster_path as string | null),
    clearLogoUrl: null,
    genres,
    cast,
    directors,
    tmdbId: (data.id as number) ?? undefined,
    isOwned: false,
    ...(() => {
      const { collectionId, collectionName } = extractBelongsToCollection(data);
      return { collectionId, collectionName };
    })(),
  };
}

// ---------------------------------------------------------------------------
// TV parsers
// ---------------------------------------------------------------------------

export function parseTmdbTvResponse(data: Record<string, unknown>): TvShowDetail {
  const credits = (data.credits ?? {}) as Record<string, unknown>;

  const rawCast = (credits.cast ?? []) as Record<string, unknown>[];
  const cast: CastMember[] = rawCast.slice(0, 30).map((c) => ({
    name: (c.name as string) ?? '',
    character: (c.character as string) ?? '',
    profilePath: (c.profile_path as string) ?? null,
    tmdbPersonId: (c.id as number) ?? undefined,
  }));

  const rawCrew = (credits.crew ?? []) as Record<string, unknown>[];
  const createdBy = (data.created_by ?? []) as Record<string, unknown>[];
  const creators: Director[] = createdBy
    .map((d) => ({
      name: (d.name as string) ?? '',
      profilePath: (d.profile_path as string) ?? null,
      tmdbPersonId: (d.id as number) ?? undefined,
    }));

  // Fall back to crew directors if no created_by
  if (creators.length === 0) {
    const dirs = rawCrew
      .filter((c) => c.job === 'Director' || c.department === 'Directing')
      .slice(0, 3)
      .map((d) => ({
        name: (d.name as string) ?? '',
        profilePath: (d.profile_path as string) ?? null,
        tmdbPersonId: (d.id as number) ?? undefined,
      }));
    creators.push(...dirs);
  }

  const rawGenres = (data.genres ?? []) as Record<string, unknown>[];
  const genres: string[] = rawGenres.map((g) => (g.name as string) ?? '');

  const firstAirDate = data.first_air_date as string | undefined;
  const year = firstAirDate
    ? parseInt(firstAirDate.substring(0, 4), 10) || null
    : null;

  const networks = (data.networks ?? []) as Record<string, unknown>[];
  const network = networks.length > 0 && networks[0] ? (networks[0].name as string) ?? null : null;

  // Content rating from US certification
  let contentRating: string | null = null;
  const contentRatings = (data.content_ratings ?? {}) as Record<string, unknown>;
  const crResults = (contentRatings.results ?? []) as Record<string, unknown>[];
  const usRating = crResults.find((r) => r.iso_3166_1 === 'US');
  if (usRating) {
    contentRating = (usRating.rating as string) ?? null;
  }

  // Extract tvdbId from external_ids (unblocks ownership + Sonarr fetches early)
  const externalIds = (data.external_ids ?? {}) as Record<string, unknown>;
  const tvdbId = typeof externalIds.tvdb_id === 'number' ? externalIds.tvdb_id : undefined;

  // Ongoing-status fields (drive the in-progress Sources prompt)
  const nextEp = data.next_episode_to_air as Record<string, unknown> | null | undefined;
  const nextEpisodeToAir = nextEp
    ? {
        airDate: (nextEp.air_date as string) ?? null,
        seasonNumber: (nextEp.season_number as number) ?? null,
        episodeNumber: (nextEp.episode_number as number) ?? null,
      }
    : null;

  return {
    title: (data.name as string) ?? (data.title as string) ?? '',
    year,
    summary: (data.overview as string) ?? '',
    contentRating,
    rating: (data.vote_average as number) ?? null,
    backdropUrl: tmdbBackdrop(data.backdrop_path as string | null),
    posterUrl: tmdbPoster(data.poster_path as string | null),
    clearLogoUrl: null,
    genres,
    cast,
    creators,
    network,
    numberOfSeasons: (data.number_of_seasons as number) ?? 0,
    tmdbId: (data.id as number) ?? undefined,
    tvdbId,
    isOwned: false,
    status: (data.status as string) ?? null,
    inProduction: (data.in_production as boolean) ?? false,
    numberOfEpisodes: (data.number_of_episodes as number) ?? undefined,
    nextEpisodeToAir,
  };
}

export function parsePlexTvResponse(data: Record<string, unknown>): TvShowDetail {
  const rawCast = (data.cast ?? []) as Record<string, unknown>[];
  const cast: CastMember[] = rawCast.map((c) => {
    let thumb: string | null = null;
    if (c.thumb && typeof c.thumb === 'string' && c.thumb.startsWith('http')) {
      thumb = c.thumb;
    }
    return {
      name: (c.tag as string) ?? (c.name as string) ?? '',
      character: (c.role as string) ?? (c.character as string) ?? '',
      thumb,
    };
  });

  const rawDirectors = (data.directors ?? []) as Record<string, unknown>[];
  const creators: Director[] = rawDirectors.map((d) => {
    let thumb: string | null = null;
    if (d.thumb && typeof d.thumb === 'string' && d.thumb.startsWith('http')) {
      thumb = d.thumb;
    }
    return { name: (d.name as string) ?? '', thumb };
  });

  const rawGenres = (data.genres ?? []) as (string | Record<string, unknown>)[];
  const genres: string[] = rawGenres.map((g) =>
    typeof g === 'string' ? g : ((g.tag as string) ?? ''),
  );

  return {
    title: (data.title as string) ?? '',
    year: (data.year as number) ?? null,
    summary: (data.summary as string) ?? '',
    contentRating: (data.content_rating as string) ?? (data.contentRating as string) ?? null,
    rating: (data.rating as number) ?? null,
    backdropUrl: plexBackdrop((data.artPath as string)
      ? apiUrl(`/api/img/plex?url=${data.artPath as string}`)
      : (data.backdrop_url as string) ?? null),
    posterUrl: (data.thumbPath as string)
      ? apiUrl(`/api/img/plex?url=${data.thumbPath as string}`)
      : (data.poster_url as string) ?? null,
    clearLogoUrl: (data.clearLogoPath as string)
      ? logoImg(apiUrl(`/api/img/plex?url=${data.clearLogoPath as string}`))
      : null,
    genres,
    cast,
    creators,
    network: null,
    numberOfSeasons: 0,
    ratingKey: (data.rating_key as number) ?? (data.ratingKey as number) ?? undefined,
    tmdbId: (data.tmdb_id as number) ?? undefined,
    tvdbId: (data.tvdb_id as number) ?? undefined,
    isOwned: true,
  };
}

// ---------------------------------------------------------------------------
// Utility functions
// ---------------------------------------------------------------------------

export function getCastImageUrl(member: CastMember | Director): string | null {
  if ('thumb' in member && member.thumb) return castThumb(member.thumb);
  if ('profilePath' in member && member.profilePath)
    return tmdbPoster(member.profilePath, 'w185');
  return null;
}
