/**
 * WatchlistPage — tabbed page with Watchlist grid + History list.
 *
 * Watchlist tab: /api/watchlist (Radarr monitored movies without files)
 * History tab: /api/downloads/history?limit=30 (recent completed downloads)
 * Click card → MediaDetailModal via openModalForItem
 *
 * Mobile: Watchlist tab renders as individual row containers (like History).
 * Desktop/Tablet: Watchlist tab renders as a responsive poster grid.
 */

import { useState, useEffect, useMemo, useCallback, useRef, memo } from 'react';
import { useRequireService } from '@/components/connect/connectService';
import { ConnectServiceCard } from '@/components/connect/ConnectServiceCard';
import { useNavigate } from 'react-router';
import { useModalHost, type ModalSource } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { useBodyScroll } from '@/hooks/useBodyScroll';
import { useUiDensity } from '@/hooks/useUiDensity';
import { type DownloadItem } from '@/hooks/useNotifications';
import { useNotificationsContext } from '@/contexts/NotificationsContext';
import { posterUrl } from '@/utils/image';
import { cn } from '@/utils/cn';
import { apiFetch } from '../apiClient';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface WatchlistMovie {
  id: string;
  title: string;
  year: number | null;
  posterUrl: string | null;
  mediaType: 'movie';
  tmdbId: number | null;
  radarrId: number | null;
  ratingKey: null;
  qualityProfileName: string;
  addedDate: string | null;
  /** ISO YYYY-MM-DD US digital (TMDb type 4), if known. */
  digitalReleaseDate: string | null;
  /** Compact badge e.g. "JAN 9" — null when no date. */
  digitalReleaseLabel: string | null;
}

interface HistoryItem {
  id: string;
  title: string;
  year: string;
  posterUrl: string | null;
  mediaType: 'movie' | 'tv';
  tmdbId: number | null;
  ratingKey: null;
  torrentName: string;
  completedTimestamp: number;
  success: boolean;
  errorMessage: string | null;
  tracker: string | null;
}

type Tab = 'watchlist' | 'history';


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeTime(ts: number): string {
  const ms = Date.now() - ts * 1000;
  const days = Math.floor(ms / 86_400_000);
  if (days <= 0) return 'Today';
  if (days === 1) return '1 day ago';
  if (days < 7) return `${days} days ago`;
  if (days < 30) return `${Math.floor(days / 7)} weeks ago`;
  if (days < 365) return `${Math.floor(days / 30)} months ago`;
  return new Date(ts * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function getAddedAgo(m: WatchlistMovie): string | null {
  if (!m.addedDate) return null;
  const parsed = new Date(m.addedDate);
  if (isNaN(parsed.getTime())) return null;
  const days = Math.floor((Date.now() - parsed.getTime()) / 86_400_000);
  if (days <= 0) return 'Added today';
  if (days === 1) return 'Added 1 day ago';
  if (days < 7) return `Added ${days} days ago`;
  if (days < 30) return `Added ${Math.floor(days / 7)} weeks ago`;
  if (days < 365) return `Added ${Math.floor(days / 30)} months ago`;
  return `Added ${parsed.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`;
}

// ---------------------------------------------------------------------------
// Download status helpers (mirrors TopBar.tsx)
// ---------------------------------------------------------------------------

function downloadStatusLabel(dl: DownloadItem): { text: string; color: string } {
  const pct = Math.round(dl.progress * 100);
  switch (dl.status) {
    case 'copying':
      return { text: `Adding to library... ${pct}%`, color: 'text-green-400' };
    case 'importing':
      return { text: 'Processing...', color: 'text-green-400' };
    case 'warning':
      return { text: 'Stalled', color: 'text-amber-400' };
    case 'queued':
      return { text: 'Queued', color: 'text-white/50' };
    case 'paused':
      return { text: 'Paused', color: 'text-white/50' };
    case 'failed':
      return { text: 'Failed', color: 'text-red-400' };
    default:
      // Download complete but status hasn't transitioned yet — show Processing
      if (pct >= 100) return { text: 'Processing...', color: 'text-green-400' };
      return { text: `Downloading... ${pct}%`, color: 'text-blue-400' };
  }
}

/** True when the item is in a "processing" limbo (no meaningful progress bar). */
function isProcessingState(dl: DownloadItem): boolean {
  return dl.status === 'importing' || (dl.progress >= 1.0 && dl.status !== 'copying');
}

function downloadBarColor(status: string): string {
  if (status === 'copying') return 'bg-green-500';
  if (status === 'warning') return 'bg-amber-500';
  if (status === 'failed') return 'bg-red-500';
  return 'bg-blue-500';
}

interface CompletedNotice {
  title: string;
  year: number;
  posterPath: string | null;
  dismissAt: number; // Date.now() + 10_000
}

// ---------------------------------------------------------------------------
// ActiveDownloadRow — mobile list view for in-progress downloads
// ---------------------------------------------------------------------------

const ActiveDownloadRow = memo(function ActiveDownloadRow({
  dl,
}: {
  dl: DownloadItem;
}) {
  const poster = posterUrl(dl.posterPath, 'w185');
  const pct = Math.round(dl.progress * 100);
  const { text: statusText, color: statusColor } = downloadStatusLabel(dl);
  const processing = isProcessingState(dl);
  const showBar = !processing;
  const barColor = downloadBarColor(dl.status);

  return (
    <div className="flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left">
      {/* Poster */}
      <div className="h-[120px] w-[80px] shrink-0 overflow-hidden rounded-lg bg-white/5 relative">
        {poster ? (
          <img
            src={poster}
            alt={dl.title}
            loading="lazy"
            draggable={false}
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-white/20">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="2" y="2" width="20" height="20" rx="2" />
              <circle cx="8" cy="8" r="2" />
              <path d="m21 15-5-5L5 21" />
            </svg>
          </div>
        )}
        {/* Pulsing orb — green when processing/copying, blue when downloading */}
        <span
          className={cn(
            'absolute left-1.5 top-1.5 block h-2.5 w-2.5 rounded-full animate-pulse',
            processing || dl.status === 'copying' ? 'bg-green-400' : 'bg-blue-400',
          )}
          style={{ boxShadow: processing || dl.status === 'copying'
            ? '0 0 6px rgba(74,222,128,0.6)'
            : '0 0 6px rgba(96,165,250,0.6)' }}
        />
      </div>

      {/* Info */}
      <div className="min-w-0 flex-1">
        <p className="truncate text-[14px] font-medium text-text-primary">
          {dl.title}
          {dl.year > 0 && (
            <span className="ml-2 font-normal text-text-muted">({dl.year})</span>
          )}
        </p>
        <div className="mt-1 flex items-center gap-2">
          <p className={`text-[13px] ${statusColor}`}>{statusText}</p>
          {dl.tracker && (
            <span className="rounded px-1.5 py-0.5 text-[12px] font-semibold bg-white/[0.08] text-white/70 border border-white/10">
              {dl.tracker}
            </span>
          )}
        </div>
        {showBar && (
          <div className="mt-1.5 h-[1.5px] w-full overflow-hidden rounded-full bg-white/10">
            <div
              className={`h-full rounded-full ${barColor} transition-[width] duration-500`}
              style={{ width: `${pct}%` }}
            />
          </div>
        )}
        {dl.eta && dl.status === 'downloading' && (
          <p className="mt-1 text-[12px] text-text-muted">{dl.eta} remaining</p>
        )}
      </div>
    </div>
  );
});

// ---------------------------------------------------------------------------
// ActiveDownloadCard — desktop grid card for in-progress downloads
// ---------------------------------------------------------------------------

function ActiveDownloadCard({ dl }: { dl: DownloadItem }) {
  const poster = posterUrl(dl.posterPath, 'w342');
  const pct = Math.round(dl.progress * 100);
  const { text: statusText, color: statusColor } = downloadStatusLabel(dl);
  const processing = isProcessingState(dl);
  const showBar = !processing;
  const barColor = downloadBarColor(dl.status);

  return (
    <div className="group relative text-left">
      <div className="relative aspect-[2/3] overflow-hidden rounded-xl">
        {poster ? (
          <img
            src={poster}
            alt={dl.title}
            loading="lazy"
            draggable={false}
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center bg-glass-10 text-sm text-text-muted">
            No Poster
          </div>
        )}

        {/* Pulsing orb — green when processing/copying, blue when downloading */}
        <div className="absolute left-2.5 top-2.5">
          <span
            className={cn(
              'block h-3 w-3 rounded-full animate-pulse',
              processing || dl.status === 'copying' ? 'bg-green-400' : 'bg-blue-400',
            )}
            style={{ boxShadow: processing || dl.status === 'copying'
              ? '0 0 8px rgba(74,222,128,0.6)'
              : '0 0 8px rgba(96,165,250,0.6)' }}
          />
        </div>

        {/* Progress bar overlay at bottom */}
        {showBar && (
          <div className="absolute bottom-0 left-0 right-0 h-[3px] bg-black/40">
            <div
              className={`h-full ${barColor} transition-[width] duration-500`}
              style={{ width: `${pct}%` }}
            />
          </div>
        )}
      </div>

      <div className="mt-2 px-0.5">
        <p className="truncate text-[14px] font-medium text-text-primary">{dl.title}</p>
        {dl.year > 0 && (
          <p className="mt-0.5 truncate text-[12px] text-text-muted">{dl.year}</p>
        )}
        <p className={`mt-0.5 text-[12px] ${statusColor}`}>{statusText}</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CompletedNoticeBanner — green "added to library" banner
// ---------------------------------------------------------------------------

function CompletedNoticeBanner({ notice }: { notice: CompletedNotice }) {
  return (
    <div className="flex items-center gap-3 rounded-xl bg-green-500/10 border border-green-500/20 px-4 py-3">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-green-400">
        <polyline points="20 6 9 17 4 12" />
      </svg>
      <p className="text-[13px] text-green-400">
        <span className="font-semibold text-green-300">{notice.title}</span>
        {notice.year > 0 && <span className="text-green-400/70"> ({notice.year})</span>}
        {' '}added to library
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

async function fetchWatchlist(): Promise<WatchlistMovie[]> {
  const resp = await apiFetch('/api/watchlist');
  if (!resp.ok) return [];
  const data = await resp.json();
  const raw = (Array.isArray(data) ? data : []) as Record<string, unknown>[];
  return raw.map((m) => {
    const tmdbId = (m.tmdb_id as number) ?? null;
    const posterPath = (m.poster_path as string) || null;
    return {
      id: `wl-${tmdbId ?? m.title}`,
      title: (m.title as string) ?? '',
      year: (m.year as number) ?? null,
      posterUrl: posterUrl(posterPath, 'w342'),
      mediaType: 'movie' as const,
      tmdbId,
      radarrId: (m.radarr_id as number) ?? null,
      ratingKey: null,
      qualityProfileName: (m.quality_profile_name as string) || '',
      addedDate: (m.added_date as string) || null,
      digitalReleaseDate: (m.digital_release_date as string) || null,
      digitalReleaseLabel: (m.digital_release_label as string) || null,
    };
  });
}

async function fetchHistory(): Promise<HistoryItem[]> {
  const resp = await apiFetch('/api/downloads/history?limit=30');
  if (!resp.ok) return [];
  const data = await resp.json();
  const raw = ((data.history ?? []) as Record<string, unknown>[]);
  return raw.map((m, i) => {
    const posterPath = (m.poster_path as string) || null;
    return {
      id: `hist-${(m.torrent_hash as string) ?? i}`,
      title: (m.tmdb_title as string) || (m.torrent_name as string) || '',
      year: (m.tmdb_year as string) ?? '',
      posterUrl: posterUrl(posterPath, 'w342'),
      mediaType: ((m.media_type as string) === 'tv' ? 'tv' : 'movie') as 'movie' | 'tv',
      tmdbId: (m.tmdb_id as number) ?? null,
      ratingKey: null,
      torrentName: (m.torrent_name as string) ?? '',
      completedTimestamp: (m.completed_timestamp as number) ?? 0,
      success: (m.success as boolean) ?? true,
      errorMessage: (m.error_message as string) ?? null,
      tracker: (m.tracker as string) ?? null,
    };
  });
}

// ---------------------------------------------------------------------------
// Upcoming TV (Sonarr calendar — this week)

interface UpcomingEpisode {
  id: string;
  seriesTitle: string;
  season: number | null;
  episode: number | null;
  episodeTitle: string;
  airDateUtc: string | null;
  network: string;
  posterUrl: string | null;
  tvdbId: number | null;
}

async function fetchUpcomingTv(): Promise<UpcomingEpisode[]> {
  try {
    const resp = await apiFetch('/api/sonarr/calendar?days=7&limit=24');
    if (!resp.ok) return [];
    const data = await resp.json();
    if (!data.enabled) return [];
    return ((data.episodes ?? []) as Record<string, unknown>[]).map((e, i) => ({
      id: `up-${(e.tvdb_id as number) ?? i}-${(e.season as number) ?? 0}-${(e.episode as number) ?? 0}`,
      seriesTitle: (e.series_title as string) ?? '',
      season: (e.season as number) ?? null,
      episode: (e.episode as number) ?? null,
      episodeTitle: (e.episode_title as string) ?? '',
      airDateUtc: (e.air_date_utc as string) ?? null,
      network: (e.network as string) ?? '',
      posterUrl: (e.poster_url as string) ?? null,
      tvdbId: (e.tvdb_id as number) ?? null,
    }));
  } catch {
    return [];
  }
}

function formatAirDate(iso: string | null): string {
  if (!iso) return '';
  const dt = new Date(iso);
  if (isNaN(dt.getTime())) return '';
  const today = new Date();
  const sameDay = dt.toDateString() === today.toDateString();
  const label = dt.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
  const time = dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  return sameDay ? `Today · ${time}` : `${label} · ${time}`;
}

const UpcomingRow = memo(function UpcomingRow({ ep }: { ep: UpcomingEpisode }) {
  const epLabel = ep.season != null && ep.episode != null ? `S${ep.season}E${ep.episode}` : '';
  return (
    <div className="flex items-center gap-3 rounded-xl px-3 py-3">
      <div className="h-[90px] w-[60px] shrink-0 overflow-hidden rounded-lg bg-white/5">
        {ep.posterUrl && (
          <img src={ep.posterUrl} alt={ep.seriesTitle} className="h-full w-full object-cover" loading="lazy" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[15px] font-semibold text-text-primary">{ep.seriesTitle}</p>
        <p className="mt-0.5 truncate text-[13px] text-text-muted">
          {epLabel}{ep.episodeTitle ? `${epLabel ? ' · ' : ''}${ep.episodeTitle}` : ''}
        </p>
        <p className="mt-0.5 text-[12px] text-accent/90">
          {formatAirDate(ep.airDateUtc)}{ep.network ? ` · ${ep.network}` : ''}
        </p>
      </div>
    </div>
  );
});

function UpcomingTvView({ loading, upcoming }: { loading: boolean; upcoming: UpcomingEpisode[] }) {
  return (
    <>
      {!loading && (
        <p className="mb-4 text-[15px] text-text-muted">
          {upcoming.length} upcoming this week
        </p>
      )}
      {loading ? (
        <div className="flex flex-col gap-1">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3 rounded-xl px-3 py-3">
              <div className="h-[90px] w-[60px] shrink-0 animate-pulse rounded-lg bg-white/5" />
              <div className="flex-1 space-y-2">
                <div className="h-4 w-2/3 animate-pulse rounded bg-white/5" />
                <div className="h-3 w-1/3 animate-pulse rounded bg-white/5" />
              </div>
            </div>
          ))}
        </div>
      ) : upcoming.length === 0 ? (
        <div className="flex h-48 items-center justify-center">
          <p className="text-[14px] text-text-muted">No upcoming episodes this week</p>
        </div>
      ) : (
        <div className="flex flex-col gap-1">
          {upcoming.map((ep) => <UpcomingRow key={ep.id} ep={ep} />)}
        </div>
      )}
    </>
  );
}

// WatchlistCard — poster grid card for desktop/tablet (memo)
// ---------------------------------------------------------------------------

const WatchlistCard = memo(function WatchlistCard({
  movie,
  onClick,
}: {
  movie: WatchlistMovie;
  onClick: () => void;
}) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const addedAgo = getAddedAgo(movie);

  return (
    <button
      type="button"
      onClick={onClick}
      className="group relative text-left outline-none focus-visible:ring-2 focus-visible:ring-accent/60 rounded-xl"
    >
      <div className="relative aspect-[2/3] overflow-hidden rounded-xl">
        {movie.posterUrl ? (
          <img
            src={movie.posterUrl}
            alt={movie.title}
            loading="lazy"
            draggable={false}
            className={cn(
              'h-full w-full object-cover transition-opacity duration-300',
              imgLoaded ? 'opacity-100' : 'opacity-0',
            )}
            onLoad={() => setImgLoaded(true)}
            onError={(e) => { (e.target as HTMLImageElement).style.visibility = 'hidden'; }}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center bg-glass-10 text-sm text-text-muted">
            No Poster
          </div>
        )}

        {/* Hover gradient */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />

        {/* Watchlist orb (blue) */}
        <div className="absolute left-2.5 top-2.5">
          <span
            className="block h-3 w-3 rounded-full bg-blue-400"
            style={{ boxShadow: '0 0 8px rgba(96,165,250,0.6)' }}
          />
        </div>

      </div>

      <div className="mt-2 px-0.5">
        {movie.digitalReleaseLabel && (
          <p className="mb-0.5 text-center text-[10px] font-semibold text-white/70 sm:text-[11px]">
            {movie.digitalReleaseLabel}
          </p>
        )}
        <p className="truncate text-[14px] font-medium text-text-primary">{movie.title}</p>
        {movie.year && (
          <p className="mt-0.5 truncate text-[12px] text-text-muted">{movie.year}</p>
        )}
        <div className="mt-1 flex flex-col gap-0.5 text-[12px] text-text-muted">
          {movie.qualityProfileName && <span>{movie.qualityProfileName}</span>}
          {addedAgo && <span>{addedAgo}</span>}
        </div>
      </div>
    </button>
  );
});

// ---------------------------------------------------------------------------
// WatchlistRow — individual row container for mobile (like HistoryRow)
// ---------------------------------------------------------------------------

const WatchlistRow = memo(function WatchlistRow({
  movie,
  onClick,
}: {
  movie: WatchlistMovie;
  onClick: () => void;
}) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const addedAgo = getAddedAgo(movie);

  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left transition-colors hover:bg-white/5 active:bg-white/8"
    >
      {/* Poster */}
      <div className="h-[120px] w-[80px] shrink-0 overflow-hidden rounded-lg bg-white/5 relative">
        {movie.posterUrl ? (
          <img
            src={movie.posterUrl}
            alt={movie.title}
            loading="lazy"
            draggable={false}
            className={cn(
              'h-full w-full object-cover transition-opacity duration-300',
              imgLoaded ? 'opacity-100' : 'opacity-0',
            )}
            onLoad={() => setImgLoaded(true)}
            onError={(e) => { (e.target as HTMLImageElement).style.visibility = 'hidden'; }}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-white/20">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="2" y="2" width="20" height="20" rx="2" />
              <circle cx="8" cy="8" r="2" />
              <path d="m21 15-5-5L5 21" />
            </svg>
          </div>
        )}
        {/* Watchlist orb (blue) */}
        <span
          className="absolute left-1.5 top-1.5 block h-2.5 w-2.5 rounded-full bg-blue-400"
          style={{ boxShadow: '0 0 6px rgba(96,165,250,0.6)' }}
        />
      </div>

      {/* Info */}
      <div className="min-w-0 flex-1">
        {movie.digitalReleaseLabel && (
          <p className="mb-0.5 text-[10px] font-semibold text-white/70">
            {movie.digitalReleaseLabel}
          </p>
        )}
        <p className="truncate text-[14px] font-medium text-text-primary">
          {movie.title}
          {movie.year && (
            <span className="ml-2 font-normal text-text-muted">({movie.year})</span>
          )}
        </p>
        <div className="mt-1.5 flex items-center gap-3 text-[13px]">
          {movie.qualityProfileName && (
            <span className="rounded px-1.5 py-0.5 text-[12px] font-semibold bg-blue-500/20 text-blue-400">
              {movie.qualityProfileName}
            </span>
          )}
        </div>
        {addedAgo && (
          <p className="mt-1 text-[12px] text-text-muted">{addedAgo}</p>
        )}
      </div>

      {/* Chevron */}
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-white/20">
        <polyline points="9 18 15 12 9 6" />
      </svg>
    </button>
  );
});

// ---------------------------------------------------------------------------
// HistoryRow — horizontal list row for completed downloads
// ---------------------------------------------------------------------------

const HistoryRow = memo(function HistoryRow({
  item,
  onClick,
  isMobile,
}: {
  item: HistoryItem;
  onClick: () => void;
  isMobile: boolean;
}) {
  const [imgLoaded, setImgLoaded] = useState(false);

  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left transition-colors hover:bg-white/5 active:bg-white/8 sm:gap-5 sm:px-4"
    >
      {/* Poster */}
      <div className={cn(
        'shrink-0 overflow-hidden rounded-lg bg-white/5',
        isMobile ? 'h-[120px] w-[80px]' : 'h-[90px] w-[60px]',
      )}>
        {item.posterUrl ? (
          <img
            src={item.posterUrl}
            alt={item.title}
            loading="lazy"
            draggable={false}
            className={cn(
              'h-full w-full object-cover transition-opacity duration-300',
              imgLoaded ? 'opacity-100' : 'opacity-0',
            )}
            onLoad={() => setImgLoaded(true)}
            onError={(e) => { (e.target as HTMLImageElement).style.visibility = 'hidden'; }}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-white/20">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="2" y="2" width="20" height="20" rx="2" />
              <circle cx="8" cy="8" r="2" />
              <path d="m21 15-5-5L5 21" />
            </svg>
          </div>
        )}
      </div>

      {/* Info */}
      <div className="min-w-0 flex-1">
        <p className={cn(
          'truncate text-text-primary',
          isMobile ? 'text-[14px] font-medium' : 'text-[16px] font-semibold',
        )}>
          {item.title}
          {item.year && (
            <span className="ml-2 font-normal text-text-muted">({item.year})</span>
          )}
        </p>
        {/* Torrent name */}
        {item.torrentName && (
          <p className={cn(
            'mt-1 text-[13px] text-text-muted',
            isMobile ? 'break-all' : 'truncate',
          )}>{item.torrentName}</p>
        )}
        <div className="mt-1.5 flex items-center gap-3 text-[13px]">
          {/* Success/fail badge */}
          {item.success ? (
            <span className="flex items-center gap-1 text-green-400">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
              Completed
            </span>
          ) : (
            <span className="flex items-center gap-1 text-red-400">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
              </svg>
              Failed
            </span>
          )}
          {/* Media type badge */}
          <span className={cn(
            'rounded px-1.5 py-0.5 text-[12px] font-semibold',
            item.mediaType === 'tv' ? 'bg-purple-500/20 text-purple-400' : 'bg-blue-500/20 text-blue-400',
          )}>
            {item.mediaType === 'tv' ? 'TV' : 'Movie'}
          </span>
          {/* Tracker badge */}
          {item.tracker && (
            <span className="rounded px-1.5 py-0.5 text-[12px] font-semibold bg-white/[0.08] text-white/70 border border-white/10">
              {item.tracker}
            </span>
          )}
        </div>
        {/* Timestamp */}
        <p className="mt-1 text-[12px] text-text-muted">
          {relativeTime(item.completedTimestamp)}
        </p>
        {/* Error message */}
        {!item.success && item.errorMessage && (
          <p className="mt-1 truncate text-[12px] text-red-400/70">{item.errorMessage}</p>
        )}
      </div>

      {/* Chevron */}
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-white/20">
        <polyline points="9 18 15 12 9 6" />
      </svg>
    </button>
  );
});

// ---------------------------------------------------------------------------
// HistoryCard — poster grid card for desktop/tablet (memo)
// ---------------------------------------------------------------------------

const HistoryCard = memo(function HistoryCard({
  item,
  onClick,
}: {
  item: HistoryItem;
  onClick: () => void;
}) {
  const [imgLoaded, setImgLoaded] = useState(false);

  return (
    <button
      type="button"
      onClick={onClick}
      className="group relative text-left outline-none focus-visible:ring-2 focus-visible:ring-accent/60 rounded-xl"
    >
      <div className="relative aspect-[2/3] overflow-hidden rounded-xl">
        {item.posterUrl ? (
          <img
            src={item.posterUrl}
            alt={item.title}
            loading="lazy"
            draggable={false}
            className={cn(
              'h-full w-full object-cover transition-opacity duration-300',
              imgLoaded ? 'opacity-100' : 'opacity-0',
            )}
            onLoad={() => setImgLoaded(true)}
            onError={(e) => { (e.target as HTMLImageElement).style.visibility = 'hidden'; }}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center bg-glass-10 text-sm text-text-muted">
            No Poster
          </div>
        )}

        {/* Hover gradient */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />

        {/* Success/fail orb */}
        <div className="absolute left-2.5 top-2.5">
          <span
            className={cn(
              'block h-3 w-3 rounded-full',
              item.success ? 'bg-green-400' : 'bg-red-400',
            )}
            style={{ boxShadow: item.success ? '0 0 8px rgba(74,222,128,0.6)' : '0 0 8px rgba(248,113,113,0.6)' }}
          />
        </div>

        {/* Media type + tracker badges */}
        <div className="absolute right-2 top-2 flex flex-col items-end gap-1">
          <span className={cn(
            'rounded px-1.5 py-0.5 text-[12px] font-semibold backdrop-blur-sm',
            item.mediaType === 'tv' ? 'bg-purple-500/30 text-purple-300' : 'bg-blue-500/30 text-blue-300',
          )}>
            {item.mediaType === 'tv' ? 'TV' : 'Movie'}
          </span>
          {item.tracker && (
            <span className="rounded px-1.5 py-0.5 text-[12px] font-semibold bg-white/[0.08] text-white/70 border border-white/10 backdrop-blur-sm">
              {item.tracker}
            </span>
          )}
        </div>
      </div>

      <div className="mt-2 px-0.5">
        <p className="truncate text-[14px] font-medium text-text-primary">{item.title}</p>
        {item.year && (
          <p className="mt-0.5 truncate text-[12px] text-text-muted">{item.year}</p>
        )}
        <p className="mt-0.5 text-[12px] text-text-muted">
          {relativeTime(item.completedTimestamp)}
        </p>
      </div>
    </button>
  );
});

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export function WatchlistPage() {
  useBodyScroll();
  const navigate = useNavigate();
  const { openMovie, openTv, close } = useModalHost();
  const d = useUiDensity();
  const isMobile = d.mode === 'compact';


  const [tab, setTab] = useState<Tab>('watchlist');
  const [wlMode, setWlMode] = useState<'movie' | 'tv'>('movie');
  const radarrOk = useRequireService('radarr', false);
  const sonarrOk = useRequireService('sonarr', false);
  const arrConnected = wlMode === 'tv' ? sonarrOk : radarrOk;
  const [movies, setMovies] = useState<WatchlistMovie[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [upcoming, setUpcoming] = useState<UpcomingEpisode[]>([]);
  const [loadingWl, setLoadingWl] = useState(true);
  const [loadingHist, setLoadingHist] = useState(true);
  const [loadingUpcoming, setLoadingUpcoming] = useState(true);

  // Active downloads from notification polling
  const { downloads } = useNotificationsContext();

  // Completion detection: track previous downloads to detect when
  // copying/importing items disappear (meaning they finished)
  const prevDownloadsRef = useRef<DownloadItem[]>([]);
  const [completedNotices, setCompletedNotices] = useState<CompletedNotice[]>([]);

  useEffect(() => {
    const prev = prevDownloadsRef.current;
    prevDownloadsRef.current = downloads;

    if (prev.length === 0) return;

    const currentTitles = new Set(downloads.map(d => d.title));
    const now = Date.now();
    const newNotices: CompletedNotice[] = [];

    for (const old of prev) {
      // Only fire "added to library" when a COPYING item disappears — that
      // means the file copy actually completed.  Importing items can vanish
      // before the copy even starts (Radarr clears queue), so those are not
      // reliable indicators.
      if (
        old.status === 'copying' &&
        !currentTitles.has(old.title)
      ) {
        newNotices.push({
          title: old.title,
          year: old.year,
          posterPath: old.posterPath,
          dismissAt: now + 10_000,
        });
      }
    }

    if (newNotices.length > 0) {
      setCompletedNotices(cur => [...newNotices, ...cur]);
    }
  }, [downloads]);

  // Auto-dismiss completed notices after 10s
  useEffect(() => {
    if (completedNotices.length === 0) return;
    const earliest = Math.min(...completedNotices.map(n => n.dismissAt));
    const delay = Math.max(0, earliest - Date.now());
    const timer = setTimeout(() => {
      setCompletedNotices(cur => cur.filter(n => n.dismissAt > Date.now()));
    }, delay + 50);
    return () => clearTimeout(timer);
  }, [completedNotices]);

  // Shared refetch helper — used by mount, visibility change, and download completion
  const refetchWatchlist = useCallback(async () => {
    const data = await fetchWatchlist();
    setMovies(data);
    setLoadingWl(false);
  }, []);

  const refetchHistory = useCallback(async () => {
    const data = await fetchHistory();
    setHistory(data);
    setLoadingHist(false);
  }, []);

  // Fetch watchlist on mount
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const data = await fetchWatchlist();
      if (!cancelled) { setMovies(data); setLoadingWl(false); }
    })();
    return () => { cancelled = true; };
  }, []);

  // Refetch when movie modal adds/removes from watchlist
  useEffect(() => {
    const onChanged = () => { void refetchWatchlist(); };
    window.addEventListener('watchlist-changed', onChanged);
    return () => window.removeEventListener('watchlist-changed', onChanged);
  }, [refetchWatchlist]);

    // Refetch watchlist when app returns to foreground (visibility change)
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        refetchWatchlist();
        if (historyFetched.current) refetchHistory();
      }
    };
    document.addEventListener('visibilitychange', handleVisibility);
    return () => document.removeEventListener('visibilitychange', handleVisibility);
  }, [refetchWatchlist, refetchHistory]);

  // Refetch watchlist when a download completes (item leaves the queue)
  const prevCompletedCountRef = useRef(0);
  useEffect(() => {
    if (completedNotices.length > prevCompletedCountRef.current) {
      // New completion detected — refetch after a short delay to let backend settle
      const timer = setTimeout(() => {
        refetchWatchlist();
        if (historyFetched.current) refetchHistory();
      }, 2000);
      prevCompletedCountRef.current = completedNotices.length;
      return () => clearTimeout(timer);
    }
    prevCompletedCountRef.current = completedNotices.length;
  }, [completedNotices.length, refetchWatchlist, refetchHistory]);

  // Periodic polling — refetch watchlist every 30s while the page is active
  useEffect(() => {
    const interval = setInterval(() => {
      if (document.visibilityState === 'visible') {
        refetchWatchlist();
      }
    }, 30_000);
    return () => clearInterval(interval);
  }, [refetchWatchlist]);

  // Fetch upcoming TV lazily — only when the TV sub-view is first opened
  const upcomingFetched = useRef(false);
  useEffect(() => {
    if (tab !== 'watchlist' || wlMode !== 'tv' || upcomingFetched.current) return;
    upcomingFetched.current = true;
    let cancelled = false;
    (async () => {
      const data = await fetchUpcomingTv();
      if (!cancelled) { setUpcoming(data); setLoadingUpcoming(false); }
    })();
    return () => { cancelled = true; };
  }, [tab, wlMode]);

  // Fetch history lazily — only when History tab first activated
  const historyFetched = useRef(false);
  useEffect(() => {
    if (tab !== 'history' || historyFetched.current) return;
    historyFetched.current = true;
    let cancelled = false;
    (async () => {
      const data = await fetchHistory();
      if (!cancelled) { setHistory(data); setLoadingHist(false); }
    })();
    return () => { cancelled = true; };
  }, [tab]);

  // Soonest US digital first; undated sink last (API already sorts; keep client consistent).
  const sortedMovies = useMemo(() =>
    [...movies].sort((a, b) => {
      const da = a.digitalReleaseDate || '9999-99-99';
      const db = b.digitalReleaseDate || '9999-99-99';
      if (da !== db) return da < db ? -1 : 1;
      return (a.title || '').localeCompare(b.title || '');
    }),
  [movies]);

  const makePersonSearch = useCallback((name: string, personId?: number) => {
    close();
    const params = new URLSearchParams({ q: name, type: 'people' });
    if (personId) params.set('personId', String(personId));
    navigate(`/?${params.toString()}`);
  }, [close, navigate]);

  const handleWlCardClick = useCallback((movie: WatchlistMovie) => {
    openModalForItem(
      { openMovie, openTv },
      movie,
      'watchlist' as ModalSource,
      { onPersonSearch: makePersonSearch },
    );
  }, [openMovie, openTv, makePersonSearch]);

  const handleHistoryClick = useCallback((item: HistoryItem) => {
    if (!item.tmdbId) return;
    openModalForItem(
      { openMovie, openTv },
      item,
      'watchlist' as ModalSource,
      { onPersonSearch: makePersonSearch },
    );
  }, [openMovie, openTv, makePersonSearch]);

  // ---------------------------------------------------------------------------
  // ---------------------------------------------------------------------------
  // Shared tab content (used in both mobile shelf and desktop layout)
  // ---------------------------------------------------------------------------

  const tabBar = (
    <div className={cn('flex gap-1 border-b border-white/10', isMobile ? 'mt-2' : 'mt-4')}>
      {(['watchlist', 'history'] as const).map((t) => (
        <button
          key={t}
          type="button"
          onClick={() => setTab(t)}
          className={cn(
            'relative px-3 pb-3 pt-1 text-[14px] font-medium transition-colors sm:px-5 sm:text-[15px]',
            tab === t ? 'text-white' : 'text-white/50 hover:text-white/70',
          )}
        >
          {t === 'watchlist' ? 'Watchlist' : 'History'}
          {tab === t && (
            <span className="absolute inset-x-0 bottom-0 h-[2px] rounded-full bg-accent" />
          )}
        </button>
      ))}
    </div>
  );

  const tabContent = (
    <>
      {/* Watchlist needs Radarr (movies) / Sonarr (TV) */}
      {tab === 'watchlist' && arrConnected === false && (
        <ConnectServiceCard service={wlMode === 'tv' ? 'sonarr' : 'radarr'} fullPage={false} />
      )}
      {/* ===== WATCHLIST TAB ===== */}
      {tab === 'watchlist' && arrConnected !== false && (
        <>
          {/* Movie / TV segmented toggle */}
          <div className="mb-4 mt-1 inline-flex rounded-full border border-white/10 bg-white/5 p-1">
            {(['movie', 'tv'] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setWlMode(m)}
                className={cn(
                  'rounded-full px-4 py-1.5 text-[13px] font-semibold transition-colors',
                  wlMode === m ? 'bg-white text-black' : 'text-white/60 hover:text-white/80',
                )}
              >
                {m === 'movie' ? 'Movies' : 'TV'}
              </button>
            ))}
          </div>

          {wlMode === 'tv' ? (
            <UpcomingTvView loading={loadingUpcoming} upcoming={upcoming} />
          ) : (
          <>
          {!loadingWl && (
            <p className="mb-4 text-[15px] text-text-muted">
              {sortedMovies.length} movie{sortedMovies.length !== 1 ? 's' : ''}
            </p>
          )}
          {isMobile ? (
            <>
              {loadingWl ? (
                <div className="flex flex-col gap-1">
                  {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-3 rounded-xl px-3 py-3">
                      <div className="h-[120px] w-[80px] shrink-0 animate-pulse rounded-lg bg-white/5" />
                      <div className="flex-1 space-y-2">
                        <div className="h-5 w-2/3 animate-pulse rounded bg-white/5" />
                        <div className="h-3 w-1/3 animate-pulse rounded bg-white/5" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : sortedMovies.length === 0 ? (
                <div className="flex h-48 items-center justify-center">
                  <p className="text-[14px] text-text-muted">Your watchlist is empty</p>
                </div>
              ) : (
                <div className="flex flex-col gap-1">
                  {sortedMovies.map((movie) => (
                    <WatchlistRow
                      key={movie.id}
                      movie={movie}
                      onClick={() => handleWlCardClick(movie)}
                    />
                  ))}
                </div>
              )}
            </>
          ) : (
            <>
              {loadingWl ? (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(130px,1fr))] gap-3 sm:grid-cols-[repeat(auto-fill,minmax(160px,1fr))] sm:gap-4 md:grid-cols-[repeat(auto-fill,minmax(180px,1fr))] md:gap-5">
                  {Array.from({ length: 12 }).map((_, i) => (
                    <div key={i}>
                      <div className="aspect-[2/3] animate-pulse rounded-xl bg-glass-5" />
                      <div className="mt-2 h-4 w-3/4 animate-pulse rounded bg-glass-5" />
                    </div>
                  ))}
                </div>
              ) : sortedMovies.length === 0 ? (
                <div className="flex h-64 items-center justify-center">
                  <p className="text-[16px] text-text-muted">Your watchlist is empty</p>
                </div>
              ) : (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(130px,1fr))] gap-3 sm:grid-cols-[repeat(auto-fill,minmax(160px,1fr))] sm:gap-4 md:grid-cols-[repeat(auto-fill,minmax(180px,1fr))] md:gap-5">
                  {sortedMovies.map((movie) => (
                    <WatchlistCard
                      key={movie.id}
                      movie={movie}
                      onClick={() => handleWlCardClick(movie)}
                    />
                  ))}
                </div>
              )}
            </>
          )}
          </>
          )}
        </>
      )}

      {/* ===== HISTORY TAB ===== */}
      {tab === 'history' && (
        <>
          {/* Summary line */}
          {!loadingHist && (
            <p className="mb-4 text-[15px] text-text-muted">
              {downloads.length > 0 && (
                <>{downloads.length} downloading · </>
              )}
              Last {history.length} download{history.length !== 1 ? 's' : ''}
            </p>
          )}

          {/* Completed notice banners */}
          {completedNotices.length > 0 && (
            <div className="mb-4 flex flex-col gap-2">
              {completedNotices.map((n) => (
                <CompletedNoticeBanner key={`${n.title}-${n.dismissAt}`} notice={n} />
              ))}
            </div>
          )}

          {isMobile ? (
            <>
              {/* Active downloads (render immediately, no loading gate) */}
              {downloads.length > 0 && (
                <div className="flex flex-col gap-1">
                  {downloads.map((dl, i) => (
                    <ActiveDownloadRow key={`dl-${dl.tmdbId ?? dl.title}-${i}`} dl={dl} />
                  ))}
                </div>
              )}

              {/* Divider between active downloads and history */}
              {downloads.length > 0 && history.length > 0 && !loadingHist && (
                <div className="my-3 border-t border-white/8" />
              )}

              {loadingHist ? (
                <div className="flex flex-col gap-3">
                  {Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-3 rounded-xl px-3 py-3">
                      <div className="h-[120px] w-[80px] shrink-0 animate-pulse rounded-lg bg-glass-5" />
                      <div className="flex-1 space-y-2">
                        <div className="h-5 w-2/3 animate-pulse rounded bg-glass-5" />
                        <div className="h-3 w-1/3 animate-pulse rounded bg-glass-5" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : history.length === 0 && downloads.length === 0 ? (
                <div className="flex h-48 items-center justify-center">
                  <p className="text-[14px] text-text-muted">No download history</p>
                </div>
              ) : (
                <div className="flex flex-col gap-1">
                  {history.map((item) => (
                    <HistoryRow
                      key={item.id}
                      item={item}
                      isMobile={isMobile}
                      onClick={() => handleHistoryClick(item)}
                    />
                  ))}
                </div>
              )}
            </>
          ) : (
            <>
              {/* Active downloads grid */}
              {downloads.length > 0 && (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(130px,1fr))] gap-3 sm:grid-cols-[repeat(auto-fill,minmax(160px,1fr))] sm:gap-4 md:grid-cols-[repeat(auto-fill,minmax(180px,1fr))] md:gap-5">
                  {downloads.map((dl, i) => (
                    <ActiveDownloadCard key={`dl-${dl.tmdbId ?? dl.title}-${i}`} dl={dl} />
                  ))}
                </div>
              )}

              {/* Divider between active downloads and history */}
              {downloads.length > 0 && history.length > 0 && !loadingHist && (
                <div className="my-5 border-t border-white/8" />
              )}

              {loadingHist ? (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(130px,1fr))] gap-3 sm:grid-cols-[repeat(auto-fill,minmax(160px,1fr))] sm:gap-4 md:grid-cols-[repeat(auto-fill,minmax(180px,1fr))] md:gap-5">
                  {Array.from({ length: 12 }).map((_, i) => (
                    <div key={i}>
                      <div className="aspect-[2/3] animate-pulse rounded-xl bg-glass-5" />
                      <div className="mt-2 h-4 w-3/4 animate-pulse rounded bg-glass-5" />
                    </div>
                  ))}
                </div>
              ) : history.length === 0 && downloads.length === 0 ? (
                <div className="flex h-64 items-center justify-center">
                  <p className="text-[16px] text-text-muted">No download history</p>
                </div>
              ) : (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(130px,1fr))] gap-3 sm:grid-cols-[repeat(auto-fill,minmax(160px,1fr))] sm:gap-4 md:grid-cols-[repeat(auto-fill,minmax(180px,1fr))] md:gap-5">
                  {history.map((item) => (
                    <HistoryCard
                      key={item.id}
                      item={item}
                      onClick={() => handleHistoryClick(item)}
                    />
                  ))}
                </div>
              )}
            </>
          )}
        </>
      )}
    </>
  );

  // ---------------------------------------------------------------------------
  // Mobile: shelf layout (rounded sheet below dynamic island, swipe to dismiss)
  // ---------------------------------------------------------------------------

  if (isMobile) {
    return (
      <div
        className="text-white"
        style={{ minHeight: '100dvh', background: 'var(--color-kiosk-black)' }}
      >
        {/* Back button + title */}
        <div
          style={{
            position: 'fixed', top: 0, left: 0, right: 0, zIndex: 50,
            paddingTop: 'env(safe-area-inset-top, 0px)',
            background: 'var(--color-kiosk-black)',
          }}
        >
          <div style={{ height: 56, display: 'flex', alignItems: 'center', paddingLeft: 8, paddingRight: 16 }}>
            <button
              type="button"
              onClick={() => navigate('/')}
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 44, height: 44, background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: 'rgba(255,255,255,0.7)' }}
              aria-label="Back"
            >
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="15 18 9 12 15 6" />
              </svg>
            </button>
            <h1 style={{ margin: 0, flex: 1, fontSize: 18, fontWeight: 600, color: 'white', textAlign: 'center', marginRight: 44 }}>Watchlist</h1>
          </div>
        </div>

        {/* Content */}
        <div style={{ paddingTop: 'calc(56px + env(safe-area-inset-top, 0px) + 8px)', paddingLeft: 16, paddingRight: 16, paddingBottom: 32 }}>
          <div style={{ marginBottom: 20 }}>{tabBar}</div>
          {tabContent}
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Desktop / Tablet layout
  // ---------------------------------------------------------------------------

  return (
    <div data-scroll-container className="luma-scroll-y h-dvh bg-kiosk-black">
      <div className="mx-auto max-w-[1600px] pb-16 px-4 pt-16 sm:px-6 sm:pb-20 sm:pt-20 md:px-8">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-text-primary sm:text-3xl md:text-[36px]">Watchlist</h1>
          {tabBar}
        </div>
        {tabContent}
      </div>
    </div>
  );
}
