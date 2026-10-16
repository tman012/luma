/**
 * sourcesUtils — pure utility functions for movie sources (Radarr releases).
 *
 * Extracted from SourcesPanel.tsx so that component files contain only
 * presentation logic and other modules (ExpandedLayout, useMovieSourcesData)
 * don't need to import from a React component file.
 */

import { classifySourceType, SOURCE_TYPE_SEGMENT_ORDER, orderResolutions } from '@/components/media/sourcesShared';

// ---------------------------------------------------------------------------
// Badge palette for indexer colors
// ---------------------------------------------------------------------------

const BADGE_PALETTE = [
  { bg: 'rgba(187, 134, 252, 0.15)', fg: '#bb86fc', border: 'rgba(187, 134, 252, 0.3)' },
  { bg: 'rgba(96, 165, 250, 0.15)',  fg: '#60a5fa', border: 'rgba(96, 165, 250, 0.3)' },
  { bg: 'rgba(52, 211, 153, 0.15)',  fg: '#34d399', border: 'rgba(52, 211, 153, 0.3)' },
  { bg: 'rgba(251, 191, 36, 0.15)',  fg: '#fbbf24', border: 'rgba(251, 191, 36, 0.3)' },
  { bg: 'rgba(248, 113, 113, 0.15)', fg: '#f87171', border: 'rgba(248, 113, 113, 0.3)' },
  { bg: 'rgba(45, 212, 191, 0.15)',  fg: '#2dd4bf', border: 'rgba(45, 212, 191, 0.3)' },
  { bg: 'rgba(244, 114, 182, 0.15)', fg: '#f472b6', border: 'rgba(244, 114, 182, 0.3)' },
  { bg: 'rgba(163, 230, 53, 0.15)',  fg: '#a3e635', border: 'rgba(163, 230, 53, 0.3)' },
];

// Explicit color mappings for known indexers
const INDEXER_COLOR_MAP: Record<string, (typeof BADGE_PALETTE)[0]> = {
  IPT:  { bg: 'rgba(96, 165, 250, 0.15)',  fg: '#60a5fa', border: 'rgba(96, 165, 250, 0.3)' },  // blue
  PHD:  { bg: 'rgba(187, 134, 252, 0.15)', fg: '#bb86fc', border: 'rgba(187, 134, 252, 0.3)' },  // purple (green reserved for Recommended)
};

const _badgeColorCache: Record<string, (typeof BADGE_PALETTE)[0]> = {};
let _paletteCounter = 0;

export function getBadgeColor(indexer: string): (typeof BADGE_PALETTE)[0] {
  if (_badgeColorCache[indexer]) return _badgeColorCache[indexer];
  // Check explicit name mappings first (case-insensitive, word-boundary aware)
  const upper = indexer.toUpperCase();
  for (const [key, color] of Object.entries(INDEXER_COLOR_MAP)) {
    // Use word boundary check: "BHD" should NOT match "PHD"
    const re = new RegExp(`(^|[^A-Z])${key}($|[^A-Z])`);
    if (re.test(upper)) {
      _badgeColorCache[indexer] = color;
      return color;
    }
  }
  // Exclude explicitly-mapped palette colors from cycling to avoid duplicates
  const explicitColors = new Set(Object.values(INDEXER_COLOR_MAP).map(c => c.fg));
  let color = BADGE_PALETTE[_paletteCounter % BADGE_PALETTE.length]!;
  // Skip palette entries that match an explicit mapping
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
// Helpers
// ---------------------------------------------------------------------------

export function extractResolution(qualityName: string, relTitle: string): string {
  // Lowercase the match so "1080P" and "1080p" don't become two segments.
  const match = (qualityName || '').match(/(\d{3,4}p)/i);
  if (match?.[1]) return match[1].toLowerCase();
  const titleMatch = (relTitle || '').match(/(\d{3,4}p)/i);
  if (titleMatch?.[1]) return titleMatch[1].toLowerCase();
  // 4K / UHD without an explicit 2160p token still counts as a 4K bucket in the UI.
  const blob = `${qualityName || ''} ${relTitle || ''}`;
  if (/\b(4k|uhd)\b/i.test(blob)) return '2160p';
  return 'Unknown';
}

/** Delegates to the shared classifier (REMUX / BLURAY / WEB-DL / Other).
 *  Kept as a thin re-export for existing callers. */
export function getSourceType(name: string): string {
  return classifySourceType(name);
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ProcessedRelease {
  guid: string;
  title: string;
  indexer: string;
  indexerId: number;
  size: number;
  seeders: number;
  leechers: number;
  quality: string;
  customFormatScore: number;
  age: number | null;
  protocol: string;
  resolution: string;
  sourceType: string;
  sizeGb: string | null;
  rejected?: boolean;
  rejectionReasons?: string[];
  /** Where the release came from. 'arr' = Radarr (default), 'indexer' = raw indexer sweep. */
  origin?: 'arr' | 'indexer';
  /** Direct download link (indexer rows + all-results Arr rows). Feeds grab-to-library. */
  downloadUrl?: string;
  /** Torrent info hash (lowercased dedup key). May be empty. */
  infoHash?: string;
}

export interface SourceTypeGroup {
  type: string;
  items: ProcessedRelease[];
}

export interface QualitySegment {
  quality: string;
  totalCount: number;
  sourceTypes: SourceTypeGroup[];
}

// ---------------------------------------------------------------------------
// Grouping / transformation
// ---------------------------------------------------------------------------

export function buildQualityGroups(releases: ProcessedRelease[]): QualitySegment[] {
  const grouped: Record<string, Record<string, ProcessedRelease[]>> = {};
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

export function processRawReleases(rawReleases: Record<string, unknown>[]): ProcessedRelease[] {
  return rawReleases.map((r) => {
    const relTitle = (r.title as string) ?? '';
    const qualityName = (r.quality as string) ?? '';
    const resolution = extractResolution(qualityName, relTitle);
    // Classify from the Radarr quality string + title together — the quality
    // field is authoritative (e.g. "Bluray-1080p") and the title fills gaps.
    const sourceType = classifySourceType(`${qualityName} ${relTitle}`);
    const size = (r.size as number) ?? 0;
    const sizeGb = size ? (size / (1024 * 1024 * 1024)).toFixed(2) : null;
    return {
      guid: (r.guid as string) ?? '',
      title: relTitle,
      indexer: (r.indexer as string) || 'Unknown',
      indexerId: (r.indexerId as number) ?? (r.indexer_id as number) ?? 0,
      size,
      seeders: (r.seeders as number) ?? 0,
      leechers: (r.leechers as number) ?? 0,
      quality: qualityName,
      customFormatScore: (r.customFormatScore as number) ?? (r.custom_format_score as number) ?? 0,
      age: (r.age as number) ?? null,
      protocol: (r.protocol as string) ?? 'torrent',
      resolution,
      sourceType,
      sizeGb,
      rejected: (r.rejected as boolean) ?? false,
      rejectionReasons: (r.rejection_reasons as string[]) ?? [],
      // Passthrough for all-results merge. Default origin 'arr' for Radarr rows.
      origin: (r.origin as 'arr' | 'indexer') ?? 'arr',
      downloadUrl: (r.download_url as string) ?? (r.downloadUrl as string) ?? '',
      infoHash: (r.info_hash as string) ?? (r.infoHash as string) ?? '',
    };
  });
}
