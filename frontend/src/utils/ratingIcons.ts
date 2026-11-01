import type { Ratings } from '@/utils/modalParsers';

import imdbLogo from '@/assets/ratings/imdb-logo.png?no-inline';
import rtLogo from '@/assets/ratings/rotten-tomatoes-logo.png?no-inline';
import rtSplat from '@/assets/ratings/rotten-tomatoes-splat.png?no-inline';
import rtPopcornGood from '@/assets/ratings/rotten-tomatoes-popcorn-good.png?no-inline';
import rtPopcornBad from '@/assets/ratings/rotten-tomatoes-popcorn-bad.png?no-inline';
import metacriticLogo from '@/assets/ratings/metacritic.png?no-inline';
import tmdbLogo from '@/assets/ratings/tmdb-logo-short.svg?no-inline';
import { isDecoded, loadImage } from '@/loading/imageLoader';

/** Rating service icons, bundled with the app (content-hashed /assets URLs). */
export const RATING_ICONS = {
  imdb: imdbLogo,
  rtCriticFresh: rtLogo,
  rtCriticRotten: rtSplat,
  rtAudienceGood: rtPopcornGood,
  rtAudienceBad: rtPopcornBad,
  metacritic: metacriticLogo,
  tmdb: tmdbLogo,
} as const;

/** Decodes one rating icon through the image loader (once per session, kept in memory). */
export function decodeRatingIcon(url: string): Promise<void> {
  return loadImage(url);
}

export function ratingIconDecoded(url: string): boolean {
  return isDecoded(url);
}

/** Boot: decode every rating icon ahead of the first sheet. */
export function preloadRatingIcons(): Promise<void> {
  return Promise.all(Object.values(RATING_ICONS).map(decodeRatingIcon)).then(() => {});
}

/** Width / height of each icon file, so the bar can size every icon before it
 *  loads (no layout shift when the files arrive). */
export const RATING_ICON_ASPECT: Record<string, number> = {
  [RATING_ICONS.imdb]: 595 / 362,
  [RATING_ICONS.rtCriticFresh]: 500 / 510,
  [RATING_ICONS.rtCriticRotten]: 500 / 483,
  [RATING_ICONS.rtAudienceGood]: 500 / 659,
  [RATING_ICONS.rtAudienceBad]: 960 / 727,
  [RATING_ICONS.metacritic]: 1,
};

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
      label: 'RT critic',
      value: `${score}%`,
    });
  }
  if (ratings.rotten_tomatoes_audience?.score != null) {
    const score = Number(ratings.rotten_tomatoes_audience.score);
    items.push({
      icon: score >= RT_FRESH_THRESHOLD ? RATING_ICONS.rtAudienceGood : RATING_ICONS.rtAudienceBad,
      label: 'RT audience',
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
