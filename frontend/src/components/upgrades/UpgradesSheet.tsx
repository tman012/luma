import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useModalHost, type ModalSource } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { useSwipeToDismiss } from '@/hooks/useSwipeToDismiss';
import { cn } from '@/utils/cn';
import { apiFetch } from '../../apiClient';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface UpgradeCandidate {
  tmdb_id: string;
  title: string;
  year: string;
  poster_path: string;
  tmdb_poster?: string;
  rating_key: number;
  sections_owned: string;
  current_quality: string;
  current_tier: number;
  best_quality: string;
  best_tier: number;
  best_seeders: number;
  best_size_gb: number;
  best_tracker: string;
  best_torrent_name: string;
  upgrade_type: string;
  searched_at?: string;
  pub_date?: string;
  discovered_at?: string;
  rt_critic?: number | null;
  rt_audience?: number | null;
}

interface UpgradeStats {
  total_upgrades: number;
  total_grabbed: number;
  total_in_library: number;
  total_searched: number;
}

type TabMode = 'all' | 'new';
type SortMode = 'title' | 'year' | 'rating' | 'quality' | 'recent';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const EXIT_DURATION = 400;

const QUALITY_COLORS: Record<string, string> = {
  '2160p Remux': 'bg-emerald-500/20 text-emerald-300/90 border-emerald-400/30 shadow-[0_0_8px_rgba(16,185,129,0.3)]',
  '2160p':       'bg-sky-500/20 text-sky-300/90 border-sky-400/30 shadow-[0_0_8px_rgba(14,165,233,0.3)]',
  '1080p Remux': 'bg-blue-500/20 text-blue-300/90 border-blue-400/30 shadow-[0_0_8px_rgba(59,130,246,0.3)]',
  '1080p':       'bg-orange-500/20 text-orange-300/90 border-orange-400/30 shadow-[0_0_8px_rgba(249,115,22,0.3)]',
  '720p':        'bg-violet-500/20 text-violet-300/90 border-violet-400/30 shadow-[0_0_8px_rgba(139,92,246,0.3)]',
};
const DEFAULT_BADGE = 'bg-white/10 text-white/80 border-white/20';

function badgeColor(quality: string): string {
  return QUALITY_COLORS[quality] ?? DEFAULT_BADGE;
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const days = Math.floor(diff / 86400000);
  if (days < 1) return 'Today';
  if (days === 1) return '1 day ago';
  if (days < 30) return `${days} days ago`;
  const months = Math.floor(days / 30);
  return months === 1 ? '1 month ago' : `${months} months ago`;
}

function rtAvg(item: UpgradeCandidate): number {
  const c = item.rt_critic ?? 0;
  const a = item.rt_audience ?? 0;
  if (c && a) return Math.round((c + a) / 2);
  return c || a || 0;
}

const SORT_OPTIONS_ALL: { value: SortMode; label: string }[] = [
  { value: 'title', label: 'A-Z' },
  { value: 'year', label: 'Year' },
  { value: 'rating', label: 'Rating' },
  { value: 'quality', label: 'Quality' },
];


function sortUpgrades(items: UpgradeCandidate[], sort: SortMode, qualityFilter: string): UpgradeCandidate[] {
  let filtered = qualityFilter ? items.filter(i => i.best_quality === qualityFilter) : items;
  const sorted = [...filtered];
  switch (sort) {
    case 'title':
      sorted.sort((a, b) => a.title.localeCompare(b.title));
      break;
    case 'year':
      sorted.sort((a, b) => (parseInt(b.year) || 0) - (parseInt(a.year) || 0));
      break;
    case 'rating':
      sorted.sort((a, b) => rtAvg(b) - rtAvg(a));
      break;
    case 'quality':
      sorted.sort((a, b) => b.best_tier - a.best_tier || a.title.localeCompare(b.title));
      break;
    case 'recent':
      sorted.sort((a, b) => (b.pub_date ?? '').localeCompare(a.pub_date ?? ''));
      break;
  }
  return sorted;
}

function getPosterUrl(item: UpgradeCandidate): string | null {
  if (item.tmdb_poster) return item.tmdb_poster;
  if (item.poster_path) return `/api/img/plex?url=${encodeURIComponent(item.poster_path)}&width=300`;
  return null;
}

// ---------------------------------------------------------------------------
// UpgradeRow — swipe right to add to watchlist, swipe left to dismiss
// ---------------------------------------------------------------------------

function UpgradeRow({
  item, onUpgrade, onInfo, onDismiss, showPubDate,
}: {
  item: UpgradeCandidate;
  onUpgrade: (item: UpgradeCandidate) => void;
  onInfo: (item: UpgradeCandidate) => void;
  onDismiss: (item: UpgradeCandidate) => void;
  showPubDate?: boolean;
}) {
  const [removed, setRemoved] = useState(false);
  const posterSrc = getPosterUrl(item);
  const rowRef = useRef<HTMLDivElement>(null);

  const swipeRef = useRef({ active: false, startX: 0, startY: 0, locked: false });
  const [swipeOffset, setSwipeOffset] = useState(0);
  const [dragging, setDragging] = useState(false);

  const onSwipeStart = useCallback((e: React.TouchEvent) => {
    if ((e.target as HTMLElement).closest('[data-sheet-handle]')) return;
    swipeRef.current = {
      active: true,
      startX: e.touches[0]!.clientX,
      startY: e.touches[0]!.clientY,
      locked: false,
    };
    setDragging(true);
  }, []);

  const onSwipeMove = useCallback((e: React.TouchEvent) => {
    if (!swipeRef.current.active || !e.touches.length) return;
    const dx = e.touches[0]!.clientX - swipeRef.current.startX;
    const dy = e.touches[0]!.clientY - swipeRef.current.startY;

    if (!swipeRef.current.locked) {
      if (Math.abs(dx) > 8 || Math.abs(dy) > 8) {
        swipeRef.current.locked = true;
        if (Math.abs(dy) > Math.abs(dx)) {
          swipeRef.current.active = false;
          setDragging(false);
          return;
        }
      } else {
        return;
      }
    }

    // Only allow swipe left (dismiss)
    if (dx > 0) {
      setSwipeOffset(0);
      return;
    }

    e.preventDefault();
    setSwipeOffset(dx);
  }, []);

  const onSwipeEnd = useCallback(() => {
    if (!swipeRef.current.active) {
      setDragging(false);
      return;
    }
    swipeRef.current.active = false;
    setDragging(false);
    const rowWidth = rowRef.current?.offsetWidth || 300;
    const threshold = rowWidth * 0.75;

    if (swipeOffset < -threshold) {
      // Swipe LEFT → dismiss
      setSwipeOffset(-rowWidth - 50);
      setTimeout(() => {
        setRemoved(true);
        setTimeout(() => onDismiss(item), 300);
      }, 250);
    } else {
      setSwipeOffset(0);
    }
  }, [swipeOffset, item, onDismiss]);

  if (removed) {
    return (
      <div
        style={{
          overflow: 'hidden',
          maxHeight: 0,
          opacity: 0,
          transition: 'max-height 0.3s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease',
        }}
      />
    );
  }

  const rowWidth = rowRef.current?.offsetWidth || 300;
  const swipeProgress = Math.min(1, Math.abs(swipeOffset) / (rowWidth * 0.75));
  const isLeft = swipeOffset < -10;

  return (
    <div ref={rowRef} style={{ position: 'relative', overflow: 'hidden' }}>
      {/* Red backdrop — swipe left to dismiss */}
      {isLeft && (
        <div
          style={{
            position: 'absolute', inset: 0, zIndex: 0,
            background: `rgba(220, 38, 38, ${0.3 + swipeProgress * 0.5})`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <span
            style={{
              opacity: swipeProgress,
              transform: `scale(${0.7 + swipeProgress * 0.3})`,
              transition: dragging ? 'none' : 'all 0.2s ease',
            }}
            className="text-[13px] font-semibold text-white/80"
          >
            {swipeProgress >= 1 ? 'Release to remove' : 'Swipe to remove'}
          </span>
        </div>
      )}

      {/* Swipeable row content */}
      <div
        className="flex w-full items-stretch gap-2 px-3 py-3"
        style={{
          transform: `translateX(${swipeOffset}px)`,
          transition: dragging ? 'none' : 'transform 0.4s cubic-bezier(0.22, 1, 0.36, 1)',
          background: isLeft
            ? `rgba(${26 + (220 - 26) * swipeProgress * 0.08}, ${10 - 10 * swipeProgress * 0.3}, ${30 - 30 * swipeProgress * 0.15}, 1)`
            : 'var(--color-kiosk-black)',
          position: 'relative',
          zIndex: 1,
          clipPath: 'inset(0 0 0 0)',
        }}
        onTouchStart={onSwipeStart}
        onTouchMove={onSwipeMove}
        onTouchEnd={onSwipeEnd}
      >
          {/* Poster — click opens modal */}
          <div
            className="relative h-[162px] w-[108px] shrink-0 overflow-hidden rounded-xl bg-white/5 cursor-pointer active:opacity-80"
            onClick={() => { if (swipeOffset === 0) onInfo(item); }}
          >
            {posterSrc ? (
              <img src={posterSrc} alt={item.title} loading="lazy" draggable={false}
                className="h-full w-full object-cover" />
            ) : (
              <div className="flex h-full w-full items-center justify-center text-white/20 text-[11px]">
                No Poster
              </div>
            )}
            {/* Ratings overlay on poster */}
            <div
              className="absolute bottom-0 left-0 right-0 flex items-center justify-center gap-2 pb-2 pt-6"
              style={{ background: 'linear-gradient(to top, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0.5) 60%, transparent 100%)' }}
            >
              <span className="inline-flex items-center gap-0.5">
                <img
                  src={item.rt_critic != null && item.rt_critic > 0
                    ? (item.rt_critic >= 60 ? '/static/images/rotten-tomatoes-logo.png' : '/static/images/rotten-tomatoes-splat.png')
                    : '/static/images/rotten-tomatoes-logo.png'}
                  alt="" width="16" height="16" className="shrink-0"
                  style={!(item.rt_critic != null && item.rt_critic > 0) ? { opacity: 0.4 } : undefined}
                />
                <span className={`text-[12px] font-bold ${item.rt_critic != null && item.rt_critic > 0 ? 'text-white/90' : 'text-white/35'}`}>
                  {item.rt_critic != null && item.rt_critic > 0 ? `${item.rt_critic}%` : 'N/A'}
                </span>
              </span>
              <span className="inline-flex items-center gap-0.5">
                <img
                  src={item.rt_audience != null && item.rt_audience > 0
                    ? (item.rt_audience >= 60 ? '/static/images/rotten-tomatoes-popcorn-good.png' : '/static/images/rotten-tomatoes-popcorn-bad.png')
                    : '/static/images/rotten-tomatoes-popcorn-good.png'}
                  alt="" width="16" height="16" className="shrink-0"
                  style={!(item.rt_audience != null && item.rt_audience > 0) ? { opacity: 0.4 } : undefined}
                />
                <span className={`text-[12px] font-bold ${item.rt_audience != null && item.rt_audience > 0 ? 'text-white/90' : 'text-white/35'}`}>
                  {item.rt_audience != null && item.rt_audience > 0 ? `${item.rt_audience}%` : 'N/A'}
                </span>
              </span>
            </div>
          </div>
          <div className="relative min-w-0 flex-1">
            {/* Quality badge — vertically centered, click adds to watchlist */}
            <div className="absolute inset-0 flex items-center justify-center">
              <span
                className={cn('shrink-0 rounded-xl border-2 py-2 px-4 text-[15px] font-bold whitespace-nowrap text-center cursor-pointer active:scale-95 transition-transform', badgeColor(item.best_quality))}
                onClick={(e) => { e.stopPropagation(); if (swipeOffset === 0) onUpgrade(item); }}
              >
                {item.best_quality}
              </span>
            </div>
            {showPubDate && item.pub_date && (
              <p className="absolute bottom-0 left-0 right-0 text-[11px] text-white/20 text-center">{timeAgo(item.pub_date)}</p>
            )}
          </div>
        </div>
      </div>
  );
}

// ---------------------------------------------------------------------------
// UpgradesSheet — upgrade finder as a bottom sheet
// ---------------------------------------------------------------------------

interface UpgradesSheetProps {
  open: boolean;
  onClose: () => void;
}

export function UpgradesSheet({ open, onClose }: UpgradesSheetProps) {
  const { openMovie, openTv } = useModalHost();
  const scrollRef = useRef<HTMLDivElement>(null);

  // --- Phase-based animation (matches MovieDetailModal exactly) ---
  const [phase, setPhase] = useState<'entering' | 'visible' | 'exiting' | 'gone'>('gone');

  useEffect(() => {
    if (open) {
      setPhase('entering');
      requestAnimationFrame(() => {
        requestAnimationFrame(() => setPhase('visible'));
      });
    }
  }, [open]);

  // Body scroll lock + background
  useEffect(() => {
    if (phase !== 'visible') return;
    const prevBg = document.documentElement.style.background;
    document.documentElement.style.background = '#000';
    return () => { document.documentElement.style.background = prevBg; };
  }, [phase]);

  const handleDismiss = useCallback(() => {
    setPhase('exiting');
    setTimeout(() => {
      setPhase('gone');
      onClose();
    }, EXIT_DURATION);
  }, [onClose]);

  // Swipe-to-dismiss: same hook as MovieDetailModal
  const { handlers: swipeHandlers } = useSwipeToDismiss({
    scrollRef,
    onDismiss: handleDismiss,
    enabled: phase === 'visible',
  });

  // Grab handle drag-to-dismiss
  const cardRef = useRef<HTMLDivElement>(null);
  const handleDragRef = useRef({ active: false, startY: 0 });

  const onHandleTouchStart = useCallback((e: React.TouchEvent) => {
    if (phase !== 'visible') return;
    handleDragRef.current = { active: true, startY: e.touches[0]!.clientY };
  }, [phase]);

  const onHandleTouchMove = useCallback((e: React.TouchEvent) => {
    if (!handleDragRef.current.active) return;
    const dy = e.touches[0]!.clientY - handleDragRef.current.startY;
    if (dy < 0) return; // only drag down
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
      // Dismiss
      card.style.transform = `translate3d(0, ${window.innerHeight}px, 0)`;
      card.style.transition = 'transform 300ms cubic-bezier(0.22, 1, 0.36, 1)';
      const overlay = card.closest('[data-modal]') as HTMLElement | null;
      if (overlay) {
        overlay.style.transition = 'opacity 300ms ease-out';
        overlay.style.opacity = '0';
      }
      setTimeout(() => handleDismiss(), 280);
    } else {
      // Snap back
      card.style.transform = 'translate3d(0, 0, 0)';
      card.style.transition = 'transform 300ms cubic-bezier(0.25, 1, 0.5, 1)';
      const overlay = card.closest('[data-modal]') as HTMLElement | null;
      if (overlay) overlay.style.backgroundColor = 'rgba(0,0,0,0.6)';
      setTimeout(() => {
        if (card) card.style.transition = '';
      }, 310);
    }
  }, [handleDismiss]);

  // --- Upgrade data ---
  const [sort, setSort] = useState<SortMode>('title');
  const [qualityFilter, setQualityFilter] = useState('');
  const [tab, setTab] = useState<TabMode>('all');

  const switchTab = useCallback((t: TabMode) => {
    setTab(t);
    setSort(t === 'new' ? 'recent' : 'title');
    setQualityFilter('');
  }, []);
  const [allUpgrades, setAllUpgrades] = useState<UpgradeCandidate[]>([]);
  const [allLoading, setAllLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [newUpgrades, setNewUpgrades] = useState<UpgradeCandidate[]>([]);
  const [newLoading, setNewLoading] = useState(false);
  const [upgradingIds, setUpgradingIds] = useState<Set<string>>(new Set());
  const [upgradedIds, setUpgradedIds] = useState<Set<string>>(new Set());
  const [stats, setStats] = useState<UpgradeStats | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval>>(undefined);

  const fetchAllUpgrades = useCallback(() => {
    setAllLoading(true);
    apiFetch('/api/upgrades/list?sort=title&limit=500')
      .then(r => r.json())
      .then(data => {
        setAllUpgrades(data.upgrades || []);
        if (data.stats) setStats(data.stats);
        setAllLoading(false);
      })
      .catch(() => setAllLoading(false));
  }, []);

  const fetchNewUpgrades = useCallback(() => {
    setNewLoading(true);
    apiFetch('/api/upgrades/new/list?limit=100')
      .then(r => r.json())
      .then(data => { setNewUpgrades(data.upgrades || []); setNewLoading(false); })
      .catch(() => setNewLoading(false));
  }, []);

  // Check scan status on open — restores scanning state if backend is still running
  useEffect(() => {
    if (!open) return;
    apiFetch('/api/upgrades/progress')
      .then(r => r.json())
      .then(data => { if (data.is_running) setScanning(true); })
      .catch(() => {});
  }, [open]);

  // Always fetch stats on open
  useEffect(() => {
    if (!open) return;
    if (!stats) {
      apiFetch('/api/upgrades/list?sort=title&limit=1')
        .then(r => r.json())
        .then(data => { if (data.stats) setStats(data.stats); })
        .catch(() => {});
    }
  }, [open, stats]);

  useEffect(() => {
    if (!open) return;
    if (tab === 'all') fetchAllUpgrades();
    else fetchNewUpgrades();
  }, [tab, open, fetchAllUpgrades, fetchNewUpgrades]);

  // Unified poll while any scan runs — also fetch results progressively
  useEffect(() => {
    if (!scanning) return;
    pollRef.current = setInterval(() => {
      apiFetch('/api/upgrades/progress').then(r => r.json()).then(data => {
        if (data.total_upgrades != null) {
          setStats(prev => prev ? { ...prev, total_upgrades: data.total_upgrades, total_searched: data.total_searched, total_in_library: data.total_in_library } : { total_upgrades: data.total_upgrades, total_grabbed: 0, total_in_library: data.total_in_library, total_searched: data.total_searched });
        }
        if (!data.is_running) {
          setScanning(false);
          if (tab === 'all') fetchAllUpgrades();
          else fetchNewUpgrades();
        } else {
          // Progressively load results while scan is running
          if (tab === 'all') {
            apiFetch('/api/upgrades/list?sort=title&limit=500')
              .then(r => r.json())
              .then(d => { if (d.upgrades?.length) setAllUpgrades(d.upgrades); })
              .catch(() => {});
          } else {
            apiFetch('/api/upgrades/new/list?limit=100')
              .then(r => r.json())
              .then(d => { if (d.upgrades?.length) setNewUpgrades(d.upgrades); })
              .catch(() => {});
          }
        }
      }).catch(() => {});
    }, 4000);
    return () => clearInterval(pollRef.current);
  }, [scanning, tab, fetchAllUpgrades, fetchNewUpgrades]);

  const handleScan = useCallback(() => {
    if (scanning) return;
    setScanning(true);
    const endpoint = tab === 'all' ? '/api/upgrades/batch' : '/api/upgrades/new/scan';
    const opts: RequestInit = tab === 'all'
      ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }
      : { method: 'POST' };
    apiFetch(endpoint, opts)
      .then(() => {
        // Poll quickly until scan finishes (handles both fast and slow scans)
        const quickPoll = setInterval(() => {
          apiFetch('/api/upgrades/progress').then(r => r.json()).then(data => {
            if (!data.is_running) {
              clearInterval(quickPoll);
              setScanning(false);
              if (tab === 'all') fetchAllUpgrades();
              else fetchNewUpgrades();
            }
          }).catch(() => {});
        }, 2000);
        // Safety: stop polling after 5 minutes
        setTimeout(() => { clearInterval(quickPoll); setScanning(false); }, 300000);
      })
      .catch(() => setScanning(false));
  }, [scanning, tab, fetchAllUpgrades, fetchNewUpgrades]);

  const handleUpgrade = useCallback(async (item: UpgradeCandidate) => {
    const tmdbId = item.tmdb_id;
    if (!tmdbId || upgradingIds.has(tmdbId)) return;
    const profile = item.best_quality.includes('2160p') ? '2160p Remux' : '1080p Remux';
    setUpgradingIds(prev => new Set(prev).add(tmdbId));
    try {
      const resp = await apiFetch('/api/watchlist', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tmdb_id: parseInt(tmdbId), title: item.title, year: parseInt(item.year) || undefined, quality_profile: profile }),
      });
      if (resp.ok) {
        setUpgradedIds(prev => new Set(prev).add(tmdbId));
        setToast(`${item.title} added to Watchlist`);
        setTimeout(() => setToast(null), 2500);
        apiFetch('/api/upgrades/mark-grabbed', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tmdb_id: tmdbId }),
        }).catch(() => {});
      }
    } catch { /* silent */ } finally {
      setUpgradingIds(prev => { const n = new Set(prev); n.delete(tmdbId); return n; });
    }
  }, [upgradingIds]);

  const handleDismissUpgrade = useCallback((item: UpgradeCandidate) => {
    const tmdbId = item.tmdb_id;
    if (!tmdbId) return;
    setUpgradedIds(prev => new Set(prev).add(tmdbId));
    apiFetch('/api/upgrades/dismiss', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tmdb_id: tmdbId }),
    }).catch(() => {});
    setStats(prev => prev ? { ...prev, total_upgrades: Math.max(0, prev.total_upgrades - 1) } : prev);
  }, []);

  // Info: hide sheet behind modal, restore when modal closes
  const [behindModal, setBehindModal] = useState(false);

  const handleInfo = useCallback((item: UpgradeCandidate) => {
    if (!item.tmdb_id) return;
    setBehindModal(true);
    openModalForItem(
      { openMovie, openTv },
      { mediaType: 'movie' as const, tmdbId: parseInt(item.tmdb_id), ratingKey: item.rating_key || null },
      'upgrades' as ModalSource,
    );
  }, [openMovie, openTv]);

  // Listen for modal close to restore the sheet
  useEffect(() => {
    if (!behindModal) return;
    const restore = () => setBehindModal(false);
    window.addEventListener('modal-closed', restore);
    return () => window.removeEventListener('modal-closed', restore);
  }, [behindModal]);

  const currentItems = tab === 'all' ? allUpgrades : newUpgrades;
  const currentLoading = tab === 'all' ? allLoading : newLoading;
  const showPubDate = tab === 'new';

  // Derive available qualities for filter dropdown
  const availableQualities = useMemo(() => {
    const quals = new Set(currentItems.map(i => i.best_quality));
    return Array.from(quals).sort((a, b) => {
      const tierA = currentItems.find(i => i.best_quality === a)?.best_tier ?? 0;
      const tierB = currentItems.find(i => i.best_quality === b)?.best_tier ?? 0;
      return tierB - tierA;
    });
  }, [currentItems]);

  const filteredItems = useMemo(
    () => sortUpgrades(currentItems.filter(u => !upgradedIds.has(u.tmdb_id)), sort, qualityFilter),
    [currentItems, upgradedIds, sort, qualityFilter],
  );

  // --- Render ---
  if (phase === 'gone') return null;

  const ENTER_DUR = 600;
  const EXIT_DUR = 400;
  const springBezier = 'cubic-bezier(0.22, 1, 0.36, 1)';

  const cardTransform =
    phase === 'entering'
      ? 'translate3d(0, 100%, 0)'
      : phase === 'visible'
        ? 'translate3d(0, 0, 0)'
        : 'translate3d(0, 100%, 0)';
  const cardTransition =
    phase === 'exiting'
      ? `transform ${EXIT_DUR}ms cubic-bezier(0.4, 0, 1, 1)`
      : `transform ${ENTER_DUR}ms ${springBezier}`;

  return createPortal(
    <div
      data-modal="UpgradesSheet"
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        zIndex: behindModal ? 9999 : 10000,
        backgroundColor: 'rgba(0,0,0,0.6)',
        opacity: phase === 'entering' ? 0 : 1,
        transition: 'opacity 250ms ease-out',
      }}
    >
      {/* Card — full height, rounded top, matches MovieDetailModal */}
      <div
        ref={cardRef}
        style={{
          position: 'absolute',
          top: 'max(env(safe-area-inset-top, 0px), 6px)',
          left: 0,
          right: 0,
          bottom: 0,
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
        {/* Grab handle — draggable to dismiss */}
        <div
          data-sheet-handle
          onTouchStart={onHandleTouchStart}
          onTouchMove={onHandleTouchMove}
          onTouchEnd={onHandleTouchEnd}
          style={{ flexShrink: 0, display: 'flex', justifyContent: 'center', padding: '12px 0 8px', cursor: 'grab', touchAction: 'none' }}
        >
          <div style={{ width: 40, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)' }} />
        </div>

        {/* Header */}
        <div className="shrink-0 px-4 pb-3 space-y-3">
          {/* Row 1: tabs + scan */}
          <div className="flex items-center justify-between">
            <div className="inline-flex rounded-xl bg-white/8 p-1">
              {(['all', 'new'] as const).map(t => (
                <button
                  key={t}
                  onClick={() => switchTab(t)}
                  className={cn(
                    'rounded-lg px-5 py-2 text-[14px] font-semibold transition-colors',
                    tab === t ? 'bg-white/15 text-white/90' : 'text-white/40',
                  )}
                >
                  {t === 'all' ? 'All' : 'New'}
                </button>
              ))}
            </div>
            <button
              onClick={handleScan}
              disabled={scanning}
              className="relative rounded-xl px-5 py-2 text-[14px] font-semibold transition-colors overflow-hidden whitespace-nowrap"
              style={{
                background: scanning ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.12)',
                color: scanning ? 'rgba(255,255,255,0.5)' : 'rgba(255,255,255,0.7)',
                cursor: scanning ? 'not-allowed' : 'pointer',
              }}
            >
              {scanning && (
                <span
                  className="absolute inset-0"
                  style={{
                    background: 'linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.08) 50%, transparent 100%)',
                    animation: 'scanShimmer 1.5s ease-in-out infinite',
                  }}
                />
              )}
              <span className="relative flex items-center gap-2">
                {scanning && (
                  <span className="h-3 w-3 animate-spin rounded-full border-[1.5px] border-white/15 border-t-white/60 inline-block" />
                )}
                {scanning ? 'Scanning...' : 'Scan Now'}
              </span>
            </button>
          </div>

          {/* Row 2: sort + quality filter (All tab only) */}
          {tab === 'all' && (
            <div className="flex items-center gap-2">
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value as SortMode)}
                className="flex-1 rounded-lg bg-white/8 border border-white/8 px-3 py-2 text-[13px] font-medium text-white/80 appearance-none cursor-pointer"
                style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' width=\'12\' height=\'12\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'rgba(255,255,255,0.4)\' stroke-width=\'3\'%3E%3Cpath d=\'M6 9l6 6 6-6\'/%3E%3C/svg%3E")', backgroundRepeat: 'no-repeat', backgroundPosition: 'right 10px center', paddingRight: '28px' }}
              >
                {SORT_OPTIONS_ALL.map(o => (
                  <option key={o.value} value={o.value}>Sort: {o.label}</option>
                ))}
              </select>
              {availableQualities.length > 1 && (
                <select
                  value={qualityFilter}
                  onChange={(e) => setQualityFilter(e.target.value)}
                  className="flex-1 rounded-lg bg-white/8 border border-white/8 px-3 py-2 text-[13px] font-medium text-white/80 appearance-none cursor-pointer"
                  style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' width=\'12\' height=\'12\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'rgba(255,255,255,0.4)\' stroke-width=\'3\'%3E%3Cpath d=\'M6 9l6 6 6-6\'/%3E%3C/svg%3E")', backgroundRepeat: 'no-repeat', backgroundPosition: 'right 10px center', paddingRight: '28px' }}
                >
                  <option value="">All Quality</option>
                  {availableQualities.map(q => (
                    <option key={q} value={q}>{q}</option>
                  ))}
                </select>
              )}
            </div>
          )}
        </div>

        {/* Stats bar — tab-aware */}
        {tab === 'all' && stats && (stats.total_upgrades > 0 || stats.total_grabbed > 0) && (
          <div className="shrink-0 px-4 pb-3 flex items-center gap-4">
            <div className="flex items-center gap-1.5">
              <span className="text-[22px] font-bold text-emerald-400">{stats.total_upgrades}</span>
              <span className="text-[12px] text-white/40">found</span>
            </div>
            <div className="w-px h-4 bg-white/10" />
            <div className="flex items-center gap-1.5">
              <span className="text-[22px] font-bold text-blue-400">{stats.total_grabbed}</span>
              <span className="text-[12px] text-white/40">upgraded</span>
            </div>
          </div>
        )}
        {tab === 'new' && filteredItems.length > 0 && (
          <div className="shrink-0 px-4 pb-3 flex items-center gap-1.5">
            <span className="text-[22px] font-bold text-emerald-400">{filteredItems.length}</span>
            <span className="text-[12px] text-white/40">upgrade{filteredItems.length !== 1 ? 's' : ''} available</span>
          </div>
        )}

        {/* Scrollable content */}
        <div
          ref={scrollRef}
          data-scroll-container
          className="luma-scroll-y flex-1 px-1"
          style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}
          {...swipeHandlers}
        >
          {/* Loading */}
          {currentLoading && currentItems.length === 0 && (
            <div className="flex flex-col items-center justify-center py-20 text-white/30">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-white/10 border-t-white/60" />
              <p className="mt-4 text-[14px]">Loading...</p>
            </div>
          )}

          {/* Empty */}
          {!currentLoading && currentItems.length === 0 && (
            <div className="flex flex-col items-center justify-center py-20 text-white/30">
              <p className="text-[14px]">
                {tab === 'all'
                  ? 'No upgrades found yet. Tap "Scan Now" to search your library.'
                  : 'No 4K remux upgrades in the last 7 days. Tap "Scan Now" to check.'}
              </p>
            </div>
          )}

          {/* All upgraded */}
          {!currentLoading && filteredItems.length === 0 && currentItems.length > 0 && (
            <div className="flex flex-col items-center justify-center py-20 text-white/30">
              <p className="text-[14px]">All upgrades have been applied.</p>
            </div>
          )}

          {/* Results */}
          {filteredItems.length > 0 && (
            <div className="divide-y divide-white/5">
              {filteredItems.map(item => (
                <UpgradeRow
                  key={`${item.tmdb_id}-${item.best_quality}`}
                  item={item}
                  onUpgrade={handleUpgrade}
                  onInfo={handleInfo}
                  onDismiss={handleDismissUpgrade}
                  showPubDate={showPubDate}
                />
              ))}
            </div>
          )}

          {/* Safe area bottom spacer */}
          <div className="shrink-0" style={{ height: 'env(safe-area-inset-bottom, 12px)' }} />
        </div>
      </div>

      {/* Toast notification */}
      {toast && (
        <div
          style={{
            position: 'fixed',
            bottom: 40,
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 10003,
            animation: 'mobileToastEnter 0.4s cubic-bezier(0.22, 1, 0.36, 1)',
          }}
          className="rounded-xl border border-emerald-500/20 bg-emerald-500/15 px-5 py-3 text-[13px] font-medium text-emerald-300 shadow-lg backdrop-blur-md whitespace-nowrap"
        >
          {toast}
        </div>
      )}

      {/* Inline keyframes for scan shimmer */}
      <style>{`
        @keyframes scanShimmer {
          0% { transform: translateX(-100%); }
          100% { transform: translateX(100%); }
        }
      `}</style>
    </div>,
    document.body,
  );
}
