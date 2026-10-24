import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useSwipeToDismiss } from '@/hooks/useSwipeToDismiss';
import { useModalHost, type ModalSource } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { cn } from '@/utils/cn';
import { apiFetch } from '../../apiClient';
import { apiUrl } from '@/urlHelpers';
import { NEUTRAL_GLASS } from '@/components/media/chipGlass';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface LatestTorrent {
  title: string;
  year: string | number;
  torrent_name: string;
  quality: string;
  resolution: string;
  source: string;
  size_gb: number;
  seeders: number;
  indexer: string;
  pub_date: string;
  age_days: number | null;
  imdb_id?: string;
  tmdb_id?: number | null;
  poster_path?: string | null;
  media_type?: string | null;
  display_title?: string | null;
  guid?: string;
  indexer_id?: number | null;
  info_hash?: string;
  download_url?: string;
}

// ---------------------------------------------------------------------------
// Grabbed state — persisted in localStorage so the green checkmark survives
// reloads and reopens. Keyed by guid (globally unique across indexers).
// ---------------------------------------------------------------------------

const GRABBED_STORAGE_KEY = 'latest:grabbed_guids';

function loadGrabbed(): Set<string> {
  try {
    const raw = localStorage.getItem(GRABBED_STORAGE_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr) ? arr : []);
  } catch {
    return new Set();
  }
}

function saveGrabbed(set: Set<string>) {
  try {
    localStorage.setItem(GRABBED_STORAGE_KEY, JSON.stringify(Array.from(set)));
  } catch { /* ignore quota errors */ }
}

type SortMode = 'recent' | 'seeders' | 'size';

const SORT_LABEL: Record<SortMode, string> = {
  recent: 'Newest',
  seeders: 'Seeders',
  size: 'Size',
};

const EXIT_DURATION = 400;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const QUALITY_COLORS: Record<string, string> = {
  '2160p Remux':  'bg-emerald-500/20 text-emerald-300/90 border-emerald-400/30',
  '2160p':        'bg-sky-500/20 text-sky-300/90 border-sky-400/30',
  '1080p Remux':  'bg-blue-500/20 text-blue-300/90 border-blue-400/30',
  '1080p':        'bg-orange-500/20 text-orange-300/90 border-orange-400/30',
  '720p':         'bg-violet-500/20 text-violet-300/90 border-violet-400/30',
};
const DEFAULT_BADGE = 'bg-white/10 text-white/80 border-white/20';

function badgeColor(quality: string): string {
  // Match common forms — WEBDL-2160p, 2160p Remux, plain 1080p, etc.
  if (QUALITY_COLORS[quality]) return QUALITY_COLORS[quality];
  const up = quality.toUpperCase();
  if (up.includes('2160P') && up.includes('REMUX')) return QUALITY_COLORS['2160p Remux']!;
  if (up.includes('2160P')) return QUALITY_COLORS['2160p']!;
  if (up.includes('1080P') && up.includes('REMUX')) return QUALITY_COLORS['1080p Remux']!;
  if (up.includes('1080P')) return QUALITY_COLORS['1080p']!;
  if (up.includes('720P')) return QUALITY_COLORS['720p']!;
  return DEFAULT_BADGE;
}

function ageLabel(ageDays: number | null, pubDate: string): string {
  if (ageDays === null && !pubDate) return '';
  // Same-day items report age_days === 0, but the user expects more
  // precision. Look at hours from the raw pub_date when we can.
  if (pubDate) {
    const diffMs = Date.now() - new Date(pubDate).getTime();
    if (!Number.isNaN(diffMs) && diffMs >= 0) {
      const hours = Math.floor(diffMs / 3_600_000);
      if (hours < 1) {
        const mins = Math.max(1, Math.floor(diffMs / 60_000));
        return `${mins}m ago`;
      }
      if (hours < 24) return `${hours}h ago`;
      const days = Math.floor(hours / 24);
      if (days < 30) return `${days}d ago`;
      const months = Math.floor(days / 30);
      return `${months}mo ago`;
    }
  }
  if (ageDays === null) return '';
  if (ageDays === 0) return 'Today';
  if (ageDays === 1) return '1d ago';
  if (ageDays < 30) return `${ageDays}d ago`;
  const months = Math.floor(ageDays / 30);
  return `${months}mo ago`;
}

function fmtSize(gb: number): string {
  if (!gb) return '';
  if (gb < 1) return `${Math.round(gb * 1024)} MB`;
  return `${gb.toFixed(1)} GB`;
}

function pubTs(pub: string): number {
  if (!pub) return -Infinity;
  const t = new Date(pub).getTime();
  return Number.isNaN(t) ? -Infinity : t;
}

function sortTorrents(items: LatestTorrent[], sort: SortMode): LatestTorrent[] {
  const sorted = [...items];
  switch (sort) {
    case 'recent':
      // Use the full pub_date timestamp — age_days is day-granularity
      // and would shuffle items uploaded the same day.
      sorted.sort((a, b) => pubTs(b.pub_date) - pubTs(a.pub_date));
      break;
    case 'seeders':
      sorted.sort((a, b) => (b.seeders ?? 0) - (a.seeders ?? 0));
      break;
    case 'size':
      sorted.sort((a, b) => (b.size_gb ?? 0) - (a.size_gb ?? 0));
      break;
  }
  return sorted;
}

function posterUrl(item: LatestTorrent): string | null {
  if (!item.poster_path) return null;
  // TMDb relative path → through our /api/img/tmdb proxy
  return apiUrl(`/api/img/tmdb?path=${encodeURIComponent(item.poster_path)}&size=w185`);
}

/**
 * Build a deterministic gradient backdrop for poster placeholders so missing
 * artwork still looks intentional. Hue is derived from the title hash so the
 * same torrent always renders with the same color across reloads.
 */
function placeholderGradient(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  const hue = hash % 360;
  return `linear-gradient(135deg, hsl(${hue}, 38%, 22%) 0%, hsl(${(hue + 40) % 360}, 32%, 14%) 100%)`;
}

function placeholderInitials(item: LatestTorrent): string {
  const src = (item.display_title || item.title || item.torrent_name || '?').trim();
  // Use up to 2 words' first letters — e.g. "Garden Makeovers" → "GM"
  const words = src.split(/\s+/).filter(w => /^[A-Za-z0-9]/.test(w));
  if (words.length === 0) return src.slice(0, 2).toUpperCase();
  if (words.length === 1) return words[0]!.slice(0, 2).toUpperCase();
  return (words[0]![0]! + words[1]![0]!).toUpperCase();
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

type GrabState = 'idle' | 'grabbing' | 'grabbed' | 'error';

function LatestRow({
  item,
  onOpen,
  initialGrabbed,
  onGrabbed,
  onGrabError,
}: {
  item: LatestTorrent;
  onOpen: (item: LatestTorrent) => void;
  initialGrabbed: boolean;
  onGrabbed: (guid: string) => void;
  onGrabError: (message: string) => void;
}) {
  const poster = posterUrl(item);
  const hasTmdb = !!item.tmdb_id;
  const [grabState, setGrabState] = useState<GrabState>(initialGrabbed ? 'grabbed' : 'idle');

  const canGrab = !!(item.guid && item.indexer_id != null);

  const handleGrab = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!canGrab || grabState === 'grabbing' || grabState === 'grabbed') return;
    setGrabState('grabbing');
    try {
      const resp = await apiFetch('/api/indexer/grab', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          guid: item.guid,
          indexer_id: item.indexer_id,
          tmdb_id: item.tmdb_id ?? undefined,
          title: item.title,                    // clean title for Radarr add
          year: item.year,
          torrent_name: item.torrent_name,      // raw name for cache match
          info_hash: item.info_hash,
          download_url: item.download_url,
          // Used by the push_release fallback in case Radarr's cache lookup
          // misses (e.g. when Radarr is on Jackett and we're on Prowlarr).
          pub_date: item.pub_date,
          size_gb: item.size_gb,
          indexer: item.indexer,
        }),
      });
      if (resp.ok) {
        setGrabState('grabbed');
        onGrabbed(item.guid!);
      } else {
        setGrabState('error');
        // Surface the backend's explanation — typically a Radarr rejection
        // reason ("not enough seeders", "BR-DISK not wanted in profile",
        // etc.). Falls back to a generic message on a parse failure.
        let msg = 'Radarr could not grab this release';
        try {
          const body = await resp.json();
          if (body?.error) msg = body.error;
        } catch { /* keep default */ }
        onGrabError(msg);
        setTimeout(() => setGrabState('idle'), 3500);
      }
    } catch {
      setGrabState('error');
      onGrabError('Network error — Radarr unreachable');
      setTimeout(() => setGrabState('idle'), 3500);
    }
  }, [item, canGrab, grabState, onGrabbed, onGrabError]);

  return (
    <div
      className={cn(
        'px-4 py-4 flex items-stretch gap-4',
        hasTmdb ? 'cursor-pointer active:bg-white/[0.03]' : '',
      )}
      onClick={hasTmdb ? () => onOpen(item) : undefined}
    >
      {/* Poster — bigger (75×112), with tracker overlay top-left. */}
      <div
        className="relative shrink-0 overflow-hidden rounded-lg"
        style={{
          width: '75px',
          height: '112px',
          aspectRatio: '2 / 3',
          background: poster ? 'rgba(255,255,255,0.05)' : placeholderGradient(item.title || item.torrent_name || ''),
        }}
      >
        {poster ? (
          <img
            src={poster}
            alt={item.title || item.torrent_name}
            loading="lazy"
            draggable={false}
            className="h-full w-full object-cover"
          />
        ) : (
          <>
            <div className="absolute left-0 top-0 right-0 flex justify-between px-1.5 py-1 opacity-30">
              {Array.from({ length: 5 }).map((_, i) => (
                <span key={`t${i}`} className="block h-1 w-1 rounded-sm bg-white/50" />
              ))}
            </div>
            <div className="absolute left-0 bottom-0 right-0 flex justify-between px-1.5 py-1 opacity-30">
              {Array.from({ length: 5 }).map((_, i) => (
                <span key={`b${i}`} className="block h-1 w-1 rounded-sm bg-white/50" />
              ))}
            </div>
            <div className="absolute inset-0 flex items-center justify-center">
              <span
                className="text-[22px] font-bold tracking-wide text-white/85"
                style={{ textShadow: '0 1px 4px rgba(0,0,0,0.4)' }}
              >
                {placeholderInitials(item)}
              </span>
            </div>
          </>
        )}

        {/* Tracker overlay — top-left, neutral glass (v173, Designer), blurred for legibility */}
        {item.indexer && (
          <span
            className="absolute left-1 top-1 rounded-md px-1.5 py-0.5 text-[12px] font-bold"
            style={{
              background: NEUTRAL_GLASS.bg,
              color: NEUTRAL_GLASS.fg,
              border: `1px solid ${NEUTRAL_GLASS.border}`,
              backdropFilter: 'blur(8px)',
              WebkitBackdropFilter: 'blur(8px)',
            }}
          >
            {item.indexer}
          </span>
        )}
      </div>

      {/* Title + metadata — larger sizes */}
      <div className="flex-1 min-w-0 flex flex-col justify-center gap-1.5">
        <p
          className="text-[16px] font-semibold text-white/95 leading-snug line-clamp-2"
          title={item.torrent_name}
        >
          {item.title || item.torrent_name}
          {item.year ? <span className="text-white/45 font-normal"> ({item.year})</span> : null}
        </p>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[12.5px] text-white/55">
          <span className={cn('rounded-md border px-2 py-0.5 text-[12px] font-semibold whitespace-nowrap', badgeColor(item.quality))}>
            {item.quality}
          </span>
          {item.size_gb > 0 && <span>{fmtSize(item.size_gb)}</span>}
          {item.seeders > 0 && (
            <span className="inline-flex items-center gap-1 text-emerald-400/85">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="6" /></svg>
              {item.seeders}
            </span>
          )}
        </div>
        <p className="text-[11.5px] text-white/35">{ageLabel(item.age_days, item.pub_date)}</p>
      </div>

      {/* Right-side action column — was the tracker pill, now the grab button.
          States: idle (download arrow) → grabbing (spinner) → grabbed (green ✓).
          Grabbed is sticky (persisted to localStorage) so the user can tell
          which releases they already pulled, across reloads. */}
      <div className="flex shrink-0 items-center">
        <button
          type="button"
          onClick={handleGrab}
          disabled={!canGrab || grabState === 'grabbing' || grabState === 'grabbed'}
          aria-label={
            grabState === 'grabbed' ? 'Already grabbed'
            : grabState === 'grabbing' ? 'Grabbing release'
            : grabState === 'error' ? 'Grab failed — tap to retry'
            : 'Grab release'
          }
          className={cn(
            'flex h-11 w-11 items-center justify-center rounded-full transition-all',
            grabState === 'grabbed'
              ? 'bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-400/40'
              : grabState === 'error'
                ? 'bg-rose-500/15 text-rose-300 ring-1 ring-rose-400/40'
                : grabState === 'grabbing'
                  ? 'bg-white/10 text-white/70'
                  : canGrab
                    ? 'bg-white/8 text-white/75 active:bg-white/15 active:scale-95'
                    : 'bg-white/4 text-white/25',
          )}
        >
          {grabState === 'grabbed' ? (
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="20 6 9 17 4 12" />
            </svg>
          ) : grabState === 'grabbing' ? (
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/15 border-t-white/80" />
          ) : grabState === 'error' ? (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          ) : (
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
          )}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sheet
// ---------------------------------------------------------------------------

interface LatestSheetProps {
  open: boolean;
  onClose: () => void;
}

export function LatestSheet({ open, onClose }: LatestSheetProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const { openMovie, openTv } = useModalHost();

  const [phase, setPhase] = useState<'entering' | 'visible' | 'exiting' | 'gone'>('gone');
  const [items, setItems] = useState<LatestTorrent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState<SortMode>('recent');
  const [trackerFilter, setTrackerFilter] = useState<string>('');
  const [behindModal, setBehindModal] = useState(false);
  // Grabbed guids — hydrated from localStorage on first open so the green
  // checkmark persists across reloads.
  const [grabbedGuids, setGrabbedGuids] = useState<Set<string>>(() => loadGrabbed());
  // Toast for grab errors (e.g. Radarr rejection reasons)
  const [grabError, setGrabError] = useState<string | null>(null);

  const handleGrabbed = useCallback((guid: string) => {
    setGrabbedGuids(prev => {
      if (prev.has(guid)) return prev;
      const next = new Set(prev);
      next.add(guid);
      saveGrabbed(next);
      return next;
    });
  }, []);

  const handleGrabError = useCallback((message: string) => {
    setGrabError(message);
    setTimeout(() => setGrabError(null), 4500);
  }, []);

  // Listen for modal close to restore the sheet (matches UpgradesSheet pattern)
  useEffect(() => {
    if (!behindModal) return;
    const restore = () => setBehindModal(false);
    window.addEventListener('modal-closed', restore);
    return () => window.removeEventListener('modal-closed', restore);
  }, [behindModal]);

  const handleOpenItem = useCallback((item: LatestTorrent) => {
    if (!item.tmdb_id) return;
    setBehindModal(true);
    openModalForItem(
      { openMovie, openTv },
      {
        mediaType: (item.media_type === 'tv' ? 'tv' : 'movie') as 'movie' | 'tv',
        tmdbId: item.tmdb_id,
        ratingKey: null,
      },
      'now_available' as ModalSource,
    );
  }, [openMovie, openTv]);

  useEffect(() => {
    if (open) {
      setPhase('entering');
      requestAnimationFrame(() => {
        requestAnimationFrame(() => setPhase('visible'));
      });
    }
  }, [open]);

  useEffect(() => {
    if (phase !== 'visible') return;
    const prevBg = document.documentElement.style.background;
    document.documentElement.style.background = '#000';
    return () => { document.documentElement.style.background = prevBg; };
  }, [phase]);

  const fetchLatest = useCallback(() => {
    setLoading(true);
    setError(null);
    // 300 = the per-search ceiling Prowlarr returns; covers a deep window
    // without paginating. Backend caches for 5min so repeated opens are free.
    apiFetch('/api/indexer/latest?limit=300')
      .then(r => {
        if (!r.ok) throw new Error(r.status === 503 ? 'No trackers are on.' : 'Failed to load releases.');
        return r.json();
      })
      .then(data => {
        setItems(Array.isArray(data.results) ? data.results : []);
        setLoading(false);
      })
      .catch(err => {
        setError(err?.message || 'Failed to load releases.');
        setItems([]);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!open) return;
    fetchLatest();
  }, [open, fetchLatest]);

  const handleDismiss = useCallback(() => {
    setPhase('exiting');
    setTimeout(() => {
      setPhase('gone');
      onClose();
    }, EXIT_DURATION);
  }, [onClose]);

  const { handlers: swipeHandlers } = useSwipeToDismiss({
    scrollRef,
    onDismiss: handleDismiss,
    enabled: phase === 'visible',
  });

  // Grab-handle drag-to-dismiss (same as UpgradesSheet)
  const handleDragRef = useRef({ active: false, startY: 0 });

  const onHandleTouchStart = useCallback((e: React.TouchEvent) => {
    if (phase !== 'visible') return;
    handleDragRef.current = { active: true, startY: e.touches[0]!.clientY };
  }, [phase]);

  const onHandleTouchMove = useCallback((e: React.TouchEvent) => {
    if (!handleDragRef.current.active) return;
    const dy = e.touches[0]!.clientY - handleDragRef.current.startY;
    if (dy < 0) return;
    e.preventDefault();
    const card = cardRef.current;
    const overlay = card?.closest('[data-modal]') as HTMLElement | null;
    if (card) {
      card.style.transform = `translate3d(0, ${dy * 0.6}px, 0)`;
      card.style.transition = 'none';
    }
    if (overlay) {
      const progress = Math.min(dy / (window.innerHeight * 0.3), 1);
      overlay.style.backgroundColor = `rgba(0,0,0,${0.6 * (1 - progress * 0.7)})`;
    }
  }, []);

  const onHandleTouchEnd = useCallback(() => {
    if (!handleDragRef.current.active) return;
    handleDragRef.current.active = false;
    const card = cardRef.current;
    if (!card) return;
    const current = new DOMMatrix(getComputedStyle(card).transform);
    const dy = current.m42;
    if (dy > window.innerHeight * 0.2) {
      card.style.transform = `translate3d(0, ${window.innerHeight}px, 0)`;
      card.style.transition = 'transform 300ms cubic-bezier(0.22, 1, 0.36, 1)';
      const overlay = card.closest('[data-modal]') as HTMLElement | null;
      if (overlay) {
        overlay.style.transition = 'opacity 300ms ease-out';
        overlay.style.opacity = '0';
      }
      setTimeout(() => handleDismiss(), 280);
    } else {
      card.style.transform = 'translate3d(0, 0, 0)';
      card.style.transition = 'transform 300ms cubic-bezier(0.25, 1, 0.5, 1)';
      const overlay = card.closest('[data-modal]') as HTMLElement | null;
      if (overlay) overlay.style.backgroundColor = 'rgba(0,0,0,0.6)';
      setTimeout(() => { if (card) card.style.transition = ''; }, 310);
    }
  }, [handleDismiss]);

  // Tracker chip options derived from results
  const trackers = useMemo(() => {
    const counts = new Map<string, number>();
    for (const it of items) {
      const t = (it.indexer || '').trim();
      if (!t) continue;
      counts.set(t, (counts.get(t) ?? 0) + 1);
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([name, count]) => ({ name, count }));
  }, [items]);

  // Reset filter if the active tracker disappears from results
  useEffect(() => {
    if (trackerFilter && !trackers.some(t => t.name === trackerFilter)) {
      setTrackerFilter('');
    }
  }, [trackers, trackerFilter]);

  const visibleItems = useMemo(() => {
    const filtered = trackerFilter ? items.filter(i => i.indexer === trackerFilter) : items;
    return sortTorrents(filtered, sort);
  }, [items, sort, trackerFilter]);

  if (phase === 'gone') return null;

  const ENTER_DUR = 600;
  const EXIT_DUR = 400;
  const springBezier = 'cubic-bezier(0.22, 1, 0.36, 1)';

  const cardTransform =
    phase === 'entering' ? 'translate3d(0, 100%, 0)'
    : phase === 'visible' ? 'translate3d(0, 0, 0)'
    : 'translate3d(0, 100%, 0)';
  const cardTransition =
    phase === 'exiting'
      ? `transform ${EXIT_DUR}ms cubic-bezier(0.4, 0, 1, 1)`
      : `transform ${ENTER_DUR}ms ${springBezier}`;

  return createPortal(
    <div className="luma-bleed"
      data-modal="LatestSheet"
      style={{
        position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
        zIndex: behindModal ? 9999 : 10000,
        backgroundColor: 'rgba(0,0,0,0.6)',
        opacity: phase === 'entering' ? 0 : 1,
        transition: 'opacity 250ms ease-out',
      }}
    >
      <div
        ref={cardRef}
        style={{
          position: 'absolute',
          top: 'max(env(safe-area-inset-top, 0px), 6px)',
          left: 0, right: 0, bottom: 0,
          borderTopLeftRadius: '14px',
          borderTopRightRadius: '14px',
          overflow: 'hidden',
          backgroundColor: 'var(--color-kiosk-black)',
          display: 'flex',
          flexDirection: 'column',
          transform: cardTransform,
          transition: cardTransition,
          willChange: 'transform',
          backfaceVisibility: 'hidden',
        }}
      >
        {/* Grab handle */}
        <div
          data-sheet-handle
          onTouchStart={onHandleTouchStart}
          onTouchMove={onHandleTouchMove}
          onTouchEnd={onHandleTouchEnd}
          style={{ flexShrink: 0, display: 'flex', justifyContent: 'center', padding: '12px 0 8px', cursor: 'grab', touchAction: 'none' }}
        >
          <div style={{ width: 40, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)' }} />
        </div>

        {/* Header — title + refresh + sort dropdown */}
        <div className="shrink-0 px-4 pb-3 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-baseline gap-2">
              <h2 className="text-[18px] font-semibold text-white/90">Latest</h2>
              <span className="text-[12px] text-white/35">
                {loading ? 'Loading…' : `${visibleItems.length} of ${items.length}`}
              </span>
            </div>
            <button
              onClick={fetchLatest}
              disabled={loading}
              className="rounded-xl px-4 py-2 text-[13px] font-semibold whitespace-nowrap"
              style={{
                background: loading ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.12)',
                color: loading ? 'rgba(255,255,255,0.5)' : 'rgba(255,255,255,0.7)',
                cursor: loading ? 'not-allowed' : 'pointer',
              }}
            >
              {loading ? '…' : 'Refresh'}
            </button>
          </div>

          {/* Sort — inline pill row. A native <select> rendered a white-on-
              white menu in this dark UI (browsers ignore most <option> styles),
              so we use buttons instead. */}
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-semibold text-white/35 shrink-0">Sort</span>
            <div className="flex items-center gap-1.5 flex-1">
              {(Object.keys(SORT_LABEL) as SortMode[]).map(v => {
                const active = sort === v;
                return (
                  <button
                    key={v}
                    type="button"
                    onClick={() => setSort(v)}
                    className={cn(
                      'rounded-lg px-3 py-1.5 text-[13px] font-semibold transition-colors',
                      active
                        ? 'bg-white/15 text-white/95'
                        : 'bg-white/5 text-white/55 hover:text-white/80',
                    )}
                  >
                    {SORT_LABEL[v]}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Tracker filter chips — horizontal scroll, sticky-ish */}
          {trackers.length > 0 && (
            <div
              className="flex items-center gap-2 overflow-x-auto -mx-4 px-4"
              style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}
            >
              <button
                type="button"
                onClick={() => setTrackerFilter('')}
                className={cn(
                  'shrink-0 rounded-full border px-3 py-1.5 text-[12px] font-semibold whitespace-nowrap transition-colors',
                  trackerFilter === ''
                    ? 'border-white/20 bg-white/15 text-white/90'
                    : 'border-white/8 bg-white/5 text-white/55',
                )}
              >
                All <span className="ml-1 text-white/35">{items.length}</span>
              </button>
              {trackers.map(t => {
                const active = trackerFilter === t.name;
                return (
                  <button
                    key={t.name}
                    type="button"
                    onClick={() => setTrackerFilter(active ? '' : t.name)}
                    className={cn(
                      'shrink-0 rounded-full border px-3 py-1.5 text-[12px] font-semibold whitespace-nowrap transition-colors',
                      active
                        ? 'border-white/20 bg-white/15 text-white/90'
                        : 'border-white/8 bg-white/5 text-white/55',
                    )}
                  >
                    {t.name}
                    <span className="ml-1 text-white/35">{t.count}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Scrollable list */}
        <div
          ref={scrollRef}
          data-scroll-container
          className="luma-scroll-y flex-1"
          style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}
          {...swipeHandlers}
        >
          {loading && items.length === 0 && (
            <div className="flex flex-col items-center justify-center py-20 text-white/30">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-white/10 border-t-white/60" />
              <p className="mt-4 text-[14px]">Querying indexers…</p>
            </div>
          )}

          {!loading && error && (
            <div className="flex flex-col items-center justify-center py-20 px-6 text-white/40 text-center">
              <p className="text-[14px]">{error}</p>
            </div>
          )}

          {!loading && !error && items.length === 0 && (
            <div className="flex flex-col items-center justify-center py-20 text-white/30">
              <p className="text-[14px]">No recent releases.</p>
            </div>
          )}

          {!loading && !error && visibleItems.length === 0 && items.length > 0 && (
            <div className="flex flex-col items-center justify-center py-20 text-white/30">
              <p className="text-[14px]">No releases from {trackerFilter}.</p>
            </div>
          )}

          {visibleItems.length > 0 && (
            <div className="divide-y divide-white/5">
              {visibleItems.map((item, idx) => (
                <LatestRow
                  key={`${item.guid || item.torrent_name}-${idx}`}
                  item={item}
                  onOpen={handleOpenItem}
                  initialGrabbed={!!(item.guid && grabbedGuids.has(item.guid))}
                  onGrabbed={handleGrabbed}
                  onGrabError={handleGrabError}
                />
              ))}
            </div>
          )}

          <div className="shrink-0" style={{ height: 'env(safe-area-inset-bottom, 12px)' }} />
        </div>
      </div>

      {/* Grab error toast — Radarr's rejection reason or network error */}
      {grabError && (
        <div
          style={{
            position: 'fixed',
            bottom: 'calc(env(safe-area-inset-bottom, 0px) + 24px)',
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 10003,
            maxWidth: 'calc(100vw - 32px)',
          }}
          className="rounded-xl border border-rose-500/30 bg-rose-500/20 px-5 py-3 text-[13px] font-medium text-rose-100 shadow-lg backdrop-blur-md text-center"
        >
          {grabError}
        </div>
      )}
    </div>,
    document.body,
  );
}
