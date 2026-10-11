/**
 * releaseRecommend — ranking helpers for Sources UI "Recommended" picks.
 *
 * Product UX (resolution → one Recommended per source type → See all):
 *   - pickBestInGroup / recommendationsForResolution work for EVERY resolution
 *     including 4K/2160p. Tester no-4K rules apply only to bot grabs, not UX.
 *
 * standard eligibility (matchesStandard) is still used as a ranking preference for
 * non-4K WEB-DL / WEBRip / BLURAY (prefer IPT, prefer x264 over x265). For 4K
 * we still pick a best recommendation — we never refuse to recommend 4K.
 *
 * Remux stays its own source type via classifySourceType; it gets its own
 * Recommended row when present. UX type buckets are REMUX / BLURAY / WEB-DL /
 * Other (WEBRip and junk fold into Other).
 */

import { orderSourceTypes } from '@/components/media/sourcesShared';

/** Minimal release shape both movie + TV ProcessedRelease satisfy. */
export interface ReleaseLike {
  resolution: string;
  sourceType: string;
  title: string;
  quality?: string;
  indexer: string;
  seeders: number;
  guid?: string;
  downloadUrl?: string;
  infoHash?: string;
}

const STANDARD_RESOLUTIONS = new Set(['720p', '1080p']);
/** Frontend classifySourceType labels that map to Radarr standard sources. */
const STANDARD_SOURCE_TYPES = new Set(['WEB-DL', 'BLURAY']);
/** Title/quality tokens that still count as standard sources when bucketing folds WEBRip into Other. */
const STANDARD_SOURCE_BLOB_RE = /\b(?:WEB[ -]?DL|WEBDL|WEB[ -]?RIP|WEBRIP|BLU[ -]?RAY|BLURAY)\b/i;
const X265_RE = /x\.?\s?265|h\.?\s?265|hevc/i;
const IPT_RE = /(^|[^A-Z0-9])(IPT|IPTORRENTS)($|[^A-Z0-9])/i;

/** Stable identity — same fallback chain as SourcesPanel releaseKey. */
export function releaseKey(r: ReleaseLike): string {
  return r.guid || r.downloadUrl || r.infoHash || r.title;
}

export function isIptIndexer(indexer: string): boolean {
  return IPT_RE.test((indexer || '').toUpperCase());
}

export function is4KResolution(resolution: string): boolean {
  const r = (resolution || '').toLowerCase();
  return r === '2160p' || r === '4k' || r === 'uhd' || r.includes('2160');
}

/** UI label for a resolution bucket — show "4K" for 2160p. */
export function displayResolution(resolution: string): string {
  if (is4KResolution(resolution)) return '4K';
  return resolution || 'Unknown';
}

function releaseBlob(r: ReleaseLike): string {
  return `${r.quality || ''} ${r.title || ''}`;
}

export function isX265Release(r: ReleaseLike): boolean {
  return X265_RE.test(releaseBlob(r));
}

/**
 * Eligibility mirror of `_matches_standard` (bot / profile rules):
 * - 720p / 1080p only (not 4K/2160p)
 * - WEB-DL / WEBRip / Bluray only (not remux, not cam/hdtv/bdrip, …)
 * - NOT x265 / HEVC / H.265 in title or quality string
 *
 * Used as a ranking preference for non-4K picks — NOT as a gate that hides 4K
 * from the product UI.
 */
export function matchesStandard(r: ReleaseLike): boolean {
  const resolution = (r.resolution || '').toLowerCase();
  if (!STANDARD_RESOLUTIONS.has(resolution)) return false;

  const sourceType = r.sourceType || '';
  if (sourceType === 'REMUX') return false;
  const blob = releaseBlob(r);
  if (/\bREMUX\b/i.test(blob)) return false;

  // Prefer classified WEB-DL/BLURAY; also accept WEBRip (now in Other) via title blob.
  if (!STANDARD_SOURCE_TYPES.has(sourceType) && !STANDARD_SOURCE_BLOB_RE.test(blob)) return false;

  if (X265_RE.test(blob)) return false;

  return true;
}

function resolutionRank(resolution: string): number {
  const r = (resolution || '').toLowerCase();
  if (r === '2160p' || r === '4k') return 3;
  if (r === '1080p') return 2;
  if (r === '720p') return 1;
  return 0;
}

/**
 * Compare two releases for a single resolution+type bucket.
 * Prefer IPT → (non-4K: standard-eligible / non-x265) → seeders.
 * Higher sort priority = comes first when compare returns negative for (a,b)
 * meaning a should rank above b (same convention as compareStandardRank).
 */
export function compareBucketRank(a: ReleaseLike, b: ReleaseLike): number {
  const ipt = Number(isIptIndexer(b.indexer)) - Number(isIptIndexer(a.indexer));
  if (ipt !== 0) return ipt;

  // Non-4K: prefer standard-safe / x264 over x265 when choosing among equals.
  // 4K: still recommend — x265 is common at 4K, so don't penalize it.
  if (!is4KResolution(a.resolution) && !is4KResolution(b.resolution)) {
    const std = Number(matchesStandard(b)) - Number(matchesStandard(a));
    if (std !== 0) return std;
    const x265 = Number(isX265Release(a)) - Number(isX265Release(b));
    if (x265 !== 0) return x265;
  }

  return (b.seeders || 0) - (a.seeders || 0);
}

/** Compare across mixed resolutions (legacy flat-list helper). IPT > res > seeders. */
export function compareStandardRank(a: ReleaseLike, b: ReleaseLike): number {
  const ipt = Number(isIptIndexer(b.indexer)) - Number(isIptIndexer(a.indexer));
  if (ipt !== 0) return ipt;
  const res = resolutionRank(b.resolution) - resolutionRank(a.resolution);
  if (res !== 0) return res;
  return (b.seeders || 0) - (a.seeders || 0);
}

/** Best release inside an already-scoped group (same res + type). Always picks one if non-empty — including 4K. */
export function pickBestInGroup<T extends ReleaseLike>(releases: T[]): T | null {
  let best: T | null = null;
  for (const r of releases) {
    if (!best || compareBucketRank(r, best) < 0) best = r;
  }
  return best;
}

/**
 * Legacy: best standard-eligible release across a list, or null if none qualify.
 * Prefer recommendationsForResolution for the product UX.
 */
export function pickRecommendedStandard<T extends ReleaseLike>(releases: T[]): T | null {
  let best: T | null = null;
  for (const r of releases) {
    if (!matchesStandard(r)) continue;
    if (!best || compareStandardRank(r, best) < 0) best = r;
  }
  return best;
}

export interface TypeRecommendation<T extends ReleaseLike> {
  type: string;
  recommended: T;
  all: T[];
}

/**
 * For a selected resolution, one Recommended pick per source type present
 * (REMUX / BLURAY / WEB-DL / Other), ordered by SOURCE_TYPE_SEGMENT_ORDER.
 * Includes 4K buckets — never skips recommending because resolution is 2160p.
 */
export function recommendationsForResolution<T extends ReleaseLike>(
  releases: T[],
  resolution: string,
): TypeRecommendation<T>[] {
  const inRes = releases.filter((r) => r.resolution === resolution);
  const byType = new Map<string, T[]>();
  for (const r of inRes) {
    const list = byType.get(r.sourceType);
    if (list) list.push(r);
    else byType.set(r.sourceType, [r]);
  }
  const types = orderSourceTypes(byType.keys());
  const out: TypeRecommendation<T>[] = [];
  for (const type of types) {
    const all = [...(byType.get(type) || [])].sort(
      (a, b) => (b.seeders || 0) - (a.seeders || 0),
    );
    const recommended = pickBestInGroup(all);
    if (recommended) out.push({ type, recommended, all });
  }
  return out;
}

/**
 * Pin recommended to the front of a list. If it is already in the list, move
 * it to index 0 (deduped). No-op when recommended is null.
 */
export function pinRecommendedFirst<T extends ReleaseLike>(
  list: T[],
  recommended: T | null,
): T[] {
  if (!recommended) return list;
  const key = releaseKey(recommended);
  const rest = list.filter((r) => releaseKey(r) !== key);
  return [recommended, ...rest];
}

export function isSameRelease(a: ReleaseLike, b: ReleaseLike): boolean {
  return releaseKey(a) === releaseKey(b);
}

/** Badge label — standard mark only when the pick is actually standard-eligible. */
export function recommendedBadgeLabel(r: ReleaseLike): string {
  return matchesStandard(r) ? 'Recommended · Standard' : 'Recommended';
}
