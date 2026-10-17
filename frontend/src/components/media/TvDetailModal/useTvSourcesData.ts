/**
 * useTvSourcesData — shared hook for fetching Sonarr TV releases.
 *
 * Extracts the fetch logic previously duplicated between TvSourcesPanel (expanded)
 * and TvMobileSourcesPanel (compact). Both modes now use this single hook.
 */

import { useState, useEffect, useRef } from 'react';
import {
  searchSeasonReleases,
  searchEpisodeReleases,
  downloadRelease,
  type SonarrRelease,
} from '@/services/sonarrApi';
import { fetchAllResults, mergeReleases, type AllResultsRelease } from '@/services/allSourcesApi';
import { classifySourceType } from '@/components/media/sourcesShared';
import type { SourcesTarget } from './types';

export interface TvProcessedRelease {
  guid: string;
  title: string;
  indexer: string;
  indexerId: number;
  size: number;
  seeders: number;
  leechers: number;
  quality: string;
  age: number | null;
  resolution: string;
  sourceType: string;
  sizeGb: string | null;
  rejected?: boolean;
  rejectionReasons?: string[];
  /** Where the release came from. 'arr' = Sonarr (default), 'indexer' = raw indexer sweep. */
  origin?: 'arr' | 'indexer';
  /** Direct download link (indexer rows). Feeds grab-to-library. */
  downloadUrl?: string;
  /** Torrent info hash (lowercased dedup key). May be empty. */
  infoHash?: string;
  /** Protocol — usenet rows can't ride the qBit pipeline. */
  protocol?: string;
  /** Tracker type for rows from Settings > Trackers (drives the IPT preference). */
  trackerType?: string;
  /** Whether the release is a full-season pack or a single episode, parsed from
   *  the title. Drives the season/episode segmentation in the TV sources panel. */
  packType: 'season' | 'episode';
  /** Episode number when packType === 'episode' (null for season packs). */
  packEpisode: number | null;
}

/**
 * Classify a release as a full-season pack or a single episode from its title.
 * A single `SxxEyy` (not part of a range like `S01E01-E10`) is an episode;
 * `1x05` style counts too. Everything else — season packs, complete-series,
 * multi-episode ranges — is treated as a season-scoped pack.
 */
function detectTvScope(title: string): { packType: 'season' | 'episode'; packEpisode: number | null } {
  const t = title || '';
  // Multi-episode range (S01E01-E10 / S01E01-10 / E01-E10) → season-scoped pack.
  if (/S\d{1,2}\s?E\d{1,3}\s?-\s?E?\d{1,3}/i.test(t) || /\bE\d{1,3}\s?-\s?E\d{1,3}\b/i.test(t)) {
    return { packType: 'season', packEpisode: null };
  }
  // Single SxxEyy (not immediately followed by a range dash).
  const m = t.match(/S\d{1,2}\s?E(\d{1,3})(?!\s?-\s?E?\d)/i);
  if (m) return { packType: 'episode', packEpisode: parseInt(m[1]!, 10) };
  // 1x05 style single episode.
  const m2 = t.match(/\b\d{1,2}x(\d{1,3})\b/i);
  if (m2) return { packType: 'episode', packEpisode: parseInt(m2[1]!, 10) };
  return { packType: 'season', packEpisode: null };
}

function extractResolution(quality: string, title: string): string {
  const combined = `${quality || ''} ${title || ''}`;
  if (/2160p/i.test(combined)) return '2160p';
  if (/\b(4k|uhd)\b/i.test(combined)) return '2160p';
  if (/1080p/i.test(combined)) return '1080p';
  if (/720p/i.test(combined)) return '720p';
  if (/480p/i.test(combined)) return '480p';
  const match = combined.match(/(\d{3,4}p)/i);
  if (match?.[1]) return match[1].toLowerCase();
  return 'Unknown';
}

function processReleases(raw: SonarrRelease[]): TvProcessedRelease[] {
  return raw
    .map((r) => {
      const relTitle = r.title ?? '';
      const qualityName = r.quality ?? '';
      const resolution = extractResolution(qualityName, relTitle);
      const sourceType = classifySourceType(`${qualityName} ${relTitle}`);
      const size = r.size ?? 0;
      const sizeGb = size ? (size / (1024 * 1024 * 1024)).toFixed(2) : null;
      const rr = r as SonarrRelease & Record<string, unknown>;
      return {
        guid: r.guid ?? '',
        title: relTitle,
        indexer: r.indexer || 'Unknown',
        indexerId: r.indexer_id ?? 0,
        size,
        seeders: r.seeders ?? 0,
        leechers: r.leechers ?? 0,
        quality: qualityName,
        age: r.age ?? null,
        resolution,
        sourceType,
        sizeGb,
        // Passthrough for all-results merge. Default origin 'arr' for Sonarr rows.
        rejected: (rr.rejected as boolean) ?? false,
        rejectionReasons: (rr.rejection_reasons as string[]) ?? [],
        origin: (rr.origin as 'arr' | 'indexer') ?? 'arr',
        downloadUrl: (rr.download_url as string) ?? (rr.downloadUrl as string) ?? '',
        infoHash: (rr.info_hash as string) ?? (rr.infoHash as string) ?? '',
        protocol: (rr.protocol as string) ?? 'torrent',
        ...detectTvScope(relTitle),
      };
    })
    .sort((a, b) => b.seeders - a.seeders);
}

/** Adapt a raw all-results indexer release into the TvProcessedRelease shape. */
function indexerToProcessed(r: AllResultsRelease): TvProcessedRelease {
  const resolution = extractResolution(r.quality, r.title);
  const sourceType = classifySourceType(`${r.quality} ${r.title}`);
  const sizeGb = r.size ? (r.size / (1024 * 1024 * 1024)).toFixed(2) : null;
  return {
    guid: r.guid,
    title: r.title,
    indexer: r.indexer,
    indexerId: r.indexerId,
    size: r.size,
    seeders: r.seeders,
    leechers: r.leechers,
    quality: r.quality,
    age: null,
    resolution,
    sourceType,
    sizeGb,
    rejected: r.rejected,
    rejectionReasons: r.rejectionReasons,
    origin: 'indexer',
    downloadUrl: r.downloadUrl,
    infoHash: r.infoHash,
    protocol: r.protocol,
    trackerType: r.trackerType,
    ...detectTvScope(r.title),
  };
}

const pad2 = (n: number) => String(n).padStart(2, '0');

/** The tracker query for a show: its title plus S01E05 for an episode or S01 for a season. */
export function tvIndexerQuery(title: string | undefined, target: SourcesTarget): string {
  const t = (title || '').trim();
  if (!t) return '';
  if (typeof target.season !== 'number' || !Number.isFinite(target.season)) return t;
  if (target.type === 'season') return `${t} S${pad2(target.season)}`;
  if (typeof target.episode === 'number') return `${t} S${pad2(target.season)}E${pad2(target.episode)}`;
  return t;
}

interface UseTvSourcesDataOptions {
  target: SourcesTarget;
  tvdbId: number;
  tmdbId?: number;
  /** Show title — used for the ungated indexer sweep in all-results mode. */
  title?: string;
  /** Show first-air year. Not sent to the indexer sweep (see below). */
  year?: number | null;
  /**
   * Optional plain-text query used ONLY for the ungated indexer / Prowlarr
   * sweep. Sonarr search + grab/rename identity keep using title + tmdbId.
   */
  indexerQuery?: string | null;
}

interface UseTvSourcesDataResult {
  loading: boolean;
  error: boolean;
  releases: TvProcessedRelease[];
  downloadIds: { seriesId?: number; episodeId?: number };
  handleGrab: (guid: string, indexerId: number) => Promise<boolean>;
}

export function useTvSourcesData({
  target,
  tvdbId,
  tmdbId,
  title,
  year,
  indexerQuery = null,
}: UseTvSourcesDataOptions): UseTvSourcesDataResult {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [releases, setReleases] = useState<TvProcessedRelease[]>([]);
  const [downloadIds, setDownloadIds] = useState<{
    seriesId?: number;
    episodeId?: number;
  }>({});
  // Override search / clear override is an explicit retry: skip server caches.
  const lastQuery = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    const refresh = lastQuery.current !== undefined && lastQuery.current !== (indexerQuery ?? null);
    lastQuery.current = indexerQuery ?? null;

    async function fetchReleases() {
      setLoading(true);
      setError(false);
      try {
        let data: {
          success: boolean;
          releases: SonarrRelease[];
          series_id?: number;
          episode_id?: number;
        };
        if (!tvdbId) {
          // No TVDB id: Sonarr can't search this show; the trackers still can.
          data = { success: false, releases: [] };
        } else if (target.type === 'season') {
          data = await searchSeasonReleases(tvdbId, target.season, tmdbId);
        } else {
          data = await searchEpisodeReleases(
            tvdbId,
            target.season,
            target.episode!,
            tmdbId,
          );
        }

        if (cancelled) return;

        // A Sonarr failure (show not in Sonarr, Sonarr down, etc.) must NOT
        // suppress the ungated indexer results — those bypass Arr entirely.
        const sonarrOk = data.success;
        setDownloadIds({
          seriesId: data.series_id,
          episodeId: data.episode_id,
        });

        const arrReleases = sonarrOk ? processReleases(data.releases || []) : [];

        // Always fetch the ungated indexer sweep and merge it with the Sonarr
        // rows (Arr wins on collision). Rejected rows are kept — the panel hides
        // them client-side unless the user toggles "Show rejected".
        // Plain-text override replaces only this indexer query string. Omit year
        // while overriding so wrong matched metadata does not narrow to zero.
        // TV release names carry SxxEyy (or Sxx for a season), never the
        // first-air year: the year is never sent for TV.
        const indexerTitle = (indexerQuery || '').trim() || tvIndexerQuery(title, target);
        const indexerYear = null;
        // Without Sonarr (or with no trackers) an answered sweep is still an
        // answer: only a sweep that failed outright counts toward an error.
        let indexerOk = !!indexerTitle;
        const indexerRaw = indexerTitle
          ? await fetchAllResults(indexerTitle, 'tv', indexerYear, tmdbId, controller.signal, refresh)
              .catch((err: unknown) => {
                if ((err as { name?: string })?.name === 'AbortError') throw err;
                indexerOk = false;
                return [] as AllResultsRelease[];
              })
          : [];
        const indexerReleases = indexerRaw.map(indexerToProcessed);
        const merged = mergeReleases(arrReleases, indexerReleases) as TvProcessedRelease[];
        merged.sort((a, b) => b.seeders - a.seeders);
        if (!cancelled) {
          setReleases(merged);
          // Only surface an error when we truly have nothing to show.
          if (!sonarrOk && !indexerOk && merged.length === 0) setError(true);
        }
      } catch (err: unknown) {
        const e = err as { name?: string };
        if (e?.name !== 'AbortError' && !cancelled) setError(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchReleases();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [tvdbId, tmdbId, target, title, year, indexerQuery]);

  const handleGrab = async (
    guid: string,
    indexerId: number,
  ): Promise<boolean> => {
    try {
      const result = await downloadRelease(guid, indexerId, {
        seriesId: downloadIds.seriesId,
        season: target.type === 'season' ? target.season : undefined,
        episodeId: downloadIds.episodeId,
        tmdbId,
      });
      return result.success;
    } catch {
      return false;
    }
  };

  return { loading, error, releases, downloadIds, handleGrab };
}
