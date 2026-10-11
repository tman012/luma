import type { Ratings } from '@/utils/modalParsers';

/** Cache-bust version — bump when icon files change on disk */
const V = '?v=3';

/** Static asset paths for rating service icons */
export const RATING_ICONS = {
  imdb: `/static/images/imdb-logo.png${V}`,
  rtCriticFresh: `/static/images/rotten-tomatoes-logo.png${V}`,
  rtCriticRotten: `/static/images/rotten-tomatoes-splat.png${V}`,
  rtAudienceGood: `/static/images/rotten-tomatoes-popcorn-good.png${V}`,
  rtAudienceBad: `/static/images/rotten-tomatoes-popcorn-bad.png${V}`,
  metacritic: `/static/images/metacritic.png${V}`,
  tmdb: `/static/images/tmdb-logo-short.svg${V}`,
} as const;

/** Freshness threshold (>= means fresh/good, < means rotten/bad) */
export const RT_FRESH_THRESHOLD = 60;

interface RatingItem {
  icon: string;
  label: string;
  value: string;
  /** Scale factor applied to the standard icon height (default 1) */
  heightScale?: number;
}

/** Build display items from a Ratings object */
export function buildRatingItems(ratings: Ratings): RatingItem[] {
  const items: RatingItem[] = [];

  if (ratings.imdb?.score != null) {
    items.push({ icon: RATING_ICONS.imdb, label: 'IMDb', value: String(ratings.imdb.score) });
  }
  if (ratings.rotten_tomatoes_critic?.score != null) {
    const score = Number(ratings.rotten_tomatoes_critic.score);
    items.push({
      icon: score >= RT_FRESH_THRESHOLD ? RATING_ICONS.rtCriticFresh : RATING_ICONS.rtCriticRotten,
      label: 'RT Critic',
      value: `${score}%`,
    });
  }
  if (ratings.rotten_tomatoes_audience?.score != null) {
    const score = Number(ratings.rotten_tomatoes_audience.score);
    items.push({
      icon: score >= RT_FRESH_THRESHOLD ? RATING_ICONS.rtAudienceGood : RATING_ICONS.rtAudienceBad,
      label: 'RT Audience',
      value: `${score}%`,
    });
  }
  if (ratings.metacritic?.score != null) {
    items.push({ icon: RATING_ICONS.metacritic, label: 'Metacritic', value: String(ratings.metacritic.score) });
  }
  // Intentionally omit TMDb vote_average — user-facing scores are IMDb-only.
  return items;
}

/** Resolve RT critic icon based on score */
export function rtCriticIcon(score: number): string {
  return score >= RT_FRESH_THRESHOLD ? RATING_ICONS.rtCriticFresh : RATING_ICONS.rtCriticRotten;
}

/** Resolve RT audience icon based on score */
export function rtAudienceIcon(score: number): string {
  return score >= RT_FRESH_THRESHOLD ? RATING_ICONS.rtAudienceGood : RATING_ICONS.rtAudienceBad;
}
