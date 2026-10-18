/**
 * sourcesUtils — pure utility functions for movie sources (Radarr releases).
 *
 * Extracted from SourcesPanel.tsx so that component files contain only
 * presentation logic and other modules (ExpandedLayout, useMovieSourcesData)
 * don't need to import from a React component file.
 */

import { classifySourceType, SOURCE_TYPE_SEGMENT_ORDER, orderResolutions } from '@/components/media/sourcesShared';
import { NEUTRAL_GLASS, type ChipColor } from '@/components/media/chipGlass';

// ---------------------------------------------------------------------------
// Tracker chips: neutral glass for every tracker (v173, Designer)
// ---------------------------------------------------------------------------

export function getBadgeColor(_indexer: string): ChipColor {
  return NEUTRAL_GLASS;
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
  /** Tracker type for rows from Settings > Trackers (drives the IPT preference). */
  trackerType?: string;
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
