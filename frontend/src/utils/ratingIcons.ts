import type { Ratings } from '@/utils/modalParsers';
import { assetUrl } from '@/urlHelpers';

/** Cache-bust version — bump when icon files change on disk */
const V = '?v=3';

/** Static asset paths for rating service icons */
export const RATING_ICONS = {
  imdb: assetUrl(`/static/images/imdb-logo.png${V}`),
  rtCriticFresh: assetUrl(`/static/images/rotten-tomatoes-logo.png${V}`),
  rtCriticRotten: assetUrl(`/static/images/rotten-tomatoes-splat.png${V}`),
  rtAudienceGood: assetUrl(`/static/images/rotten-tomatoes-popcorn-good.png${V}`),
  rtAudienceBad: assetUrl(`/static/images/rotten-tomatoes-popcorn-bad.png${V}`),
  metacritic: assetUrl(`/static/images/metacritic.png${V}`),
  tmdb: assetUrl(`/static/images/tmdb-logo-short.svg${V}`),
} as const;

const decoded = new Map<string, { img: HTMLImageElement; ready: Promise<void>; done: boolean }>();

/** Decodes one rating icon once per session and keeps the decoded image alive, so every bar paints it from memory. */
export function decodeRatingIcon(url: string): Promise<void> {
  let e = decoded.get(url);
  if (!e) {
    const img = new Image(); img.src = url;
    const entry: { img: HTMLImageElement; ready: Promise<void>; done: boolean } = { img, ready: Promise.resolve(), done: false };
    entry.ready = (typeof img.decode === 'function' ? img.decode() : Promise.resolve()).catch(() => {}).then(() => { entry.done = true; });
    decoded.set(url, entry); e = entry;
  }
  return e.ready;
}

export function ratingIconDecoded(url: string): boolean {
  return !!decoded.get(url)?.done;
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
