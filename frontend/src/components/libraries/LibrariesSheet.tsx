import { useState, useEffect, useCallback, useRef, memo } from 'react';
import { createPortal } from 'react-dom';
import { useModalHost, type ModalSource } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { cn } from '@/utils/cn';
import { fetchMediaPaths, fetchLibraryBrowse, fetchLibraryGenres } from '@/services/libraryBrowseApi';
import type { MediaPath, BrowseItem } from '@/services/libraryBrowseApi';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DISMISS_THRESHOLD = 120;
const ENTER_DURATION = 600;
const EXIT_DURATION = 400;
const SPRING = 'cubic-bezier(0.16, 1, 0.3, 1)';
const PAGE_SIZE = 30;

// ---------------------------------------------------------------------------
// Icons & cards
// ---------------------------------------------------------------------------

function MovieIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-white/40">
      <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18" />
      <line x1="7" y1="2" x2="7" y2="22" /><line x1="17" y1="2" x2="17" y2="22" />
      <line x1="2" y1="12" x2="22" y2="12" />
      <line x1="2" y1="7" x2="7" y2="7" /><line x1="2" y1="17" x2="7" y2="17" />
      <line x1="17" y1="7" x2="22" y2="7" /><line x1="17" y1="17" x2="22" y2="17" />
    </svg>
  );
}

function TvIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-white/40">
      <rect x="2" y="7" width="20" height="15" rx="2" ry="2" />
      <polyline points="17 2 12 7 7 2" />
    </svg>
  );
}

interface LibraryRow {
  name: string;
  type: string;
  count: number | null;
}

const PosterCard = memo(function PosterCard({ item, onClick, revealed, onImgReady }: { item: BrowseItem; onClick: () => void; revealed: boolean; onImgReady: () => void }) {
  return (
    <button type="button" onClick={onClick} className="w-full text-left">
      <div className="relative aspect-[2/3] overflow-hidden rounded-lg bg-white/5">
        {item.poster_url ? (
          <img
            src={item.poster_url} alt={item.title} draggable={false}
            className={cn('h-full w-full object-cover transition-opacity duration-300', revealed ? 'opacity-100' : 'opacity-0')}
            onLoad={onImgReady}
            onError={onImgReady}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-white/20 text-[10px]">No poster</div>
        )}
      </div>
      <div className={cn('transition-opacity duration-300', revealed ? 'opacity-100' : 'opacity-0')}>
        <p className="mt-1.5 truncate text-[12px] font-medium text-white/80">{item.title}</p>
        {item.year && <p className="truncate text-[11px] text-white/30">{item.year}</p>}
      </div>
    </button>
  );
});

// ---------------------------------------------------------------------------
// Sort / filter types
// ---------------------------------------------------------------------------

type SortKey = 'added_date' | 'title' | 'year' | 'rating';

const SORT_OPTIONS: { key: SortKey; label: string; dir: 'desc' | 'asc' }[] = [
  { key: 'added_date', label: 'Date added', dir: 'desc' },
  { key: 'title', label: 'A-Z', dir: 'asc' },
  { key: 'year', label: 'Year', dir: 'desc' },
  { key: 'rating', label: 'Rating', dir: 'desc' },
];

type FilterPanel = 'none' | 'main' | 'genre';

// ---------------------------------------------------------------------------
// LibrariesSheet
// ---------------------------------------------------------------------------

interface LibrariesSheetProps {
  open: boolean;
  onClose: () => void;
}

export function LibrariesSheet({ open, onClose }: LibrariesSheetProps) {
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

  // --- Navigation state ---
  const [selectedLibrary, setSelectedLibrary] = useState<{ name: string; mediaType: 'movie' | 'tv' } | null>(null);

  const handleDismiss = useCallback(() => {
    // If browsing a library, go back to library list instead of closing
    if (selectedLibrary) {
      setDragOffset(0);
      setSelectedLibrary(null);
      return;
    }
    setDragOffset(0);
    requestAnimationFrame(() => {
      setPhase('exiting');
      setTimeout(() => { setPhase('gone'); onClose(); }, EXIT_DURATION);
    });
  }, [onClose, selectedLibrary]);

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
    if (open) setSelectedLibrary(null);
  }, [open]);

  // --- Library list data ---
  const [paths, setPaths] = useState<MediaPath[]>([]);
  const [counts, setCounts] = useState<Map<string, number>>(new Map());
  const [loadingPaths, setLoadingPaths] = useState(true);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoadingPaths(true);
    fetchMediaPaths().then((p) => {
      if (!cancelled) { setPaths(p); setLoadingPaths(false); }
    });
    return () => { cancelled = true; };
  }, [open]);

  useEffect(() => {
    if (paths.length === 0) return;
    let cancelled = false;
    Promise.all(
      paths.map(async (p) => {
        const resp = await fetchLibraryBrowse({ library: p.name, limit: 0 });
        return { name: p.name, total: resp.total };
      }),
    ).then((results) => {
      if (cancelled) return;
      const map = new Map<string, number>();
      for (const r of results) map.set(r.name, r.total);
      setCounts(map);
    });
    return () => { cancelled = true; };
  }, [paths]);

  const movieLibraries: LibraryRow[] = [];
  const tvLibraries: LibraryRow[] = [];
  for (const p of paths) {
    const row: LibraryRow = { name: p.name, type: p.type, count: counts.get(p.name) ?? null };
    if (p.type.toLowerCase().includes('tv')) tvLibraries.push(row);
    else movieLibraries.push(row);
  }

  // --- Browse grid data ---
  const [items, setItems] = useState<BrowseItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loadingGrid, setLoadingGrid] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>('added_date');
  const [genres, setGenres] = useState<string[]>([]);
  const [genre, setGenre] = useState('');
  const [filterPanel, setFilterPanel] = useState<FilterPanel>('none');

  // Batch image loading — show skeletons until enough posters are ready
  const imgReadyCount = useRef(0);
  const [postersRevealed, setPostersRevealed] = useState(false);

  // Reset reveal state when items change (new library, sort, genre, etc.)
  useEffect(() => {
    imgReadyCount.current = 0;
    setPostersRevealed(false);
  }, [selectedLibrary, sortKey, genre]);

  const handleImgReady = useCallback(() => {
    imgReadyCount.current += 1;
    // Reveal once first visible row (3 cols) is loaded, or all images are done
    if (!postersRevealed && imgReadyCount.current >= Math.min(items.length, 6)) {
      setPostersRevealed(true);
    }
  }, [postersRevealed, items.length]);

  // Also reveal after a timeout so we don't block forever on slow/missing images
  useEffect(() => {
    if (postersRevealed || items.length === 0) return;
    const t = setTimeout(() => setPostersRevealed(true), 3000);
    return () => clearTimeout(t);
  }, [postersRevealed, items.length]);

  useEffect(() => {
    if (!selectedLibrary) return;
    let cancelled = false;
    setLoadingGrid(true);
    setItems([]);
    const sortOpt = SORT_OPTIONS.find(s => s.key === sortKey)!;
    fetchLibraryBrowse({
      library: selectedLibrary.name,
      offset: 0,
      limit: PAGE_SIZE,
      sort: sortOpt.key,
      direction: sortOpt.dir,
      genre: genre || undefined,
    }).then((resp) => {
      if (!cancelled) { setItems(resp.items); setTotal(resp.total); setLoadingGrid(false); }
    });
    return () => { cancelled = true; };
  }, [selectedLibrary, sortKey, genre]);

  useEffect(() => {
    if (!selectedLibrary) return;
    let cancelled = false;
    fetchLibraryGenres(selectedLibrary.name).then(g => { if (!cancelled) setGenres(g); });
    return () => { cancelled = true; };
  }, [selectedLibrary]);

  const handleLoadMore = useCallback(async () => {
    if (!selectedLibrary) return;
    setLoadingMore(true);
    const sortOpt = SORT_OPTIONS.find(s => s.key === sortKey)!;
    const resp = await fetchLibraryBrowse({
      library: selectedLibrary.name,
      offset: items.length,
      limit: PAGE_SIZE,
      sort: sortOpt.key,
      direction: sortOpt.dir,
      genre: genre || undefined,
    });
    setItems(prev => [...prev, ...resp.items]);
    setTotal(resp.total);
    setLoadingMore(false);
  }, [selectedLibrary, sortKey, genre, items.length]);

  const handleSelectLibrary = useCallback((name: string, type: string) => {
    const mediaType = type.toLowerCase().includes('tv') ? 'tv' as const : 'movie' as const;
    setSelectedLibrary({ name, mediaType });
    setSortKey('added_date');
    setGenre('');
    setFilterPanel('none');
  }, []);

  // --- Modal ---
  const [behindModal, setBehindModal] = useState(false);

  const handleCardClick = useCallback((item: BrowseItem) => {
    if (!selectedLibrary) return;
    setBehindModal(true);
    openModalForItem(
      { openMovie, openTv },
      {
        mediaType: selectedLibrary.mediaType,
        tmdbId: item.tmdb_id,
        ratingKey: item.rating_key,
      },
      'browse' as ModalSource,
    );
  }, [openMovie, openTv, selectedLibrary]);

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

  const hasMore = items.length < total;
  const activeSortLabel = SORT_OPTIONS.find(s => s.key === sortKey)!.label;

  return createPortal(
    <div className="luma-fullscreen luma-bleed"
      data-sheet="LibrariesSheet"
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
      <div className="luma-bleed-panel"
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
          style={{ flexShrink: 0, display: 'flex', justifyContent: 'center', padding: '12px 0 4px', cursor: 'grab' }}
        >
          <div style={{ width: 40, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)' }} />
        </div>

        {/* Header */}
        <div className="shrink-0 px-4 pt-4 pb-4">
          {selectedLibrary ? (
              <h2 className="text-[24px] font-bold text-white text-center tracking-tight">
                {selectedLibrary.name}
              </h2>
          ) : (
            <h2 className="text-[24px] font-bold text-white text-center tracking-tight">Libraries</h2>
          )}
        </div>

        {/* Filter — single dropdown pill, only in grid view */}
        {selectedLibrary && (
          <div className="shrink-0 px-4 pb-5" style={{ position: 'relative' }}>
            <button
              type="button"
              onClick={() => setFilterPanel(p => p !== 'none' ? 'none' : 'main')}
              className={cn(
                'flex items-center gap-2 rounded-xl px-5 py-3 text-[15px] font-medium transition-colors',
                filterPanel !== 'none' || sortKey !== 'added_date' || genre
                  ? 'bg-white/15 text-white/90'
                  : 'bg-white/8 text-white/60',
              )}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="4" y1="6" x2="20" y2="6" /><line x1="4" y1="12" x2="14" y2="12" /><line x1="4" y1="18" x2="8" y2="18" />
              </svg>
              {genre ? `${activeSortLabel} / ${genre}` : activeSortLabel}
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="ml-auto">
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </button>

            {/* Popover — positioned directly below the pill */}
            {filterPanel !== 'none' && (
              <>
                <div className="fixed inset-0 z-[10002]" onClick={() => setFilterPanel('none')} />
                <div
                  className="absolute left-4 z-[10003] rounded-2xl bg-[#1c1c1e] border border-white/10 overflow-hidden shadow-2xl"
                  style={{
                    top: '100%',
                    marginTop: 6,
                    minWidth: 220,
                    maxHeight: filterPanel === 'genre' ? '50vh' : 'auto',
                    animation: 'filterSlideIn 200ms ease-out',
                  }}
                >
                  {filterPanel === 'main' && (
                    <div className="py-1">
                      {SORT_OPTIONS.map(opt => (
                        <button
                          key={opt.key}
                          type="button"
                          onClick={() => { setSortKey(opt.key); setFilterPanel('none'); }}
                          className={cn(
                            'flex w-full items-center justify-between px-5 py-3.5 text-left text-[16px] active:bg-white/5',
                            sortKey === opt.key && !genre ? 'text-blue-400 font-semibold' : 'text-white/80',
                          )}
                        >
                          {opt.label}
                          {sortKey === opt.key && !genre && (
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                              <polyline points="20 6 9 17 4 12" />
                            </svg>
                          )}
                        </button>
                      ))}
                      {genres.length > 0 && (
                        <>
                          <div className="mx-4 border-t border-white/8" />
                          <button
                            type="button"
                            onClick={() => setFilterPanel('genre')}
                            className={cn(
                              'flex w-full items-center justify-between px-5 py-3.5 text-left text-[16px] active:bg-white/5',
                              genre ? 'text-blue-400 font-semibold' : 'text-white/80',
                            )}
                          >
                            <span>{genre ? `Genre: ${genre}` : 'Genre'}</span>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-white/30">
                              <polyline points="9 18 15 12 9 6" />
                            </svg>
                          </button>
                        </>
                      )}
                    </div>
                  )}

                  {filterPanel === 'genre' && (
                    <div>
                      {/* Back to main */}
                      <button
                        type="button"
                        onClick={() => setFilterPanel('main')}
                        className="flex w-full items-center gap-2 px-4 py-3 text-left text-[13px] text-white/40 active:bg-white/5 border-b border-white/8"
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="15 18 9 12 15 6" />
                        </svg>
                        Back
                      </button>
                      <div data-scroll-container className="luma-scroll-y py-1" style={{ maxHeight: 'calc(50vh - 44px)', WebkitOverflowScrolling: 'touch', touchAction: 'pan-y', overscrollBehavior: 'contain' }}>
                        <button
                          type="button"
                          onClick={() => { setGenre(''); setFilterPanel('none'); }}
                          className={cn(
                            'flex w-full items-center justify-between px-5 py-3.5 text-left text-[16px] active:bg-white/5',
                            !genre ? 'text-blue-400 font-semibold' : 'text-white/80',
                          )}
                        >
                          All Genres
                          {!genre && (
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                              <polyline points="20 6 9 17 4 12" />
                            </svg>
                          )}
                        </button>
                        {genres.map(g => (
                          <button
                            key={g}
                            type="button"
                            onClick={() => { setGenre(g); setFilterPanel('none'); }}
                            className={cn(
                              'flex w-full items-center justify-between px-5 py-3.5 text-left text-[16px] active:bg-white/5',
                              genre === g ? 'text-blue-400 font-semibold' : 'text-white/80',
                            )}
                          >
                            {g}
                            {genre === g && (
                              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="20 6 9 17 4 12" />
                              </svg>
                            )}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                <style>{`
                  @keyframes filterSlideIn {
                    from { opacity: 0; transform: scale(0.95) translateY(-4px); }
                    to { opacity: 1; transform: scale(1) translateY(0); }
                  }
                `}</style>
              </>
            )}
          </div>
        )}

        {/* Scrollable content */}
        <div
          data-scroll-container
          className="luma-scroll-y flex-1"
          style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}
        >
          {!selectedLibrary ? (
            /* ---- Library list view ---- */
            loadingPaths ? (
              <div className="flex flex-col items-center justify-center py-20 text-white/30">
                <div className="h-6 w-6 animate-spin rounded-full border-2 border-white/10 border-t-white/60" />
                <p className="mt-4 text-[14px]">Loading...</p>
              </div>
            ) : paths.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 text-white/30">
                <p className="text-[14px]">No libraries configured</p>
              </div>
            ) : (
              <div className="px-4">
                {movieLibraries.length > 0 && (
                  <div className="mb-6">
                    <h3 className="text-[13px] font-semibold text-white/40 mb-3 px-1">Movies</h3>
                    <div className="rounded-2xl bg-white/5 overflow-hidden divide-y divide-white/5">
                      {movieLibraries.map(row => (
                        <button
                          key={row.name} type="button"
                          onClick={() => handleSelectLibrary(row.name, row.type)}
                          className="flex w-full items-center gap-4 px-4 py-4 text-left active:bg-white/5"
                        >
                          <MovieIcon />
                          <span className="flex-1 truncate text-[16px] font-medium text-white/90">{row.name}</span>
                          {row.count != null ? (
                            <span className="luma-fade-in text-[14px] text-white/30">{row.count.toLocaleString()}</span>
                          ) : (
                            <span className="h-4 w-10" aria-hidden="true" />
                          )}
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-white/20">
                            <polyline points="9 18 15 12 9 6" />
                          </svg>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {tvLibraries.length > 0 && (
                  <div className="mb-6">
                    <h3 className="text-[13px] font-semibold text-white/40 mb-3 px-1">TV shows</h3>
                    <div className="rounded-2xl bg-white/5 overflow-hidden divide-y divide-white/5">
                      {tvLibraries.map(row => (
                        <button
                          key={row.name} type="button"
                          onClick={() => handleSelectLibrary(row.name, row.type)}
                          className="flex w-full items-center gap-4 px-4 py-4 text-left active:bg-white/5"
                        >
                          <TvIcon />
                          <span className="flex-1 truncate text-[16px] font-medium text-white/90">{row.name}</span>
                          {row.count != null ? (
                            <span className="luma-fade-in text-[14px] text-white/30">{row.count.toLocaleString()}</span>
                          ) : (
                            <span className="h-4 w-10" aria-hidden="true" />
                          )}
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-white/20">
                            <polyline points="9 18 15 12 9 6" />
                          </svg>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )
          ) : (
            /* ---- Grid browse view ---- */
            loadingGrid ? (
              <div className="grid grid-cols-3 gap-3 px-4" data-testid="libraries-reserved" aria-hidden="true">
                {Array.from({ length: 9 }).map((_, i) => (
                  <div key={i}>
                    <div className="aspect-[2/3]" />
                    <div className="mt-1.5 h-3" />
                  </div>
                ))}
              </div>
            ) : items.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 text-white/30">
                <p className="text-[14px]">No items found</p>
              </div>
            ) : (
              <div className="px-4 luma-fade-in">
                <div className="grid grid-cols-3 gap-3">
                  {items.map((item, idx) => (
                    <PosterCard key={`${item.rating_key}-${idx}`} item={item} onClick={() => handleCardClick(item)} revealed={postersRevealed} onImgReady={handleImgReady} />
                  ))}
                </div>
                {hasMore && (
                  <div className="mt-6 mb-4 flex justify-center">
                    <button
                      type="button" onClick={handleLoadMore} disabled={loadingMore}
                      className={cn(
                        'rounded-xl px-5 py-2.5 text-[14px] font-medium bg-white/8 text-white/60 active:bg-white/12',
                        loadingMore && 'opacity-50',
                      )}
                    >
                      {loadingMore ? 'Loading...' : 'Load more'}
                    </button>
                  </div>
                )}
              </div>
            )
          )}

          <div className="shrink-0" style={{ height: 'env(safe-area-inset-bottom, 12px)' }} />
        </div>
      </div>
    </div>,
    document.body,
  );
}
