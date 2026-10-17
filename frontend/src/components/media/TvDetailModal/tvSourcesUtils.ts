/**
 * tvSourcesUtils — pure utility functions for TV sources (Sonarr releases).
 *
 * Extracted from TvSourcesPanel.tsx so that the component file contains only
 * presentation logic.
 */

import type { TvProcessedRelease } from './useTvSourcesData';
import type { TvShowDetail } from '@/utils/modalParsers';
import type { TvSeason, SonarrStatus } from '@/services/sonarrApi';
import { SOURCE_TYPE_SEGMENT_ORDER, orderResolutions } from '@/components/media/sourcesShared';

/** Whether a given season is currently monitored in Sonarr. */
export function isSeasonMonitored(
  sonarrStatus: SonarrStatus | null,
  seasonNumber: number,
): boolean {
  const seasons = sonarrStatus?.series?.seasons;
  if (!seasons) return false;
  return seasons.some((s) => s.seasonNumber === seasonNumber && s.monitored);
}

// ---------------------------------------------------------------------------
// Ongoing-show detection (drives the "in progress" Sources prompt)
// ---------------------------------------------------------------------------

/**
 * A show is "ongoing" when TMDb still expects more episodes — either its status
 * is a returning/in-production state or it has a scheduled next episode. Ended
 * and canceled shows are treated as complete (browse their sources directly).
 */
export function isShowOngoing(show: TvShowDetail | null): boolean {
  if (!show) return false;
  if (show.nextEpisodeToAir?.airDate) return true;
  const status = (show.status ?? '').toLowerCase();
  if (status === 'returning series' || status === 'in production' || status === 'pilot') {
    return true;
  }
  // Fallback for parsers that only set inProduction (no status string).
  if (show.inProduction && !status) return true;
  return false;
}

/**
 * Count episodes that have already aired across regular seasons (excludes
 * specials / season 0). Used to tell the user how much exists to grab.
 */
export function countAiredEpisodes(seasons: TvSeason[]): number {
  const today = new Date();
  today.setHours(23, 59, 59, 999);
  let count = 0;
  for (const season of seasons) {
    if (season.season_number === 0) continue;
    for (const ep of season.episodes ?? []) {
      if (!ep.air_date) continue;
      const aired = new Date(ep.air_date);
      if (!Number.isNaN(aired.getTime()) && aired <= today) count++;
    }
  }
  return count;
}

/**
 * Earliest not-yet-aired episode across regular seasons, derived from the
 * per-episode air dates. Used as a fallback when TMDb's top-level
 * `next_episode_to_air` is null (e.g. a show on a between-seasons break) even
 * though the next episode date is actually known at the episode level.
 */
export function findNextEpisode(
  seasons: TvSeason[],
): { airDate: string; seasonNumber: number; episodeNumber: number } | null {
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  let best: { date: Date; airDate: string; seasonNumber: number; episodeNumber: number } | null = null;
  for (const season of seasons) {
    if (season.season_number === 0) continue;
    for (const ep of season.episodes ?? []) {
      if (!ep.air_date) continue;
      const d = new Date(ep.air_date);
      if (Number.isNaN(d.getTime())) continue;
      if (d >= now && (!best || d < best.date)) {
        best = { date: d, airDate: ep.air_date, seasonNumber: season.season_number, episodeNumber: ep.episode_number };
      }
    }
  }
  return best
    ? { airDate: best.airDate, seasonNumber: best.seasonNumber, episodeNumber: best.episodeNumber }
    : null;
}

/** Format an ISO air date as a friendly label, e.g. "Jun 12, 2026". */
export function formatAirDate(airDate: string | null | undefined): string | null {
  if (!airDate) return null;
  const d = new Date(airDate);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

// ---------------------------------------------------------------------------
// Badge palette for indexer colors
// ---------------------------------------------------------------------------

const BADGE_PALETTE = [
  { bg: 'rgba(187,134,252,0.15)', fg: '#bb86fc', border: 'rgba(187,134,252,0.3)' },
  { bg: 'rgba(96,165,250,0.15)',  fg: '#60a5fa', border: 'rgba(96,165,250,0.3)' },
  { bg: 'rgba(52,211,153,0.15)',  fg: '#34d399', border: 'rgba(52,211,153,0.3)' },
  { bg: 'rgba(251,191,36,0.15)',  fg: '#fbbf24', border: 'rgba(251,191,36,0.3)' },
  { bg: 'rgba(248,113,113,0.15)', fg: '#f87171', border: 'rgba(248,113,113,0.3)' },
  { bg: 'rgba(45,212,191,0.15)',  fg: '#2dd4bf', border: 'rgba(45,212,191,0.3)' },
  { bg: 'rgba(244,114,182,0.15)', fg: '#f472b6', border: 'rgba(244,114,182,0.3)' },
  { bg: 'rgba(163,230,53,0.15)',  fg: '#a3e635', border: 'rgba(163,230,53,0.3)' },
];

export type BadgeColor = { bg: string; fg: string; border: string };

// Explicit color mappings for known indexers (keep in sync with movie sourcesUtils).
// Purple for PHD so Recommended can own green without colliding.
const INDEXER_COLOR_MAP: Record<string, BadgeColor> = {
  IPT:  { bg: 'rgba(96, 165, 250, 0.15)',  fg: '#60a5fa', border: 'rgba(96, 165, 250, 0.3)' },  // blue
  PHD:  { bg: 'rgba(187, 134, 252, 0.15)', fg: '#bb86fc', border: 'rgba(187, 134, 252, 0.3)' },  // purple
};

const _badgeColorCache: Record<string, BadgeColor> = {};
let _paletteCounter = 0;

export function getBadgeColor(indexer: string): BadgeColor {
  if (_badgeColorCache[indexer]) return _badgeColorCache[indexer];
  const upper = indexer.toUpperCase();
  for (const [key, color] of Object.entries(INDEXER_COLOR_MAP)) {
    const re = new RegExp(`(^|[^A-Z])${key}($|[^A-Z])`);
    if (re.test(upper)) {
      _badgeColorCache[indexer] = color;
      return color;
    }
  }
  const explicitColors = new Set(Object.values(INDEXER_COLOR_MAP).map(c => c.fg));
  let color = BADGE_PALETTE[_paletteCounter % BADGE_PALETTE.length]!;
  let attempts = 0;
  while (explicitColors.has(color.fg) && attempts < BADGE_PALETTE.length) {
    _paletteCounter++;
    color = BADGE_PALETTE[_paletteCounter % BADGE_PALETTE.length]!;
    attempts++;
  }
  _badgeColorCache[indexer] = color;
  _paletteCounter++;
  return color;
}

// ---------------------------------------------------------------------------
// Quality grouping types
// ---------------------------------------------------------------------------

export interface SourceTypeGroup {
  type: string;
  items: TvProcessedRelease[];
}

export interface QualitySegment {
  quality: string;
  totalCount: number;
  sourceTypes: SourceTypeGroup[];
}

// ---------------------------------------------------------------------------
// Season / episode scope segmentation
// ---------------------------------------------------------------------------

export interface ScopeOption {
  /** 'season' for the full-season pack, or `e<n>` for a single episode. */
  key: string;
  label: string;
  count: number;
  /** Episode number for episode scopes; null for the season pack. */
  episode: number | null;
}

/**
 * Split a season search's releases into scopes: the full-season pack and each
 * individual episode present. Season pack first, then episodes ascending. Used
 * so a season browse can be narrowed to "Full season" or one episode before
 * drilling into resolution/type.
 */
export function buildScopeOptions(releases: TvProcessedRelease[]): ScopeOption[] {
  const seasonCount = releases.filter((r) => r.packType === 'season').length;
  const epCounts = new Map<number, number>();
  for (const r of releases) {
    if (r.packType === 'episode' && r.packEpisode != null) {
      epCounts.set(r.packEpisode, (epCounts.get(r.packEpisode) ?? 0) + 1);
    }
  }
  const opts: ScopeOption[] = [];
  if (seasonCount > 0) opts.push({ key: 'season', label: 'Full season', count: seasonCount, episode: null });
  for (const ep of [...epCounts.keys()].sort((a, b) => a - b)) {
    opts.push({ key: `e${ep}`, label: `Episode ${ep}`, count: epCounts.get(ep)!, episode: ep });
  }
  return opts;
}

/** Filter releases to the selected scope (null → all). */
export function filterByScope(releases: TvProcessedRelease[], scopeKey: string | null): TvProcessedRelease[] {
  if (!scopeKey) return releases;
  if (scopeKey === 'season') return releases.filter((r) => r.packType === 'season');
  const ep = parseInt(scopeKey.slice(1), 10);
  return releases.filter((r) => r.packType === 'episode' && r.packEpisode === ep);
}

// ---------------------------------------------------------------------------
// Grouping
// ---------------------------------------------------------------------------

export function buildQualityGroups(releases: TvProcessedRelease[]): QualitySegment[] {
  const grouped: Record<string, Record<string, TvProcessedRelease[]>> = {};
  for (const r of releases) {
    if (!grouped[r.resolution]) grouped[r.resolution] = {};
    if (!grouped[r.resolution]![r.sourceType]) grouped[r.resolution]![r.sourceType] = [];
    grouped[r.resolution]![r.sourceType]!.push(r);
  }
  const sourceTypeOrder = SOURCE_TYPE_SEGMENT_ORDER;
  const sortedQualities = orderResolutions(Object.keys(grouped));
  return sortedQualities.map((quality) => {
    const sourceTypes = grouped[quality]!;
    const sortedTypes = Object.keys(sourceTypes).sort((a, b) => {
      const aIdx = sourceTypeOrder.indexOf(a);
      const bIdx = sourceTypeOrder.indexOf(b);
      return (aIdx === -1 ? 999 : aIdx) - (bIdx === -1 ? 999 : bIdx);
    });
    const totalCount = Object.values(sourceTypes).reduce((sum, arr) => sum + arr.length, 0);
    return {
      quality,
      totalCount,
      sourceTypes: sortedTypes.map((type) => ({
        type,
        items: sourceTypes[type]!.sort((a, b) => b.seeders - a.seeders),
      })),
    };
  });
}
