/**
 * Vibe Engine — deterministic mood-based classification of library items.
 *
 * Computes "vibes" from: genres + runtime + decade + rating + content rating.
 * No ML, no external API calls — pure metadata heuristics.
 */

import type { VibeItem, VibeId, VibeDefinition, VibeShelf } from '@/types/vibe';
import { posterUrl, backdropUrl } from '@/utils/image';

// ---------------------------------------------------------------------------
// Vibe definitions (UI metadata)
// ---------------------------------------------------------------------------

export const VIBE_DEFINITIONS: VibeDefinition[] = [
  {
    id: 'cozy-comfort',
    name: 'Cozy Comfort',
    emoji: '\u{1F9F8}',
    description: 'Warm, feel-good stories',
    color: '#f59e0b',
  },
  {
    id: 'dark-moody',
    name: 'Dark & Moody',
    emoji: '\u{1F311}',
    description: 'Atmospheric and intense',
    color: '#6366f1',
  },
  {
    id: 'mind-bending',
    name: 'Mind-Bending',
    emoji: '\u{1F9E0}',
    description: 'Twist your reality',
    color: '#a855f7',
  },
  {
    id: 'big-adventure',
    name: 'Big Adventure',
    emoji: '\u{1F5FA}',
    description: 'Epic journeys and grand scale',
    color: '#22c55e',
  },
  {
    id: 'laugh-out-loud',
    name: 'Laugh Out Loud',
    emoji: '\u{1F602}',
    description: 'Pure comedy gold',
    color: '#f97316',
  },
  {
    id: 'classic-throwback',
    name: 'Classic Throwback',
    emoji: '\u{1F4FD}',
    description: 'Timeless cinema',
    color: '#eab308',
  },
  {
    id: 'edge-of-seat',
    name: 'Edge-of-Seat',
    emoji: '\u{1F630}',
    description: 'Heart-pounding tension',
    color: '#ef4444',
  },
  {
    id: 'easy-background',
    name: 'Easy Background',
    emoji: '\u{1FAE7}',
    description: 'Light and easy watching',
    color: '#06b6d4',
  },
];

// ---------------------------------------------------------------------------
// Scoring rules per vibe
// ---------------------------------------------------------------------------

interface VibeRule {
  matchGenres: string[];
  avoidGenres: string[];
  runtimeMin?: number;
  runtimeMax?: number;
  yearMax?: number;
  yearMin?: number;
  ratingMin?: number;
  familyFriendly?: boolean;
}

const VIBE_RULES: Record<VibeId, VibeRule> = {
  'cozy-comfort': {
    matchGenres: ['Comedy', 'Romance', 'Family', 'Animation', 'Music', 'Musical'],
    avoidGenres: ['Horror', 'War', 'Crime', 'Thriller'],
    runtimeMax: 150,
    ratingMin: 5.0,
  },
  'dark-moody': {
    matchGenres: ['Crime', 'Thriller', 'Film-Noir', 'Mystery', 'Horror', 'Drama'],
    avoidGenres: ['Comedy', 'Family', 'Animation', 'Music'],
    ratingMin: 5.5,
  },
  'mind-bending': {
    matchGenres: ['Science Fiction', 'Mystery', 'Thriller', 'Fantasy'],
    avoidGenres: ['Family', 'Animation', 'Music', 'Musical', 'Western'],
    ratingMin: 6.0,
  },
  'big-adventure': {
    matchGenres: ['Adventure', 'Action', 'Fantasy', 'Science Fiction', 'War'],
    avoidGenres: [],
    runtimeMin: 90,
  },
  'laugh-out-loud': {
    matchGenres: ['Comedy'],
    avoidGenres: ['Horror', 'Thriller', 'War', 'Crime'],
  },
  'classic-throwback': {
    matchGenres: [],
    avoidGenres: [],
    yearMax: 1999,
    ratingMin: 6.0,
  },
  'edge-of-seat': {
    matchGenres: ['Thriller', 'Action', 'Horror', 'War', 'Crime', 'Mystery'],
    avoidGenres: ['Comedy', 'Romance', 'Family', 'Animation'],
    ratingMin: 5.0,
  },
  'easy-background': {
    matchGenres: ['Comedy', 'Romance', 'Animation', 'Family', 'Documentary', 'Reality'],
    avoidGenres: ['Horror', 'Thriller', 'War'],
    runtimeMax: 120,
    familyFriendly: true,
  },
};

// ---------------------------------------------------------------------------
// Scoring function
// ---------------------------------------------------------------------------

function scoreItem(item: VibeItem, vibeId: VibeId): number {
  const rule = VIBE_RULES[vibeId];
  const genres = item.genres.map((g) => g.toLowerCase());
  let score = 0;

  // Genre matches (+3 each)
  for (const g of rule.matchGenres) {
    if (genres.includes(g.toLowerCase())) score += 3;
  }

  // Genre avoids (-5 each)
  for (const g of rule.avoidGenres) {
    if (genres.includes(g.toLowerCase())) score -= 5;
  }

  // Must have at least one genre match (except for classic-throwback which uses year)
  if (vibeId !== 'classic-throwback' && rule.matchGenres.length > 0) {
    const hasMatch = rule.matchGenres.some((g) => genres.includes(g.toLowerCase()));
    if (!hasMatch) return -100; // hard exclude
  }

  // Runtime bounds
  if (rule.runtimeMin && item.runtimeMinutes > 0 && item.runtimeMinutes < rule.runtimeMin) {
    score -= 3;
  }
  if (rule.runtimeMax && item.runtimeMinutes > rule.runtimeMax) {
    score -= 2;
  }
  if (rule.runtimeMin && item.runtimeMinutes >= rule.runtimeMin) {
    score += 1;
  }
  if (rule.runtimeMax && item.runtimeMinutes > 0 && item.runtimeMinutes <= rule.runtimeMax) {
    score += 1;
  }

  // Year bounds
  if (rule.yearMax && item.year && item.year <= rule.yearMax) {
    score += 3;
    // Extra bonus for truly old films
    if (item.year < 1980) score += 1;
  }
  if (rule.yearMax && item.year && item.year > rule.yearMax) {
    return -100; // hard exclude for classic-throwback
  }

  // Rating threshold
  const bestRating = item.audienceRating ?? item.rating ?? 0;
  if (rule.ratingMin) {
    if (bestRating >= rule.ratingMin) {
      score += 1;
    } else if (bestRating > 0 && bestRating < rule.ratingMin) {
      score -= 2;
    }
  }

  // Bonus for high-rated items
  if (bestRating >= 8.0) score += 1;

  // Family-friendly heuristic
  if (rule.familyFriendly) {
    const cr = (item.contentRating || '').toUpperCase();
    if (['G', 'PG', 'TV-G', 'TV-PG', 'TV-Y', 'TV-Y7'].includes(cr)) {
      score += 2;
    }
    if (['R', 'NC-17', 'TV-MA'].includes(cr)) {
      score -= 3;
    }
  }

  return score;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/** Build all 8 vibe shelves from the full library. Returns ~20 items per vibe. */
export function getVibeShelves(library: VibeItem[], maxPerShelf = 25): VibeShelf[] {
  // Score every item against every vibe
  const vibeScores: Record<VibeId, { item: VibeItem; score: number }[]> = {} as any;

  for (const vibe of VIBE_DEFINITIONS) {
    vibeScores[vibe.id] = [];
  }

  for (const item of library) {
    // Skip items with no poster (poor visual)
    if (!item.posterUrl) continue;

    for (const vibe of VIBE_DEFINITIONS) {
      const s = scoreItem(item, vibe.id);
      if (s > 0) {
        vibeScores[vibe.id].push({ item, score: s });
      }
    }
  }

  // Sort each vibe by score desc, take top N
  return VIBE_DEFINITIONS.map((vibe) => {
    const scored = vibeScores[vibe.id]
      .sort((a, b) => b.score - a.score || a.item.title.localeCompare(b.item.title))
      .slice(0, maxPerShelf)
      .map((s) => s.item);

    return { vibe, items: scored };
  }).filter((shelf) => shelf.items.length > 0);
}

/** Genre aliases for matching (chip label → Plex genre strings). */
const GENRE_ALIASES: Record<string, string[]> = {
  'Sci-Fi': ['science fiction', 'sci-fi'],
};

/** Fisher-Yates shuffle (in-place). */
function shuffle<T>(arr: T[]): T[] {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j]!, arr[i]!];
  }
  return arr;
}

/** Pick random items for the "Pick For Me" roulette.  */
export function pickRandom(
  library: VibeItem[],
  count: number,
  filters: {
    type?: 'movie' | 'tv' | 'both';
    unwatchedBoost?: boolean;
    maxRuntime?: number;
    era?: 'any' | 'newer' | 'older';
    genres?: string[];
    exclude?: Set<number>;
    newerThanYears?: number;
    unwatchedBoostWeight?: number;
  } = {},
): VibeItem[] {
  let pool = library.filter((i) => i.posterUrl);

  // Exclude previously shown items
  if (filters.exclude && filters.exclude.size > 0) {
    pool = pool.filter((i) => !filters.exclude!.has(i.ratingKey));
  }

  // Type filter
  if (filters.type && filters.type !== 'both') {
    pool = pool.filter((i) => i.type === filters.type);
  }

  // Runtime filter (e.g. Tonight ≤100m)
  if (filters.maxRuntime != null) {
    const max = filters.maxRuntime;
    pool = pool.filter((i) => i.runtimeMinutes > 0 && i.runtimeMinutes <= max);
  }

  // Era filter
  if (filters.era && filters.era !== 'any') {
    const now = new Date().getFullYear();
    const newerYears = filters.newerThanYears ?? 1;
    if (filters.era === 'newer') {
      pool = pool.filter((i) => i.year != null && i.year >= now - newerYears);
    } else {
      pool = pool.filter((i) => i.year != null && i.year < now - newerYears);
    }
  }

  // Genre filter — item must match ALL selected genres
  if (filters.genres && filters.genres.length > 0) {
    pool = pool.filter((i) => {
      const itemGenres = i.genres.map((g) => g.toLowerCase());
      return filters.genres!.every((selected) => {
        const aliases = GENRE_ALIASES[selected];
        const matchStrings = aliases
          ? [...aliases, selected.toLowerCase()]
          : [selected.toLowerCase()];
        return matchStrings.some((m) => itemGenres.includes(m));
      });
    });
  }

  // Deduplicate pool — same movie can appear from multiple Plex libraries
  const seen = new Set<string>();
  pool = pool.filter((i) => {
    const key = i.tmdbId ? `tmdb:${i.tmdbId}` : `${i.title}:${i.year}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  if (pool.length === 0) return [];
  if (pool.length <= count) return shuffle([...pool]);

  // Weighted random selection — unwatched items get a boost
  const boostWeight = filters.unwatchedBoostWeight ?? 3;
  const available = pool.map((item) => {
    let weight = 1;
    if (filters.unwatchedBoost && item.viewCount === 0) weight += boostWeight;
    return { item, weight };
  });

  const picked: VibeItem[] = [];
  for (let i = 0; i < count && available.length > 0; i++) {
    const currentTotal = available.reduce((sum, w) => sum + w.weight, 0);
    let r = Math.random() * currentTotal;
    for (let j = 0; j < available.length; j++) {
      r -= available[j]!.weight;
      if (r <= 0) {
        picked.push(available[j]!.item);
        available.splice(j, 1);
        break;
      }
    }
  }

  return shuffle(picked);
}

// ---------------------------------------------------------------------------
// TMDb genre ID → name map (matches filter chip labels)
// ---------------------------------------------------------------------------

const TMDB_GENRE_MAP: Record<number, string> = {
  28: 'Action',
  12: 'Adventure',
  16: 'Animation',
  35: 'Comedy',
  80: 'Crime',
  99: 'Documentary',
  18: 'Drama',
  10751: 'Family',
  14: 'Fantasy',
  36: 'History',
  27: 'Horror',
  10402: 'Music',
  9648: 'Mystery',
  10749: 'Romance',
  878: 'Science Fiction',
  10770: 'TV Movie',
  53: 'Thriller',
  10752: 'War',
  37: 'Western',
  // TV-specific
  10759: 'Action',      // Action & Adventure
  10765: 'Science Fiction', // Sci-Fi & Fantasy
  10768: 'War',         // War & Politics
};

/** Convert a raw TMDb list item to a VibeItem for Pick For Me. */
export function tmdbToVibeItem(
  raw: Record<string, unknown>,
  mediaType: 'movie' | 'tv',
  index: number,
): VibeItem {
  const title = (raw.title as string) ?? (raw.name as string) ?? '';
  const dateStr = (raw.release_date as string) ?? (raw.first_air_date as string) ?? '';
  const year = dateStr ? parseInt(dateStr.substring(0, 4), 10) || null : null;
  const posterPath = raw.poster_path as string | null;
  const backdropPath = raw.backdrop_path as string | null;
  const genreIds = (raw.genre_ids as number[]) ?? [];
  const genres = genreIds
    .map((id) => TMDB_GENRE_MAP[id])
    .filter((g): g is string => !!g);
  // Deduplicate genre names (e.g. TV 10759 and movie 28 both map to "Action")
  const uniqueGenres = [...new Set(genres)];

  return {
    ratingKey: -(index + 1), // negative sentinel — never collides with Plex keys
    title,
    year,
    type: mediaType,
    runtimeMinutes: 0,
    genres: uniqueGenres,
    summary: (raw.overview as string) ?? '',
    contentRating: '',
    rating: (raw.vote_average as number) ?? null,
    audienceRating: null,
    posterUrl: posterUrl(posterPath, 'w342'),
    backdropUrl: backdropUrl(backdropPath),
    tmdbId: (raw.id as number) ?? null,
    imdbId: null,
    viewCount: 0,
    lastViewedAt: 0,
    addedAt: 0,
    libraryName: '',
    voteCount: (raw.vote_count as number) ?? 0,
    rtCriticScore: null,
    rtAudienceScore: null,
  };
}


/** Pick random unowned items — all-premium strategy favouring high vote counts. */
export function pickRandomUnowned(
  pool: VibeItem[],
  count: number,
  filters: {
    type?: 'movie' | 'tv' | 'both';
    era?: 'any' | 'newer' | 'older';
    genres?: string[];
    exclude?: Set<number>;
    newerThanYears?: number;
  } = {},
): VibeItem[] {
  let candidates = pool.filter((i) => i.posterUrl);

  // Exclude previously shown
  if (filters.exclude && filters.exclude.size > 0) {
    candidates = candidates.filter((i) => !filters.exclude!.has(i.ratingKey));
  }

  // Type filter
  if (filters.type && filters.type !== 'both') {
    candidates = candidates.filter((i) => i.type === filters.type);
  }

  // Era filter
  if (filters.era && filters.era !== 'any') {
    const now = new Date().getFullYear();
    const newerYears = filters.newerThanYears ?? 1;
    if (filters.era === 'newer') {
      candidates = candidates.filter((i) => i.year != null && i.year >= now - newerYears);
    } else {
      candidates = candidates.filter((i) => i.year != null && i.year < now - newerYears);
    }
  }

  // Genre filter — item must match ALL selected genres
  if (filters.genres && filters.genres.length > 0) {
    candidates = candidates.filter((i) => {
      const itemGenres = i.genres.map((g) => g.toLowerCase());
      return filters.genres!.every((selected) => {
        const aliases = GENRE_ALIASES[selected];
        const matchStrings = aliases
          ? [...aliases, selected.toLowerCase()]
          : [selected.toLowerCase()];
        return matchStrings.some((m) => itemGenres.includes(m));
      });
    });
  }

  // Deduplicate by tmdbId
  const seen = new Set<number>();
  candidates = candidates.filter((i) => {
    if (!i.tmdbId) return true;
    if (seen.has(i.tmdbId)) return false;
    seen.add(i.tmdbId);
    return true;
  });

  if (candidates.length === 0) return [];
  if (candidates.length <= count) return shuffle([...candidates]);

  return shuffle([...candidates]).slice(0, count);
}
