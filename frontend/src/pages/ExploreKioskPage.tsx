/**
 * Kiosk Explore Page — platform-quality cinematic browsing with smart tiles.
 *
 * Rails:
 *   1. Trending Movies (Trakt — conditional)
 *   2. Trending Shows  (Trakt — conditional)
 *   3. In Theaters     (TMDb now_playing)
 *   4. Now Available   (indexer cache)
 *
 * Each rail loads data in parallel and renders immediately.
 * Ownership enrichment runs as a single consolidated batch after all fetches settle.
 *
 * Web/tablet: poster scroll rails (PosterRail + ExplorePosterTile)
 * TV mode: landscape scroll rails (ScrollRail + FocusZone + ExploreTile)
 */

import { useCallback, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router';
import { useModalHost, type ModalSource } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { useBodyScroll } from '@/hooks/useBodyScroll';
import { ExploreTile } from '@/components/media/ExploreTile';
import { ExplorePosterTile } from '@/components/media/ExplorePosterTile';
import { FocusZone } from '@/components/tv/FocusZone';
import { TopBar } from '@/components/layout/TopBar';
import { useTVMode } from '@/contexts/TVModeContext';
import { useUiDensity } from '@/hooks/useUiDensity';
import { uiDensityTokens } from '@/ui/uiDensityTokens';
import { MobileExplorePage } from './MobileExplorePage';
import { useRequireService } from '@/components/connect/connectService';
import { ConnectServiceCard } from '@/components/connect/ConnectServiceCard';
import { normalizeForTiles } from '@/utils/exploreNormalizer';
import { useExploreRails, type ExploreRailState } from '@/features/explore/useExploreRails';
import type { RailId } from '@/features/explore/exploreRails';
import type { NormalizedMediaItem } from '@/types/media';
import type { RailContext } from '@/types/explore';

// ---------------------------------------------------------------------------
// Modal source mapping per rail
// ---------------------------------------------------------------------------

const RAIL_MODAL_SOURCE: Record<RailId, string> = {
  trakt_movies: 'trakt_movies',
  trakt_shows: 'trakt_shows',
  trakt_anticipated_movies: 'trakt_movies',
  trakt_anticipated_shows: 'trakt_shows',
  in_theaters: 'explore_in_theaters',
  now_available: 'explore_trackers',
};

// ---------------------------------------------------------------------------
// PosterRail sub-component (web/tablet — portrait poster scroll rail)
// ---------------------------------------------------------------------------

function PosterRail({
  rail,
  railIndex,
  onCardClick,
  seeMorePath,
}: {
  rail: ExploreRailState;
  railIndex: number;
  onCardClick: (item: NormalizedMediaItem) => void;
  seeMorePath?: string;
}) {
  const navigate = useNavigate();
  const d = useUiDensity();
  const tk = uiDensityTokens(d);
  const scrollRef = useRef<HTMLDivElement>(null);
  const dragState = useRef({ down: false, startX: 0, startY: 0, scrollLeft: 0, moved: false });
  const railTitleSize = tk.typography.railTitle;

  const tiles = useMemo(
    () => normalizeForTiles(rail.items, rail.id as RailContext),
    [rail.items, rail.id],
  );

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    const el = scrollRef.current;
    if (!el) return;
    dragState.current = { down: true, startX: e.pageX, startY: 0, scrollLeft: el.scrollLeft, moved: false };
    el.style.cursor = 'grabbing';
  }, []);

  const onMouseMove = useCallback((e: React.MouseEvent) => {
    const ds = dragState.current;
    if (!ds.down) return;
    e.preventDefault();
    const dx = e.pageX - ds.startX;
    if (Math.abs(dx) > 5) ds.moved = true;
    scrollRef.current!.scrollLeft = ds.scrollLeft - dx;
  }, []);

  const onMouseUp = useCallback(() => {
    dragState.current.down = false;
    if (scrollRef.current) scrollRef.current.style.cursor = 'grab';
  }, []);

  // Touch: native scroll handles movement, but we still need to flag a swipe so
  // a tap that ends after a horizontal fling doesn't open a modal (the click
  // guard is otherwise mouse-only).
  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const t = e.touches[0];
    if (!t) return;
    dragState.current.startX = t.pageX;
    dragState.current.startY = t.pageY;
    dragState.current.moved = false;
  }, []);

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    const t = e.touches[0];
    if (!t) return;
    if (Math.abs(t.pageX - dragState.current.startX) > 8 ||
        Math.abs(t.pageY - dragState.current.startY) > 8) {
      dragState.current.moved = true;
    }
  }, []);

  if (rail.isLoading) {
    return (
      <section className="mb-10">
        <h2 className={`mb-4 pl-6 ${railTitleSize} font-bold text-text-primary`}>
          {rail.title}
        </h2>
        <div className="flex gap-4 pl-6">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="w-[220px] shrink-0" data-poster-tile>
              <div className="aspect-[2/3] animate-pulse rounded-xl bg-glass-5" />
              <div className="mt-2 h-4 w-3/4 animate-pulse rounded bg-glass-5" />
            </div>
          ))}
        </div>
      </section>
    );
  }

  if (rail.items.length === 0) return null;

  return (
    <section
      className="explore-rail mb-10"
      style={{ '--rail-delay': `${railIndex * 100}ms` } as React.CSSProperties}
    >
      <h2 className={`mb-4 pl-6 ${railTitleSize} font-bold text-text-primary`}>
        {rail.title}
      </h2>

      <div
        ref={scrollRef}
        className="flex gap-4 overflow-x-auto pb-4 snap-x snap-proximity select-none"
        style={{ scrollbarWidth: 'none', WebkitOverflowScrolling: 'touch', cursor: 'grab', scrollPaddingLeft: '1.5rem' }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
      >
        {/* Left spacer */}
        <div className="shrink-0 w-6" aria-hidden />
        {tiles.map((tile, i) => (
          <ExplorePosterTile
            key={tile.id}
            tile={tile}
            onClick={() => {
              if (!dragState.current.moved) onCardClick(rail.items[i]!);
              dragState.current.moved = false;
            }}
          />
        ))}
        {seeMorePath && (
          <button
            type="button"
            onClick={() => navigate(seeMorePath)}
            className="group w-[220px] shrink-0 snap-start outline-none focus-visible:ring-2 focus-visible:ring-accent/60 rounded-xl"
          >
            <div className="flex aspect-[2/3] flex-col items-center justify-center gap-3 rounded-xl bg-glass-10 transition-colors group-hover:bg-glass-20">
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-text-muted group-hover:text-text-primary transition-colors">
                <path d="M5 12h14" />
                <path d="m12 5 7 7-7 7" />
              </svg>
              <span className="text-[15px] font-medium text-text-muted group-hover:text-text-primary transition-colors">See more</span>
            </div>
          </button>
        )}
        {/* Right spacer — prevents last poster from clipping at modal edge */}
        <div className="shrink-0 w-16" aria-hidden />
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// ScrollRail sub-component (TV mode — landscape tile scroll rail)
// ---------------------------------------------------------------------------

function ScrollRail({
  rail,
  railIndex,
  onCardClick,
  seeMorePath,
}: {
  rail: ExploreRailState;
  railIndex: number;
  onCardClick: (item: NormalizedMediaItem) => void;
  seeMorePath?: string;
}) {
  const navigate = useNavigate();
  const d = useUiDensity();
  const tk = uiDensityTokens(d);
  const scrollRef = useRef<HTMLDivElement>(null);
  const dragState = useRef({ down: false, startX: 0, startY: 0, scrollLeft: 0, moved: false });
  const railGap = tk.spacing.railGap;

  const tiles = useMemo(
    () => normalizeForTiles(rail.items, rail.id as RailContext),
    [rail.items, rail.id],
  );

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    const el = scrollRef.current;
    if (!el) return;
    dragState.current = { down: true, startX: e.pageX, startY: 0, scrollLeft: el.scrollLeft, moved: false };
    el.style.cursor = 'grabbing';
  }, []);

  const onMouseMove = useCallback((e: React.MouseEvent) => {
    const ds = dragState.current;
    if (!ds.down) return;
    e.preventDefault();
    const dx = e.pageX - ds.startX;
    if (Math.abs(dx) > 5) ds.moved = true;
    scrollRef.current!.scrollLeft = ds.scrollLeft - dx;
  }, []);

  const onMouseUp = useCallback(() => {
    dragState.current.down = false;
    if (scrollRef.current) scrollRef.current.style.cursor = 'grab';
  }, []);

  // Touch: native scroll handles movement, but we still need to flag a swipe so
  // a tap that ends after a horizontal fling doesn't open a modal (the click
  // guard is otherwise mouse-only).
  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const t = e.touches[0];
    if (!t) return;
    dragState.current.startX = t.pageX;
    dragState.current.startY = t.pageY;
    dragState.current.moved = false;
  }, []);

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    const t = e.touches[0];
    if (!t) return;
    if (Math.abs(t.pageX - dragState.current.startX) > 8 ||
        Math.abs(t.pageY - dragState.current.startY) > 8) {
      dragState.current.moved = true;
    }
  }, []);

  if (rail.isLoading) {
    return (
      <section className="mb-10">
        <h2 className="mb-6 pl-16 text-[44px] font-bold text-text-primary">
          {rail.title}
        </h2>
        <div className={`flex ${railGap}`}>
          <div className="shrink-0 w-14" aria-hidden />
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="w-[420px] shrink-0" data-explore-tile>
              <div className="aspect-video animate-pulse rounded-2xl bg-glass-5" />
            </div>
          ))}
        </div>
      </section>
    );
  }

  if (rail.items.length === 0) return null;

  return (
    <section
      className="explore-rail mb-10"
      style={{ '--rail-delay': `${railIndex * 100}ms` } as React.CSSProperties}
    >
      <h2 className="mb-6 pl-16 text-[44px] font-bold text-text-primary">
        {rail.title}
      </h2>

      <div
        ref={scrollRef}
        className={`flex ${railGap} overflow-x-auto pb-4 snap-x snap-proximity select-none`}
        style={{ scrollbarWidth: 'none', WebkitOverflowScrolling: 'touch', cursor: 'grab' }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
      >
        <div className="shrink-0 w-16" aria-hidden />
        <FocusZone zoneId={`explore-rail-${railIndex}`} orientation="horizontal">
          {tiles.map((tile, i) => (
            <ExploreTile
              key={tile.id}
              tile={tile}
              index={i}
              railIndex={railIndex}
              onClick={() => {
                if (!dragState.current.moved) onCardClick(rail.items[i]!);
                dragState.current.moved = false;
              }}
            />
          ))}
        </FocusZone>
        {seeMorePath && (
          <button
            type="button"
            onClick={() => navigate(seeMorePath)}
            className="group w-[420px] shrink-0 snap-start outline-none focus-visible:ring-2 focus-visible:ring-accent/60 rounded-2xl"
          >
            <div className="flex aspect-video flex-col items-center justify-center gap-3 rounded-2xl bg-glass-10 transition-colors group-hover:bg-glass-20">
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-text-muted group-hover:text-text-primary transition-colors">
                <path d="M5 12h14" />
                <path d="m12 5 7 7-7 7" />
              </svg>
              <span className="text-lg font-medium text-text-muted group-hover:text-text-primary transition-colors">See more</span>
            </div>
          </button>
        )}
        {/* Right spacer — prevents last tile from clipping at modal edge */}
        <div className="shrink-0 w-16" aria-hidden />
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export function ExploreKioskPage() {
  const { mode } = useUiDensity();
  // Discover is built on TMDB; without a key show the Connect TMDB state.
  const tmdb = useRequireService('tmdb');
  if (tmdb === null) return <div className="bg-kiosk-black" style={{ minHeight: '100dvh' }} />;
  if (tmdb === false) {
    return (
      <div className="bg-kiosk-black" style={{ minHeight: '100dvh', paddingTop: 60 }}>
        <ConnectServiceCard service="tmdb" />
      </div>
    );
  }
  if (mode === 'compact') return <MobileExplorePage />;

  return <DesktopExplore />;
}

function DesktopExplore() {
  useBodyScroll();
  const { isTvMode } = useTVMode();
  const rails = useExploreRails();

  const { openMovie, openTv, close } = useModalHost();
  const navigate = useNavigate();

  const makePersonSearch = useCallback((name: string, personId?: number) => {
    close();
    const params = new URLSearchParams({ q: name, type: 'people' });
    if (personId) params.set('personId', String(personId));
    navigate(`/?${params.toString()}`);
  }, [close, navigate]);

  const handleCardClickWithSource = useCallback((source: string) => (item: NormalizedMediaItem) => {
    openModalForItem(
      { openMovie, openTv },
      item,
      source as ModalSource,
      { onPersonSearch: makePersonSearch },
    );
  }, [openMovie, openTv, makePersonSearch]);

  // Every rail self-hides when empty; without a page-level fallback the whole
  // page goes blank (e.g. all sources unreachable). Show an empty/retry state.
  const allSettledEmpty = rails.length > 0 && rails.every((r) => !r.isLoading && r.items.length === 0);

  return (
    <div className="bg-kiosk-black" style={{ minHeight: '100dvh' }}>
      {isTvMode && <TopBar />}

      {allSettledEmpty && (
        <div className="flex flex-col items-center justify-center gap-4 px-6 text-center" style={{ minHeight: '80dvh' }}>
          <p className="text-xl font-semibold text-text-primary">Nothing to explore right now</p>
          <p className="max-w-md text-sm text-text-muted">
            We couldn't load any titles. Check your Plex, indexer, and TMDb connections, then try again.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-lg bg-glass-10 px-5 py-2.5 text-sm font-medium text-text-primary transition-colors hover:bg-glass-20"
          >
            Retry
          </button>
        </div>
      )}

      {/* Content — rails render immediately with per-rail skeleton loading */}
      <div className="pb-20 pt-20">
        {isTvMode
          ? rails.map((r, i) => (
              <ScrollRail
                key={r.id}
                rail={r}
                railIndex={i}
                onCardClick={handleCardClickWithSource(RAIL_MODAL_SOURCE[r.id])}
                seeMorePath={r.id === 'now_available' ? '/now-available' : undefined}
              />
            ))
          : rails.map((r, i) => (
              <PosterRail
                key={r.id}
                rail={r}
                railIndex={i}
                onCardClick={handleCardClickWithSource(RAIL_MODAL_SOURCE[r.id])}
                seeMorePath={r.id === 'now_available' ? '/now-available' : undefined}
              />
            ))
        }
      </div>
    </div>
  );
}
