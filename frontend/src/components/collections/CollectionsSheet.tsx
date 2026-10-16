import { useState, useEffect, useCallback, useRef, useMemo, memo } from 'react';
import { createPortal } from 'react-dom';
import { useModalHost, type ModalSource } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { posterUrl } from '@/utils/image';
import { cn } from '@/utils/cn';
import { apiFetch } from '../../apiClient';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface CollectionSummary {
  id: number;
  name: string;
  poster_path: string | null;
  backdrop_path: string | null;
  owned_count: number;
  missing_count: number;
  upcoming_count: number;
  released_total: number;
  parts_total: number;
  fame_score?: number;
}

interface CollectionPart {
  tmdb_id: number;
  title: string;
  year: number | null;
  release_date: string | null;
  poster_path: string | null;
  overview: string;
  status: 'owned' | 'missing' | 'upcoming';
  owned: boolean;
  released: boolean;
  rating_key: number | null;
  section_name: string | null;
  imdb_rating: number | null;
  imdb_id: string | null;
  media_type: 'movie';
}

interface CollectionDetail extends CollectionSummary {
  overview: string;
  parts: CollectionPart[];
}

interface CollectionsSheetProps {
  open: boolean;
  onClose: () => void;
  /** Open directly into this collection's detail (movie-modal Collection button). */
  initialCollectionId?: number | null;
  /** Portal z-index — home sheet uses 80; movie modal needs > 10000. */
  overlayZIndex?: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const DISMISS_THRESHOLD = 120;
const EXIT_DURATION = 400;
const SPRING = 'cubic-bezier(0.16, 1, 0.3, 1)';

const STATUS_ORDER: Record<CollectionPart['status'], number> = {
  owned: 0,
  missing: 1,
  upcoming: 2,
};

function ownedLabel(owned: number, total: number): string {
  return `${owned} of ${total} owned`;
}

function sortParts(parts: CollectionPart[]): CollectionPart[] {
  return [...parts].sort((a, b) => {
    const sa = STATUS_ORDER[a.status] ?? 9;
    const sb = STATUS_ORDER[b.status] ?? 9;
    if (sa !== sb) return sa - sb;
    const da = a.release_date || '9999-99-99';
    const db = b.release_date || '9999-99-99';
    if (da !== db) return da < db ? -1 : 1;
    return (a.tmdb_id || 0) - (b.tmdb_id || 0);
  });
}

function orbForStatus(status: CollectionPart['status']): { color: string; glow: string } | null {
  if (status === 'owned') {
    return { color: '#4ade80', glow: '0 0 8px rgba(74,222,128,0.6)' };
  }
  if (status === 'missing') {
    return { color: '#f87171', glow: '0 0 8px rgba(248,113,113,0.6)' };
  }
  // Upcoming — muted, not green/red ownership signal
  return { color: 'rgba(255,255,255,0.35)', glow: '0 0 6px rgba(255,255,255,0.2)' };
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

async function fetchList(): Promise<{ collections: CollectionSummary[]; building: boolean; built_at_iso: string | null }> {
  const resp = await apiFetch('/api/collections/incomplete');
  if (!resp.ok) return { collections: [], building: false, built_at_iso: null };
  const data = await resp.json();
  return {
    collections: (data.collections || []) as CollectionSummary[],
    building: Boolean(data.building),
    built_at_iso: (data.built_at_iso as string) || null,
  };
}

async function fetchDetail(id: number): Promise<CollectionDetail | null> {
  const resp = await apiFetch(`/api/collections/${id}`);
  if (!resp.ok) return null;
  return (await resp.json()) as CollectionDetail;
}

async function triggerRefresh(): Promise<void> {
  try {
    await apiFetch('/api/collections/refresh', { method: 'POST' });
  } catch {
    /* ignore — background will catch up */
  }
}

// ---------------------------------------------------------------------------
// Rows
// ---------------------------------------------------------------------------

const CollectionRow = memo(function CollectionRow({
  item,
  onClick,
}: {
  item: CollectionSummary;
  onClick: () => void;
}) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const src = posterUrl(item.poster_path, 'w342');

  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-3 px-3 py-3 text-left active:bg-white/5"
    >
      <div className="relative h-[120px] w-[80px] shrink-0 overflow-hidden rounded-lg bg-white/5">
        {src ? (
          <img
            src={src}
            alt={item.name}
            loading="lazy"
            draggable={false}
            className={cn(
              'h-full w-full object-cover transition-opacity duration-300',
              imgLoaded ? 'opacity-100' : 'opacity-0',
            )}
            onLoad={() => setImgLoaded(true)}
            onError={(e) => {
              (e.target as HTMLImageElement).style.visibility = 'hidden';
            }}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-[11px] text-white/20">
            No Poster
          </div>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[14px] font-medium text-white/90">{item.name}</p>
        <p className="mt-1 text-[13px] text-white/45">
          {ownedLabel(item.owned_count, item.released_total || item.parts_total)}
        </p>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          <span className="rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase bg-amber-500/20 text-amber-400">
            {item.missing_count} missing
          </span>
          {item.upcoming_count > 0 && (
            <span className="rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase bg-white/10 text-white/40">
              {item.upcoming_count} upcoming
            </span>
          )}
        </div>
      </div>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 text-white/25">
        <polyline points="9 18 15 12 9 6" />
      </svg>
    </button>
  );
});

const PartRow = memo(function PartRow({
  part,
  onClick,
}: {
  part: CollectionPart;
  onClick: () => void;
}) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const src = posterUrl(part.poster_path, 'w185');
  const orb = orbForStatus(part.status);

  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-3 px-3 py-3 text-left active:bg-white/5"
    >
      <div className="relative h-[96px] w-[64px] shrink-0 overflow-hidden rounded-md bg-white/5">
        {src ? (
          <img
            src={src}
            alt={part.title}
            loading="lazy"
            draggable={false}
            className={cn(
              'h-full w-full object-cover transition-opacity duration-300',
              imgLoaded ? 'opacity-100' : 'opacity-0',
              part.status === 'missing' && imgLoaded && 'opacity-70',
            )}
            onLoad={() => setImgLoaded(true)}
            onError={(e) => {
              (e.target as HTMLImageElement).style.visibility = 'hidden';
            }}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-[10px] text-white/20">—</div>
        )}
        {orb && (
          <span
            className="absolute left-1.5 top-1.5 block h-2.5 w-2.5 rounded-full"
            style={{ background: orb.color, boxShadow: orb.glow }}
            aria-label={part.status}
          />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className={cn('truncate text-[14px] font-medium', part.status === 'owned' ? 'text-white/90' : 'text-white/75')}>
          {part.title}
          {part.year != null && (
            <span className="ml-2 font-normal text-white/35">({part.year})</span>
          )}
        </p>
      </div>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 text-white/25">
        <polyline points="9 18 15 12 9 6" />
      </svg>
    </button>
  );
});

// ---------------------------------------------------------------------------
// Sheet
// ---------------------------------------------------------------------------

export function CollectionsSheet({ open, onClose, initialCollectionId = null, overlayZIndex = 80 }: CollectionsSheetProps) {
  const { openMovie, openTv } = useModalHost();

  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });
  const [dragOffset, setDragOffset] = useState(0);
  const [phase, setPhase] = useState<'entering' | 'visible' | 'exiting' | 'gone'>('gone');

  const [collections, setCollections] = useState<CollectionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [building, setBuilding] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const [detail, setDetail] = useState<CollectionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [behindModal, setBehindModal] = useState(false);
  const showingDetailRef = useRef(false);

  useEffect(() => {
    if (open) {
      setPhase('entering');
      setDragOffset(0);
      setDetail(null);
      setDetailLoading(false);
      if (initialCollectionId != null) {
        setDetailLoading(true);
        void fetchDetail(initialCollectionId).then((d) => {
          setDetail(d);
          setDetailLoading(false);
        });
      }
      requestAnimationFrame(() => {
        requestAnimationFrame(() => setPhase('visible'));
      });
    }
  }, [open, initialCollectionId]);

  useEffect(() => {
    if (!open) return;
    const prevBg = document.documentElement.style.background;
    const prevOverflow = document.body.style.overflow;
    document.documentElement.style.background = 'var(--color-kiosk-black)';
    document.body.style.overflow = 'hidden';
    return () => {
      document.documentElement.style.background = prevBg;
      document.body.style.overflow = prevOverflow;
    };
  }, [open]);

  const loadList = useCallback(async () => {
    setLoading(true);
    const data = await fetchList();
    setCollections(data.collections);
    setBuilding(data.building);
    setLoading(false);
    // Empty on first open — kick a refresh and poll briefly.
    if (data.collections.length === 0) {
      void triggerRefresh();
      setBuilding(true);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    if (initialCollectionId != null) return; // detail-only from movie modal
    void loadList();
  }, [open, loadList, initialCollectionId]);

  // Poll while building so the first warm-up fills the sheet without a manual refresh.
  useEffect(() => {
    if (!open || !building) return;
    const id = window.setInterval(async () => {
      const data = await fetchList();
      setCollections(data.collections);
      setBuilding(data.building);
      if (!data.building && data.collections.length > 0) {
        setLoading(false);
      }
    }, 2500);
    return () => window.clearInterval(id);
  }, [open, building]);

  const handleDismiss = useCallback(() => {
    setDragOffset(0);
    requestAnimationFrame(() => {
      setPhase('exiting');
      setTimeout(() => {
        setPhase('gone');
        setDetail(null);
        onClose();
      }, EXIT_DURATION);
    });
  }, [onClose]);

  const popToList = useCallback(() => {
    setDragOffset(0);
    setDetail(null);
    setDetailLoading(false);
  }, []);

  const showingDetail = detail != null || detailLoading;
  showingDetailRef.current = showingDetail;

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
    if (dy > DISMISS_THRESHOLD) {
      // Detail swipe pops back to the Collections list — unless this sheet was
      // opened from a movie modal (initialCollectionId), in which case swipe
      // returns to that movie (same stack discipline as Sources).
      if (initialCollectionId != null) handleDismiss();
      else if (showingDetailRef.current) popToList();
      else handleDismiss();
    } else {
      setDragOffset(0);
    }
  }, [handleDismiss, popToList, initialCollectionId]);

  const openCollection = useCallback(async (id: number) => {
    setDetailLoading(true);
    const d = await fetchDetail(id);
    setDetail(d);
    setDetailLoading(false);
  }, []);

  const handlePartClick = useCallback(
    (part: CollectionPart) => {
      setBehindModal(true);
      // Always open by TMDb id. Only pass ratingKey when ownership is confirmed
      // — stale cache "missing" parts must not force the unowned/Add-to-Watchlist
      // path; useMovieDetail resolves live ownership via /api/plex/lookup.
      openModalForItem(
        { openMovie, openTv },
        {
          mediaType: 'movie',
          tmdbId: part.tmdb_id,
          ratingKey: part.owned && part.rating_key ? part.rating_key : undefined,
        } as any,
        'collections' as ModalSource,
      );
    },
    [openMovie, openTv],
  );

  useEffect(() => {
    if (!behindModal) return;
    const restore = () => setBehindModal(false);
    window.addEventListener('modal-closed', restore);
    return () => window.removeEventListener('modal-closed', restore);
  }, [behindModal]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    setBuilding(true);
    await triggerRefresh();
    // Give the server a moment, then poll.
    window.setTimeout(async () => {
      const data = await fetchList();
      setCollections(data.collections);
      setBuilding(data.building);
      setRefreshing(false);
    }, 1500);
  }, []);

  const sortedDetailParts = useMemo(
    () => (detail?.parts ? sortParts(detail.parts) : []),
    [detail],
  );

  if (phase === 'gone' && !open) return null;

  const sheetTransform =
    dragOffset > 0
      ? `translate3d(0, ${dragOffset}px, 0)`
      : phase === 'entering' || phase === 'exiting'
        ? 'translate3d(0, 100%, 0)'
        : 'translate3d(0, 0, 0)';

  const onScrimClick = () => {
    if (showingDetail) popToList();
    else handleDismiss();
  };

  return createPortal(
    <div
      data-sheet="CollectionsSheet"
      className="fixed inset-0"
      style={{
        zIndex: overlayZIndex,
        pointerEvents: behindModal ? 'none' : 'auto',
        opacity: behindModal ? 0 : 1,
        transition: behindModal ? 'opacity 0.2s' : undefined,
      }}
    >
      {/* Scrim */}
      <div
        className="absolute inset-0 bg-black/60"
        style={{
          opacity: phase === 'visible' && dragOffset === 0 ? 1 : Math.max(0, 1 - dragOffset / 300),
          transition: dragOffset > 0 ? 'none' : `opacity ${EXIT_DURATION}ms ${SPRING}`,
        }}
        onClick={onScrimClick}
      />

      {/* Sheet — same purple as Watchlist / Similar / Reviews */}
      <div
        className="absolute inset-x-0 bottom-0 flex max-h-[92vh] flex-col overflow-hidden rounded-t-2xl shadow-2xl"
        style={{
          background: 'var(--color-kiosk-black)',
          transform: sheetTransform,
          transition: dragOffset > 0 ? 'none' : `transform ${phase === 'exiting' ? EXIT_DURATION : 500}ms ${SPRING}`,
          paddingBottom: 'env(safe-area-inset-bottom, 0px)',
        }}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
      >
        {/* Grab handle — match WatchlistSheet size/weight */}
        <div
          data-sheet-handle
          className="flex shrink-0 cursor-grab justify-center active:cursor-grabbing"
          style={{ padding: showingDetail ? '14px 0 22px' : '12px 0 8px' }}
        >
          <div style={{ width: 40, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)' }} />
        </div>

        {/* Header — clean title like Watchlist; no X/back; dismiss via swipe/scrim */}
        <div className={cn('flex shrink-0 items-center gap-2 px-4 pb-3', showingDetail && 'pt-1')}>
          <h2 className="min-w-0 flex-1 truncate text-[18px] font-bold text-white">
            {showingDetail ? (detail?.name || 'Collection') : 'Collections'}
          </h2>
          {!showingDetail && (
            <button
              type="button"
              onClick={() => void handleRefresh()}
              disabled={refreshing || building}
              className="flex h-8 w-8 items-center justify-center rounded-full bg-white/5 text-white/60 disabled:opacity-40 active:bg-white/10"
              aria-label="Refresh"
            >
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className={cn((refreshing || building) && 'animate-spin')}
              >
                <polyline points="23 4 23 10 17 10" />
                <polyline points="1 20 1 14 7 14" />
                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
              </svg>
            </button>
          )}
        </div>

        {/* Body */}
        <div
          data-scroll-container
          className="luma-scroll-y min-h-0 flex-1 overscroll-contain px-1"
          style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}
        >
          {showingDetail ? (
            detailLoading || !detail ? (
              <div className="flex flex-col items-center justify-center py-20 text-white/30">
                <div className="h-6 w-6 animate-spin rounded-full border-2 border-white/10 border-t-white/60" />
                <p className="mt-4 text-[14px]">Loading…</p>
              </div>
            ) : (
              <div className="divide-y divide-white/5">
                {sortedDetailParts.map((part) => (
                  <PartRow key={part.tmdb_id} part={part} onClick={() => handlePartClick(part)} />
                ))}
              </div>
            )
          ) : loading && collections.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-white/30">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-white/10 border-t-white/60" />
              <p className="mt-4 text-[14px]">
                {building ? 'Finding incomplete collections…' : 'Loading…'}
              </p>
              <p className="mt-1 max-w-[260px] text-center text-[12px] text-white/20">
                First scan looks up TMDb franchises for your Movies library — usually a minute or two.
              </p>
            </div>
          ) : collections.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-white/30">
              <p className="text-[14px]">No incomplete collections</p>
              <p className="mt-1 max-w-[280px] text-center text-[12px] text-white/20">
                Every franchise you own a part of is complete in Movies / Recently Released.
              </p>
            </div>
          ) : (
            <div className="divide-y divide-white/5">
              {collections.map((c) => (
                <CollectionRow key={c.id} item={c} onClick={() => void openCollection(c.id)} />
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
