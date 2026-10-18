/**
 * TvCalendarSheet — mobile week calendar of upcoming TV, fed by Sonarr.
 *
 * Mirrors the ActivitySheet/WatchlistSheet shell: slide-up, grab-handle
 * drag-to-dismiss, tap-outside to close. Inside, a Sun–Sat day strip with
 * per-day episode counts sits above the selected day's episode list.
 * Tapping a row opens the TV detail modal via the shared navigation util.
 */
import { useState, useEffect, useCallback, useRef, useMemo, memo } from 'react';
import { createPortal } from 'react-dom';
import { useModalHost, type ModalSource } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { posterUrl } from '@/utils/image';
import { cn } from '@/utils/cn';
import { apiFetch } from '../../apiClient';

const DISMISS_THRESHOLD = 120;
const ENTER_DURATION = 600;
const EXIT_DURATION = 400;
const SPRING = 'cubic-bezier(0.16, 1, 0.3, 1)';

const DAY_LETTERS = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];

// ---------------------------------------------------------------------------
// Types + date helpers
// ---------------------------------------------------------------------------

interface CalendarEpisode {
  id: string;
  seriesTitle: string;
  season: number | null;
  episode: number | null;
  episodeTitle: string;
  airDateUtc: string | null;
  network: string;
  posterUrl: string | null;
  tmdbId: number | null;
  hasFile: boolean;
}

/** Local YYYY-MM-DD — never toISOString(), which would shift across the date line. */
function localKey(d: Date): string {
  const m = `${d.getMonth() + 1}`.padStart(2, '0');
  const day = `${d.getDate()}`.padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

function startOfWeek(d: Date): Date {
  const out = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  out.setDate(out.getDate() - out.getDay()); // Sunday-start
  return out;
}

function addDays(d: Date, n: number): Date {
  const out = new Date(d);
  out.setDate(out.getDate() + n);
  return out;
}

function weekRangeLabel(start: Date): string {
  const end = addDays(start, 6);
  const sameMonth = start.getMonth() === end.getMonth();
  const s = start.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  const e = end.toLocaleDateString('en-US', sameMonth ? { day: 'numeric' } : { month: 'short', day: 'numeric' });
  return `${s} – ${e}`;
}

function airTime(iso: string | null): string {
  if (!iso) return '';
  const dt = new Date(iso);
  if (isNaN(dt.getTime())) return '';
  return dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

async function fetchWeek(start: Date): Promise<CalendarEpisode[]> {
  // Ask Sonarr for one extra day so episodes late on Saturday (UTC rollover)
  // still come back; the client buckets by local date and drops the overflow.
  const from = localKey(start);
  const to = localKey(addDays(start, 7));
  try {
    const resp = await apiFetch(`/api/sonarr/calendar?start=${from}&end=${to}&limit=200`);
    if (!resp.ok) return [];
    const data = await resp.json();
    if (!data.enabled) return [];
    return ((data.episodes ?? []) as Record<string, unknown>[]).map((e, i) => ({
      id: `cal-${(e.tvdb_id as number) ?? i}-${(e.season as number) ?? 0}-${(e.episode as number) ?? 0}-${i}`,
      seriesTitle: (e.series_title as string) ?? '',
      season: (e.season as number) ?? null,
      episode: (e.episode as number) ?? null,
      episodeTitle: (e.episode_title as string) ?? '',
      airDateUtc: (e.air_date_utc as string) ?? null,
      network: (e.network as string) ?? '',
      posterUrl: posterUrl((e.poster_url as string) || null, 'w342'),
      tmdbId: (e.tmdb_id as number) ?? null,
      hasFile: Boolean(e.has_file),
    }));
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Rows
// ---------------------------------------------------------------------------

const EpisodeRow = memo(function EpisodeRow({
  ep,
  onClick,
}: {
  ep: CalendarEpisode;
  onClick: () => void;
}) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const epLabel =
    ep.season != null && ep.episode != null
      ? `S${ep.season}E${`${ep.episode}`.padStart(2, '0')}`
      : '';

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!ep.tmdbId}
      className="flex w-full items-center gap-3 px-3 py-3 text-left active:bg-white/5 disabled:active:bg-transparent"
    >
      <div className="h-[84px] w-[56px] shrink-0 overflow-hidden rounded-lg bg-white/5">
        {ep.posterUrl ? (
          <img
            src={ep.posterUrl}
            alt={ep.seriesTitle}
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
          <div className="flex h-full w-full items-center justify-center text-[10px] text-white/20">
            No Poster
          </div>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[14px] font-medium text-white/90">{ep.seriesTitle}</p>
        <p className="mt-0.5 truncate text-[13px] text-white/40">
          {epLabel}
          {ep.episodeTitle ? `${epLabel ? ' · ' : ''}${ep.episodeTitle}` : ''}
        </p>
        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          {airTime(ep.airDateUtc) && (
            <span className="text-[12px] font-semibold text-blue-400">{airTime(ep.airDateUtc)}</span>
          )}
          {ep.network && <span className="text-[12px] text-white/30">{ep.network}</span>}
          {ep.hasFile && (
            <span className="rounded bg-green-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-green-400">
              Downloaded
            </span>
          )}
        </div>
      </div>
    </button>
  );
});

// ---------------------------------------------------------------------------
// Sheet
// ---------------------------------------------------------------------------

interface TvCalendarSheetProps {
  open: boolean;
  onClose: () => void;
}

export function TvCalendarSheet({ open, onClose }: TvCalendarSheetProps) {
  const { openMovie, openTv } = useModalHost();

  // --- Sheet animation (shared shell) ---
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });
  const [dragOffset, setDragOffset] = useState(0);
  const [phase, setPhase] = useState<'entering' | 'visible' | 'exiting' | 'gone'>('gone');
  const [behindModal, setBehindModal] = useState(false);

  useEffect(() => {
    if (open) {
      setPhase('entering');
      setDragOffset(0);
      setBehindModal(false);
      requestAnimationFrame(() => {
        requestAnimationFrame(() => setPhase('visible'));
      });
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prevOverflow; };
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

  useEffect(() => {
    if (!behindModal) return;
    const restore = () => setBehindModal(false);
    window.addEventListener('modal-closed', restore);
    return () => window.removeEventListener('modal-closed', restore);
  }, [behindModal]);

  // --- Week state ---
  // `todayKey` is captured once per open so an overnight session doesn't
  // silently mark the wrong chip as today.
  const [weekOffset, setWeekOffset] = useState(0);
  const [anchor, setAnchor] = useState(() => startOfWeek(new Date()));
  const [todayKey, setTodayKey] = useState(() => localKey(new Date()));
  const [selectedKey, setSelectedKey] = useState(() => localKey(new Date()));
  const [episodes, setEpisodes] = useState<CalendarEpisode[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!open) return;
    const now = new Date();
    setAnchor(startOfWeek(now));
    setTodayKey(localKey(now));
    setSelectedKey(localKey(now));
    setWeekOffset(0);
  }, [open]);

  const weekStart = useMemo(() => addDays(anchor, weekOffset * 7), [anchor, weekOffset]);
  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)), [weekStart]);
  const weekStartKey = localKey(weekStart);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    fetchWeek(weekStart).then((eps) => {
      if (cancelled) return;
      setEpisodes(eps);
      setLoading(false);
    });
    return () => { cancelled = true; };
    // weekStartKey is the stable identity of weekStart (a fresh Date each render).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, weekStartKey]);

  // Bucket by local air date; episodes outside the visible week are dropped.
  const byDay = useMemo(() => {
    const map = new Map<string, CalendarEpisode[]>();
    for (const ep of episodes) {
      if (!ep.airDateUtc) continue;
      const dt = new Date(ep.airDateUtc);
      if (isNaN(dt.getTime())) continue;
      const key = localKey(dt);
      const list = map.get(key);
      if (list) list.push(ep);
      else map.set(key, [ep]);
    }
    for (const list of map.values()) {
      list.sort((a, b) => (a.airDateUtc ?? '').localeCompare(b.airDateUtc ?? ''));
    }
    return map;
  }, [episodes]);

  // Keep the selection inside the displayed week when paging.
  useEffect(() => {
    const keys = days.map(localKey);
    if (!keys.includes(selectedKey)) setSelectedKey(keys.includes(todayKey) ? todayKey : keys[0]!);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [weekStartKey]);

  const selectedEpisodes = byDay.get(selectedKey) ?? [];
  const weekTotal = days.reduce((sum, d) => sum + (byDay.get(localKey(d))?.length ?? 0), 0);

  const handleEpisodeClick = useCallback((ep: CalendarEpisode) => {
    if (!ep.tmdbId) return;
    setBehindModal(true);
    openModalForItem(
      { openMovie, openTv },
      {
        mediaType: 'episode',
        tmdbId: ep.tmdbId,
        ratingKey: null,
        seasonNumber: ep.season,
        episodeNumber: ep.episode,
      },
      'tv_calendar' satisfies ModalSource,
    );
  }, [openMovie, openTv]);

  if (phase === 'gone' && !open) return null;

  const sheetTransform =
    dragOffset > 0 ? `translate3d(0, ${dragOffset}px, 0)`
      : phase === 'visible' ? 'translate3d(0, 0, 0)'
        : 'translate3d(0, 100%, 0)';

  const sheetTransition =
    dragOffset > 0 ? 'none'
      : phase === 'exiting' ? `transform ${EXIT_DURATION}ms cubic-bezier(0.4, 0, 1, 1)`
        : `transform ${ENTER_DURATION}ms ${SPRING}`;

  const selectedDate = new Date(`${selectedKey}T00:00:00`);
  const selectedLabel = isNaN(selectedDate.getTime())
    ? ''
    : selectedKey === todayKey
      ? 'Today'
      : selectedDate.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });

  return createPortal(
    <div
      data-sheet="TvCalendarSheet"
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

        {/* Header: title + week pager */}
        <div className="shrink-0 px-4 pb-3">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-[18px] font-bold text-white">TV calendar</h2>
              <p className="mt-0.5 text-[12px] text-white/35">
                {weekRangeLabel(weekStart)}
                {!loading && ` · ${weekTotal} episode${weekTotal === 1 ? '' : 's'}`}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              <button
                type="button"
                aria-label="Previous week"
                onClick={() => setWeekOffset((o) => o - 1)}
                className="flex h-9 w-9 items-center justify-center rounded-full bg-white/8 text-white/70 active:bg-white/15"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="15 18 9 12 15 6" />
                </svg>
              </button>
              {weekOffset !== 0 && (
                <button
                  type="button"
                  onClick={() => { setWeekOffset(0); setSelectedKey(todayKey); }}
                  className="rounded-full bg-white/8 px-3 py-2 text-[12px] font-semibold text-white/70 active:bg-white/15"
                >
                  Today
                </button>
              )}
              <button
                type="button"
                aria-label="Next week"
                onClick={() => setWeekOffset((o) => o + 1)}
                className="flex h-9 w-9 items-center justify-center rounded-full bg-white/8 text-white/70 active:bg-white/15"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              </button>
            </div>
          </div>
        </div>

        {/* Day strip */}
        <div className="shrink-0 px-2 pb-3">
          <div className="flex items-stretch gap-1">
            {days.map((d, i) => {
              const key = localKey(d);
              const count = byDay.get(key)?.length ?? 0;
              const isSelected = key === selectedKey;
              const isToday = key === todayKey;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => setSelectedKey(key)}
                  className={cn(
                    'flex flex-1 flex-col items-center gap-1 rounded-xl py-2 transition-colors',
                    isSelected ? 'bg-white/15' : 'active:bg-white/5',
                  )}
                >
                  <span className={cn('text-[11px] font-semibold',
                    isSelected ? 'text-white/70' : 'text-white/35')}>
                    {DAY_LETTERS[i]}
                  </span>
                  <span className={cn(
                    'flex h-8 w-8 items-center justify-center rounded-full text-[15px] font-semibold',
                    isToday ? 'bg-blue-500 text-white'
                      : isSelected ? 'text-white' : 'text-white/60',
                  )}>
                    {d.getDate()}
                  </span>
                  <span className={cn(
                    'block h-1.5 w-1.5 rounded-full',
                    count > 0 ? (isToday ? 'bg-blue-300' : 'bg-blue-400') : 'bg-transparent',
                  )} />
                </button>
              );
            })}
          </div>
        </div>

        <div className="shrink-0 border-t border-white/8" />

        {/* Selected day's episodes */}
        <div
          data-scroll-container
          className="luma-scroll-y flex-1 px-1"
          style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}
        >
          {selectedLabel && (
            <p className="px-3 pt-3 pb-1 text-[13px] font-semibold text-white/45">{selectedLabel}</p>
          )}
          {loading ? (
            <div className="flex flex-col items-center justify-center py-20 text-white/30">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-white/10 border-t-white/60" />
              <p className="mt-4 text-[14px]">Loading...</p>
            </div>
          ) : selectedEpisodes.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-white/30">
              <p className="text-[14px]">Nothing airing this day</p>
            </div>
          ) : (
            <div className="divide-y divide-white/5">
              {selectedEpisodes.map((ep) => (
                <EpisodeRow key={ep.id} ep={ep} onClick={() => handleEpisodeClick(ep)} />
              ))}
            </div>
          )}
          <div className="shrink-0" style={{ height: 'env(safe-area-inset-bottom, 12px)' }} />
        </div>
      </div>
    </div>,
    document.body,
  );
}
