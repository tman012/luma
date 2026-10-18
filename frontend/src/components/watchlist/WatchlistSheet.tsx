import { useState, useEffect, useCallback, useRef, memo } from 'react';
import { createPortal } from 'react-dom';
import { useModalHost, type ModalSource } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { posterUrl } from '@/utils/image';
import { cn } from '@/utils/cn';
import { apiFetch } from '../../apiClient';

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
  digitalReleaseDate: string | null;
  digitalReleaseLabel: string | null;
}

/** A monitored Sonarr series that hasn't aired yet — the TV equivalent of a
 *  watchlisted movie we don't have and are waiting for. */
interface WatchlistSeries {
  id: string;
  title: string;
  year: number | null;
  posterUrl: string | null;
  mediaType: 'tv';
  tmdbId: number | null;
  ratingKey: null;
  premiereDate: string | null;
  episodeOrder: number;
  qualityProfileName: string;
}

type Tab = 'movies' | 'tv';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const DISMISS_THRESHOLD = 120;
const ENTER_DURATION = 600;
const EXIT_DURATION = 400;
const SPRING = 'cubic-bezier(0.16, 1, 0.3, 1)';

function getAddedAgo(m: WatchlistMovie): string | null {
  if (!m.addedDate) return null;
  const parsed = new Date(m.addedDate);
  if (isNaN(parsed.getTime())) return null;
  const days = Math.floor((Date.now() - parsed.getTime()) / 86_400_000);
  if (days <= 0) return 'Added today';
  if (days === 1) return 'Added 1 day ago';
  if (days < 7) return `Added ${days} days ago`;
  if (days < 30) return `Added ${Math.floor(days / 7)} weeks ago`;
  return `Added ${Math.floor(days / 30)} months ago`;
}

/** "Premieres tonight · 9:00 PM" / "Premieres Oct 15, 2026" / TBA fallback. */
function premiereLabel(iso: string | null): string {
  if (!iso) return 'Premiere date TBA';
  const dt = new Date(iso);
  if (isNaN(dt.getTime())) return 'Premiere date TBA';
  const now = new Date();
  const days = Math.round(
    (new Date(dt.getFullYear(), dt.getMonth(), dt.getDate()).getTime()
      - new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()) / 86_400_000,
  );
  const time = dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  if (days === 0) return `Premieres today · ${time}`;
  if (days === 1) return `Premieres tomorrow · ${time}`;
  if (days > 1 && days < 7) {
    return `Premieres ${dt.toLocaleDateString('en-US', { weekday: 'long' })} · ${time}`;
  }
  // Beyond a week the weekday stops helping; show the year once it differs.
  const sameYear = dt.getFullYear() === now.getFullYear();
  return `Premieres ${dt.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
  })}`;
}

/** Days until premiere, for the countdown chip. Null when undated or past. */
function daysUntil(iso: string | null): number | null {
  if (!iso) return null;
  const dt = new Date(iso);
  if (isNaN(dt.getTime())) return null;
  const now = new Date();
  const days = Math.round(
    (new Date(dt.getFullYear(), dt.getMonth(), dt.getDate()).getTime()
      - new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()) / 86_400_000,
  );
  return days >= 0 ? days : null;
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
    const pp = (m.poster_path as string) || null;
    return {
      id: `wl-${tmdbId ?? m.title}`,
      title: (m.title as string) ?? '',
      year: (m.year as number) ?? null,
      posterUrl: posterUrl(pp, 'w342'),
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

async function fetchTvWatchlist(): Promise<WatchlistSeries[]> {
  try {
    const resp = await apiFetch('/api/watchlist/tv');
    if (!resp.ok) return [];
    const data = await resp.json();
    if (!data.enabled) return [];
    return ((data.series ?? []) as Record<string, unknown>[]).map((s, i) => {
      const tmdbId = (s.tmdb_id as number) ?? null;
      return {
        id: `wltv-${tmdbId ?? (s.sonarr_id as number) ?? i}`,
        title: (s.title as string) ?? '',
        year: (s.year as number) ?? null,
        posterUrl: posterUrl((s.poster_path as string) || null, 'w342'),
        mediaType: 'tv' as const,
        tmdbId,
        ratingKey: null,
        premiereDate: (s.premiere_date as string) ?? null,
        episodeOrder: (s.episode_order as number) ?? 0,
        qualityProfileName: (s.quality_profile_name as string) || '',
      };
    });
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Row components (inline, compact)
// ---------------------------------------------------------------------------

const WlRow = memo(function WlRow({ movie, onClick }: { movie: WatchlistMovie; onClick: () => void }) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const addedAgo = getAddedAgo(movie);

  return (
    <button type="button" onClick={onClick}
      className="flex w-full items-center gap-3 px-3 py-3 text-left active:bg-white/5"
    >
      <div className="h-[120px] w-[80px] shrink-0 overflow-hidden rounded-lg bg-white/5 relative">
        {movie.posterUrl ? (
          <img src={movie.posterUrl} alt={movie.title} loading="lazy" draggable={false}
            className={cn('h-full w-full object-cover transition-opacity duration-300', imgLoaded ? 'opacity-100' : 'opacity-0')}
            onLoad={() => setImgLoaded(true)}
            onError={(e) => { (e.target as HTMLImageElement).style.visibility = 'hidden'; }}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-white/20 text-[11px]">No Poster</div>
        )}
        <span className="absolute left-1.5 top-1.5 block h-2.5 w-2.5 rounded-full bg-blue-400"
          style={{ boxShadow: '0 0 6px rgba(96,165,250,0.6)' }} />
      </div>
      <div className="min-w-0 flex-1">
        {movie.digitalReleaseLabel && (
          <p className="mb-0.5 text-[10px] font-semibold text-white/70">
            {movie.digitalReleaseLabel}
          </p>
        )}
        <p className="truncate text-[14px] font-medium text-white/90">
          {movie.title}
          {movie.year && <span className="ml-2 font-normal text-white/40">({movie.year})</span>}
        </p>
        {movie.qualityProfileName && (
          <div className="mt-1.5">
            <span className="rounded px-1.5 py-0.5 text-[11px] font-semibold bg-blue-500/20 text-blue-400">
              {movie.qualityProfileName}
            </span>
          </div>
        )}
        {addedAgo && <p className="mt-1 text-[12px] text-white/30">{addedAgo}</p>}
      </div>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-white/20">
        <polyline points="9 18 15 12 9 6" />
      </svg>
    </button>
  );
});

const TvRow = memo(function TvRow({ series, onClick }: { series: WatchlistSeries; onClick: () => void }) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const countdown = daysUntil(series.premiereDate);

  return (
    <button type="button" onClick={onClick} disabled={!series.tmdbId}
      className="flex w-full items-center gap-3 px-3 py-3 text-left active:bg-white/5 disabled:active:bg-transparent"
    >
      <div className="h-[120px] w-[80px] shrink-0 overflow-hidden rounded-lg bg-white/5 relative">
        {series.posterUrl ? (
          <img src={series.posterUrl} alt={series.title} loading="lazy" draggable={false}
            className={cn('h-full w-full object-cover transition-opacity duration-300', imgLoaded ? 'opacity-100' : 'opacity-0')}
            onLoad={() => setImgLoaded(true)}
            onError={(e) => { (e.target as HTMLImageElement).style.visibility = 'hidden'; }}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-white/20 text-[11px]">No Poster</div>
        )}
        {/* Same blue "on the watchlist" orb the movie rows use */}
        <span className="absolute left-1.5 top-1.5 block h-2.5 w-2.5 rounded-full bg-blue-400"
          style={{ boxShadow: '0 0 6px rgba(96,165,250,0.6)' }} />
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[14px] font-medium text-white/90">
          {series.title}
          {series.year && <span className="ml-2 font-normal text-white/40">({series.year})</span>}
        </p>

        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          {countdown != null && countdown > 1 && (
            <span className="rounded px-1.5 py-0.5 text-[11px] font-semibold bg-amber-500/15 text-amber-400">
              In {countdown} days
            </span>
          )}
          {series.qualityProfileName && (
            <span className="rounded px-1.5 py-0.5 text-[11px] font-semibold bg-blue-500/20 text-blue-400">
              {series.qualityProfileName}
            </span>
          )}
        </div>

        <p className="mt-1 truncate text-[12px] text-white/40">{premiereLabel(series.premiereDate)}</p>
        {series.episodeOrder > 0 && (
          <p className="text-[12px] text-white/25">
            {series.episodeOrder} episode{series.episodeOrder === 1 ? '' : 's'}
          </p>
        )}
      </div>
      {series.tmdbId ? (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-white/20">
          <polyline points="9 18 15 12 9 6" />
        </svg>
      ) : null}
    </button>
  );
});

// ---------------------------------------------------------------------------
// WatchlistSheet — bottom sheet with Movies + TV tabs.
// Download history intentionally lives in the Activity sheet, not here.
// ---------------------------------------------------------------------------

interface WatchlistSheetProps {
  open: boolean;
  onClose: () => void;
}

export function WatchlistSheet({ open, onClose }: WatchlistSheetProps) {
  const { openMovie, openTv } = useModalHost();

  // --- Sheet animation (same as UpgradesSheet / MovieDetailModal) ---
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });
  const [dragOffset, setDragOffset] = useState(0);
  const [phase, setPhase] = useState<'entering' | 'visible' | 'exiting' | 'gone'>('gone');

  useEffect(() => {
    if (open) {
      setPhase('entering');
      setDragOffset(0);
      requestAnimationFrame(() => {
        requestAnimationFrame(() => setPhase('visible'));
      });
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const prevBg = document.documentElement.style.background;
    const prevOverflow = document.body.style.overflow;
    document.documentElement.style.background = '#000';
    document.body.style.overflow = 'hidden';
    return () => {
      document.documentElement.style.background = prevBg;
      document.body.style.overflow = prevOverflow;
    };
  }, [open]);

  const handleDismiss = useCallback(() => {
    setDragOffset(0);
    requestAnimationFrame(() => {
      setPhase('exiting');
      setTimeout(() => { setPhase('gone'); onClose(); }, EXIT_DURATION);
    });
  }, [onClose]);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest('[data-sheet-handle]') && e.touches.length > 0) {
      dragRef.current = { active: true, startY: e.touches[0]!.clientY, currentY: e.touches[0]!.clientY };
    }
  }, []);
  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragRef.current.active || !e.touches.length) return;
    dragRef.current.currentY = e.touches[0]!.clientY;
    setDragOffset(Math.max(0, dragRef.current.currentY - dragRef.current.startY));
  }, []);
  const onTouchEnd = useCallback(() => {
    if (!dragRef.current.active) return;
    const dy = dragRef.current.currentY - dragRef.current.startY;
    dragRef.current.active = false;
    if (dy > DISMISS_THRESHOLD) handleDismiss();
    else setDragOffset(0);
  }, [handleDismiss]);

  // --- Data ---
  const [tab, setTab] = useState<Tab>('movies');
  const [movies, setMovies] = useState<WatchlistMovie[]>([]);
  const [series, setSeries] = useState<WatchlistSeries[]>([]);
  const [loadingWl, setLoadingWl] = useState(true);
  const [loadingTv, setLoadingTv] = useState(true);
  const tvFetched = useRef(false);

  useEffect(() => {
    if (!open) return;
    setLoadingWl(true);
    fetchWatchlist().then(d => { setMovies(d); setLoadingWl(false); });
  }, [open]);

  // Refresh when movie modal adds/removes from watchlist while sheet is open
  // (or was just behind a modal).
  useEffect(() => {
    const onChanged = () => {
      if (!open) return;
      fetchWatchlist().then(d => setMovies(d));
    };
    window.addEventListener('watchlist-changed', onChanged);
    return () => window.removeEventListener('watchlist-changed', onChanged);
  }, [open]);

  // TV is deferred until the tab is first opened — Sonarr's full series list
  // plus a calendar sweep is the expensive half of this sheet.
  useEffect(() => {
    if (!open || tab !== 'tv' || tvFetched.current) return;
    tvFetched.current = true;
    setLoadingTv(true);
    fetchTvWatchlist().then(d => { setSeries(d); setLoadingTv(false); });
  }, [open, tab]);

  // Sort watchlist soonest digital first
  const sortedMovies = [...movies].sort((a, b) => {
    const da = a.digitalReleaseDate || '9999-99-99';
    const db = b.digitalReleaseDate || '9999-99-99';
    if (da !== db) return da < db ? -1 : 1;
    return (a.title || '').localeCompare(b.title || '');
  });

  // --- Modal: hide sheet behind, restore on close ---
  const [behindModal, setBehindModal] = useState(false);

  const handleItemClick = useCallback((item: { mediaType: string; tmdbId: number | null; ratingKey: null }) => {
    if (!item.tmdbId) return;
    setBehindModal(true);
    openModalForItem(
      { openMovie, openTv },
      item as any,
      'watchlist' as ModalSource,
    );
  }, [openMovie, openTv]);

  useEffect(() => {
    if (!behindModal) return;
    const restore = () => setBehindModal(false);
    window.addEventListener('modal-closed', restore);
    return () => window.removeEventListener('modal-closed', restore);
  }, [behindModal]);

  // --- Render ---
  if (phase === 'gone' && !open) return null;

  const sheetTransform =
    dragOffset > 0 ? `translate3d(0, ${dragOffset}px, 0)`
      : phase === 'visible' ? 'translate3d(0, 0, 0)'
        : 'translate3d(0, 100%, 0)';

  const sheetTransition =
    dragOffset > 0 ? 'none'
      : phase === 'exiting' ? `transform ${EXIT_DURATION}ms cubic-bezier(0.4, 0, 1, 1)`
        : `transform ${ENTER_DURATION}ms ${SPRING}`;

  return createPortal(
    <div
      data-sheet="WatchlistSheet"
      style={{
        position: 'fixed', inset: 0,
        zIndex: behindModal ? 9999 : 10001,
        display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
        backgroundColor: behindModal ? 'rgba(0,0,0,0)' : phase === 'visible' ? 'rgba(0,0,0,0.4)' : 'rgba(0,0,0,0)',
        transition: phase === 'exiting'
          ? `background-color ${EXIT_DURATION}ms ease`
          : `background-color ${ENTER_DURATION}ms ease`,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) handleDismiss(); }}
    >
      <div
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        style={{
          height: 'calc(100% - env(safe-area-inset-top, 0px) - 6px)',
          display: 'flex', flexDirection: 'column',
          background: 'var(--color-kiosk-black)',
          borderRadius: '20px 20px 0 0',
          overflow: 'hidden',
          transform: sheetTransform,
          transition: sheetTransition,
          willChange: 'transform',
          backfaceVisibility: 'hidden',
        }}
      >
        {/* Grab handle */}
        <div data-sheet-handle
          style={{ flexShrink: 0, display: 'flex', justifyContent: 'center', padding: '12px 0 8px', cursor: 'grab' }}
        >
          <div style={{ width: 40, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)' }} />
        </div>

        {/* Title + Movies/TV tabs */}
        <div className="shrink-0 px-4 pb-3">
          <h2 className="mb-2 text-[18px] font-bold text-white">Watchlist</h2>
          <div className="inline-flex rounded-lg bg-white/8 p-0.5">
            {(['movies', 'tv'] as const).map(t => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={cn(
                  'rounded-md px-4 py-1.5 text-[13px] font-semibold transition-colors',
                  tab === t ? 'bg-white/15 text-white/90' : 'text-white/40',
                )}
              >
                {t === 'movies' ? 'Movies' : 'TV'}
              </button>
            ))}
          </div>
        </div>

        {/* Scrollable content */}
        <div
          data-scroll-container
          className="luma-scroll-y flex-1 px-1"
          style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}
        >
          {tab === 'movies' && (
            <>
              {loadingWl ? (
                <div className="flex flex-col items-center justify-center py-20 text-white/30">
                  <div className="h-6 w-6 animate-spin rounded-full border-2 border-white/10 border-t-white/60" />
                  <p className="mt-4 text-[14px]">Loading...</p>
                </div>
              ) : sortedMovies.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-20 text-white/30">
                  <p className="text-[14px]">Your watchlist is empty</p>
                </div>
              ) : (
                <div className="divide-y divide-white/5">
                  {sortedMovies.map(movie => (
                    <WlRow key={movie.id} movie={movie} onClick={() => handleItemClick(movie)} />
                  ))}
                </div>
              )}
            </>
          )}

          {tab === 'tv' && (
            <>
              {loadingTv ? (
                <div className="flex flex-col items-center justify-center py-20 text-white/30">
                  <div className="h-6 w-6 animate-spin rounded-full border-2 border-white/10 border-t-white/60" />
                  <p className="mt-4 text-[14px]">Loading...</p>
                </div>
              ) : series.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-20 text-white/30">
                  <p className="text-[14px]">No shows waiting to premiere</p>
                  <p className="mt-1 text-[12px] text-white/20">Shows that haven't aired yet appear here</p>
                </div>
              ) : (
                <div className="divide-y divide-white/5">
                  {series.map(s => (
                    <TvRow key={s.id} series={s} onClick={() => handleItemClick(s)} />
                  ))}
                </div>
              )}
            </>
          )}

          <div className="shrink-0" style={{ height: 'env(safe-area-inset-bottom, 12px)' }} />
        </div>
      </div>
    </div>,
    document.body,
  );
}
