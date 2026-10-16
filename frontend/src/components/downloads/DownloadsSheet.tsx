import { useState, useEffect, useCallback, useRef, memo } from 'react';
import { createPortal } from 'react-dom';
import { useModalHost, type ModalSource } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { type DownloadItem } from '@/hooks/useNotifications';
import { useNotificationsContext } from '@/contexts/NotificationsContext';
import { posterUrl } from '@/utils/image';
import { cn } from '@/utils/cn';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const DISMISS_THRESHOLD = 120;
const ENTER_DURATION = 600;
const EXIT_DURATION = 400;
const SPRING = 'cubic-bezier(0.16, 1, 0.3, 1)';

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

function downloadStatusLabel(dl: DownloadItem): { text: string; color: string } {
  const pct = Math.round(dl.progress * 100);
  switch (dl.status) {
    case 'copying': return { text: `Adding to library... ${pct}%`, color: 'text-green-400' };
    case 'importing': return { text: 'Processing...', color: 'text-green-400' };
    case 'warning': return { text: 'Stalled', color: 'text-amber-400' };
    case 'queued': return { text: 'Queued', color: 'text-white/50' };
    case 'paused': return { text: 'Paused', color: 'text-white/50' };
    case 'failed': return { text: 'Failed', color: 'text-red-400' };
    default:
      if (pct >= 100) return { text: 'Processing...', color: 'text-green-400' };
      return { text: `Downloading... ${pct}%`, color: 'text-blue-400' };
  }
}

function isProcessingState(dl: DownloadItem): boolean {
  return dl.status === 'importing' || (dl.progress >= 1.0 && dl.status !== 'copying');
}

function downloadBarColor(status: string): string {
  if (status === 'copying') return 'bg-green-500';
  if (status === 'warning') return 'bg-amber-500';
  if (status === 'failed') return 'bg-red-500';
  return 'bg-blue-500';
}

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------


async function removeQbitDownload(hash: string, deleteFiles: boolean): Promise<{ ok: boolean; error?: string }> {
  if (!hash) return { ok: false, error: 'Missing torrent hash' };
  try {
    const q = deleteFiles ? '?deleteFiles=true' : '?deleteFiles=false';
    const resp = await fetch(`/api/downloads/remove/${encodeURIComponent(hash)}${q}`, { method: 'DELETE' });
    const data = await resp.json().catch(() => ({})) as { success?: boolean; error?: string };
    if (!resp.ok || data.success === false) {
      return { ok: false, error: data.error || `Remove failed (${resp.status})` };
    }
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'Network error' };
  }
}

async function fetchHistory(): Promise<HistoryItem[]> {
  const resp = await fetch('/api/downloads/history?limit=30');
  if (!resp.ok) return [];
  const data = await resp.json();
  const raw = ((data.history ?? []) as Record<string, unknown>[]);
  return raw.map((m, i) => {
    const pp = (m.poster_path as string) || null;
    return {
      id: `hist-${(m.torrent_hash as string) ?? i}`,
      title: (m.tmdb_title as string) ?? '',
      year: (m.tmdb_year as string) ?? '',
      posterUrl: posterUrl(pp, 'w342'),
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
// Row components
// ---------------------------------------------------------------------------

const HistRow = memo(function HistRow({ item, onClick }: { item: HistoryItem; onClick: () => void }) {
  const [imgLoaded, setImgLoaded] = useState(false);

  return (
    <button type="button" onClick={onClick}
      className="flex w-full items-center gap-3 px-3 py-3 text-left active:bg-white/5"
    >
      <div className="h-[120px] w-[80px] shrink-0 overflow-hidden rounded-lg bg-white/5">
        {item.posterUrl ? (
          <img src={item.posterUrl} alt={item.title} loading="lazy" draggable={false}
            className={cn('h-full w-full object-cover transition-opacity duration-300', imgLoaded ? 'opacity-100' : 'opacity-0')}
            onLoad={() => setImgLoaded(true)}
            onError={(e) => { (e.target as HTMLImageElement).style.visibility = 'hidden'; }}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-white/20 text-[11px]">No Poster</div>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[14px] font-medium text-white/90">
          {item.title}
          {item.year && <span className="ml-2 font-normal text-white/40">({item.year})</span>}
        </p>
        {item.torrentName && (
          <p className="mt-1 text-[13px] text-white/30 break-all line-clamp-2">{item.torrentName}</p>
        )}
        <div className="mt-1.5 flex items-center gap-2 text-[13px]">
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
          <span className={cn(
            'rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase',
            item.mediaType === 'tv' ? 'bg-purple-500/20 text-purple-400' : 'bg-blue-500/20 text-blue-400',
          )}>
            {item.mediaType === 'tv' ? 'TV' : 'Movie'}
          </span>
          {item.tracker && (
            <span className="rounded px-1.5 py-0.5 text-[11px] font-semibold bg-amber-500/15 text-amber-400 border border-amber-500/20">
              {item.tracker}
            </span>
          )}
        </div>
        <p className="mt-1 text-[12px] text-white/25">{relativeTime(item.completedTimestamp)}</p>
      </div>
    </button>
  );
});

const ActiveDlRow = memo(function ActiveDlRow({
  dl,
  onRemoved,
}: {
  dl: DownloadItem;
  onRemoved: (hash: string) => void;
}) {
  const poster = posterUrl(dl.posterPath, 'w185');
  const pct = Math.round(dl.progress * 100);
  const { text: statusText, color: statusColor } = downloadStatusLabel(dl);
  const processing = isProcessingState(dl);
  const barColor = downloadBarColor(dl.status);
  // Expand filename (or title when it IS the torrent name) to reveal remove actions —
  // no always-visible Remove… button (matches ActivityFeed).
  const [expanded, setExpanded] = useState(false);
  const [confirmFiles, setConfirmFiles] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const hasCleanTitle = !!dl.title && dl.title !== dl.name;
  const toggleExpand = () => {
    setExpanded((prev) => {
      if (prev) {
        setConfirmFiles(false);
        setErr(null);
      }
      return !prev;
    });
  };

  const run = async (deleteFiles: boolean) => {
    if (!dl.hash || busy) return;
    setBusy(true);
    setErr(null);
    const res = await removeQbitDownload(dl.hash, deleteFiles);
    setBusy(false);
    if (!res.ok) {
      setErr(res.error || 'Remove failed');
      setConfirmFiles(false);
      return;
    }
    onRemoved(dl.hash);
  };

  return (
    <div className="flex w-full items-start gap-3 px-3 py-3 text-left">
      <div className="h-[120px] w-[80px] shrink-0 overflow-hidden rounded-lg bg-white/5 relative">
        {poster ? (
          <img src={poster} alt={dl.title} loading="lazy" draggable={false} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-white/20 text-[11px]">No Poster</div>
        )}
        <span className={cn('absolute left-1.5 top-1.5 block h-2.5 w-2.5 rounded-full animate-pulse',
          processing || dl.status === 'copying' ? 'bg-green-400' : 'bg-blue-400')}
          style={{ boxShadow: processing || dl.status === 'copying' ? '0 0 6px rgba(74,222,128,0.6)' : '0 0 6px rgba(96,165,250,0.6)' }}
        />
      </div>
      <div className="min-w-0 flex-1">
        {hasCleanTitle ? (
          <p className="truncate text-[14px] font-medium text-white/90">
            {dl.title}
            {dl.year > 0 && <span className="ml-2 font-normal text-white/40">({dl.year})</span>}
          </p>
        ) : (
          <p
            role="button"
            onClick={toggleExpand}
            title={dl.name || dl.title}
            className={`text-[14px] font-medium text-white/90 cursor-pointer ${expanded ? 'whitespace-normal break-all' : 'truncate'}`}
          >
            {dl.name || dl.title || 'Unknown'}
            {!expanded && dl.year > 0 ? <span className="ml-2 font-normal text-white/40">({dl.year})</span> : null}
          </p>
        )}
        {hasCleanTitle && dl.name ? (
          <p
            role="button"
            onClick={toggleExpand}
            title={dl.name}
            className={`mt-0.5 text-[12px] text-white/30 cursor-pointer hover:text-white/50 ${expanded ? 'whitespace-normal break-all' : 'truncate'}`}
          >
            {dl.name}
          </p>
        ) : null}
        <div className="mt-1 flex items-center gap-2">
          <p className={`text-[13px] ${statusColor}`}>{statusText}</p>
          {dl.tracker && (
            <span className="rounded px-1.5 py-0.5 text-[11px] font-semibold bg-amber-500/15 text-amber-400 border border-amber-500/20">
              {dl.tracker}
            </span>
          )}
        </div>
        {!processing && (
          <div className="mt-1.5 h-[1.5px] w-full overflow-hidden rounded-full bg-white/10">
            <div className={`h-full rounded-full ${barColor} transition-[width] duration-500`} style={{ width: `${pct}%` }} />
          </div>
        )}
        {dl.eta && dl.status === 'downloading' && (
          <p className="mt-1 text-[12px] text-white/30">{dl.eta} remaining</p>
        )}
        {dl.hash && expanded ? (
          <div className="mt-2">
            {confirmFiles ? (
              <div className="rounded-xl px-3 py-2.5" style={{ background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.35)' }}>
                <p className="text-[12px] leading-snug text-red-200/90">
                  Delete torrent <span className="font-semibold">and files</span> from disk? This cannot be undone.
                </p>
                <div className="mt-2 flex gap-2">
                  <button type="button" disabled={busy} onClick={() => setConfirmFiles(false)}
                    className="flex-1 rounded-lg px-2.5 py-2 text-[12px] font-medium text-white/70 active:bg-white/10"
                    style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)' }}>
                    Cancel
                  </button>
                  <button type="button" disabled={busy} onClick={() => void run(true)}
                    className="flex-1 rounded-lg px-2.5 py-2 text-[12px] font-semibold text-white active:opacity-80"
                    style={{ background: 'rgba(239,68,68,0.85)' }}>
                    {busy ? 'Deleting…' : 'Delete torrent + files'}
                  </button>
                </div>
              </div>
            ) : (
              <button type="button" disabled={busy} onClick={() => { setConfirmFiles(true); setErr(null); }}
                className="w-full rounded-lg px-3 py-2 text-left text-[12px] font-medium text-red-300/90 active:bg-red-500/15"
                style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.28)' }}>
                Delete torrent + files
                <span className="mt-0.5 block text-[10px] font-normal text-red-300/50">Removes transfer and deletes files</span>
              </button>
            )}
            {err && !confirmFiles ? <p className="mt-1.5 text-[11px] text-red-400/90">{err}</p> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
});

// ---------------------------------------------------------------------------
// DownloadsSheet
// ---------------------------------------------------------------------------

interface DownloadsSheetProps {
  open: boolean;
  onClose: () => void;
}

export function DownloadsSheet({ open, onClose }: DownloadsSheetProps) {
  const { openMovie, openTv } = useModalHost();

  // --- Sheet animation ---
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
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const { downloads } = useNotificationsContext();
  const [hiddenHashes, setHiddenHashes] = useState<Set<string>>(new Set());
  const visibleDownloads = downloads.filter((d) => !d.hash || !hiddenHashes.has(d.hash));
  const handleRemoved = useCallback((hash: string) => {
    setHiddenHashes((prev) => {
      const next = new Set(prev);
      next.add(hash);
      return next;
    });
  }, []);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    fetchHistory().then(d => { setHistory(d); setLoading(false); });
  }, [open]);

  // --- Modal ---
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
      data-sheet="DownloadsSheet"
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

        {/* Title */}
        <div className="shrink-0 px-4 pb-3">
          <h2 className="text-[18px] font-bold text-white">Downloads</h2>
        </div>

        {/* Scrollable content */}
        <div
          data-scroll-container
          className="luma-scroll-y flex-1 px-1"
          style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}
        >
          {/* Active downloads */}
          {visibleDownloads.length > 0 && (
            <div className="divide-y divide-white/5">
              {visibleDownloads.map((dl, i) => (
                <ActiveDlRow key={dl.hash || `dl-${dl.tmdbId ?? dl.title}-${i}`} dl={dl} onRemoved={handleRemoved} />
              ))}
            </div>
          )}

          {visibleDownloads.length > 0 && history.length > 0 && !loading && (
            <div className="my-2 border-t border-white/8" />
          )}

          {loading ? (
            <div className="flex flex-col items-center justify-center py-20 text-white/30">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-white/10 border-t-white/60" />
              <p className="mt-4 text-[14px]">Loading...</p>
            </div>
          ) : history.length === 0 && visibleDownloads.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-white/30">
              <p className="text-[14px]">No download history</p>
            </div>
          ) : (
            <div className="divide-y divide-white/5">
              {history.map(item => (
                <HistRow key={item.id} item={item} onClick={() => handleItemClick(item)} />
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
