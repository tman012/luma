/**
 * AuthenticatedHomePage — Mobile/compact authenticated home page with rails.
 *
 * Layout:
 *   - Top bar: Luma text logo (left), search icon + user icon (right)
 *   - Content rails: horizontal scroll poster rows
 *   - Navigation pills after Continue Watching: Storage, Latest, Activity,
 *     Luma AI, Library, Watchlist, TV Calendar, Rooms, Upgrades
 *   - "See More" at end of Now Available rail → Explore page
 *   - Clickable items open MediaDetailModal or TvDetailModal
 */

import React, { useRef, useState, useMemo, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router';
import { UserMenu } from '@/components/UserMenu';
import { MobilePushBanner } from '@/components/mobile/MobilePushBanner';
import { RoomsSheet } from '@/components/rooms/RoomsSheet';
import { UpgradesSheet } from '@/components/upgrades/UpgradesSheet';
import { LatestSheet } from '@/components/latest/LatestSheet';
import { ActivitySheet } from '@/components/activity/ActivitySheet';
import { WatchlistSheet } from '@/components/watchlist/WatchlistSheet';
import { CollectionsSheet } from '@/components/collections/CollectionsSheet';
import { LumaAISheet } from '@/components/luma/LumaAISheet';
import { TvCalendarSheet } from '@/components/calendar/TvCalendarSheet';
import { LibrariesSheet } from '@/components/libraries/LibrariesSheet';
import { useBodyScroll } from '@/hooks/useBodyScroll';
import { usePullToRefresh } from '@/hooks/usePullToRefresh';
import { useHomeRails, type HomeRailState } from '@/features/home/useHomeRails';
import { DismissMissingSheet } from '@/features/home/DismissMissingSheet';
import { dismissWhatYoureMissing } from '@/features/home/wymDismissApi';
import type { HomeRailId } from '@/features/home/homeRails';
import type { NormalizedMediaItem } from '@/types/media';
import { useModalHost, type ModalSource } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { StoragePill } from '@/components/storage/StoragePill';
import { SearchShelf } from '@/components/search/SearchShelf';
import { apiFetch } from '../apiClient';
import { assetUrl } from '@/urlHelpers';

// ---------------------------------------------------------------------------
// Rail ID -> ModalSource mapping
// ---------------------------------------------------------------------------

const RAIL_MODAL_SOURCE: Record<HomeRailId, ModalSource> = {
  continue_watching: 'continue_watching',
  what_youre_missing: 'trakt_movies',
  upcoming_tv: 'tv_calendar',
  watchlist: 'watchlist',
  recent_movies: 'recently_added_movies',
  recent_tv: 'recently_added_tv',
  recently_watched: 'recently_watched',
};

// ---------------------------------------------------------------------------
// Poster card
// ---------------------------------------------------------------------------

const POSTER_W = 116;
const POSTER_H = 174;

function PosterCard({ item, onClick, onLongPress }: {
  item: NormalizedMediaItem;
  onClick: () => void;
  onLongPress?: () => void;
}) {
  const [imgLoaded, setImgLoaded] = React.useState(false);
  const [imgError, setImgError] = React.useState(false);
  const [pressed, setPressed] = React.useState(false);
  const longTimerRef = React.useRef<number | null>(null);
  const didLongPressRef = React.useRef(false);
  const touchOriginRef = React.useRef<{ x: number; y: number } | null>(null);

  const clearLongPress = React.useCallback(() => {
    if (longTimerRef.current != null) {
      window.clearTimeout(longTimerRef.current);
      longTimerRef.current = null;
    }
  }, []);

  const fireLongPress = React.useCallback(() => {
    if (!onLongPress) return;
    didLongPressRef.current = true;
    setPressed(false);
    clearLongPress();
    try { navigator.vibrate?.(12); } catch { /* ignore */ }
    onLongPress();
  }, [onLongPress, clearLongPress]);

  const onTouchStart = React.useCallback((e: React.TouchEvent) => {
    setPressed(true);
    if (!onLongPress) return;
    const t = e.touches[0];
    if (!t) return;
    didLongPressRef.current = false;
    touchOriginRef.current = { x: t.clientX, y: t.clientY };
    clearLongPress();
    longTimerRef.current = window.setTimeout(() => {
      fireLongPress();
    }, 450);
  }, [onLongPress, clearLongPress, fireLongPress]);

  const onTouchMove = React.useCallback((e: React.TouchEvent) => {
    if (!onLongPress || longTimerRef.current == null) return;
    const t = e.touches[0];
    const origin = touchOriginRef.current;
    if (!t || !origin) return;
    const dx = Math.abs(t.clientX - origin.x);
    const dy = Math.abs(t.clientY - origin.y);
    if (dx > 10 || dy > 10) {
      clearLongPress();
      setPressed(false);
    }
  }, [onLongPress, clearLongPress]);

  const onTouchEnd = React.useCallback(() => {
    setPressed(false);
    clearLongPress();
    touchOriginRef.current = null;
  }, [clearLongPress]);

  const suppressNative = Boolean(onLongPress);

  return (
    <button
      type="button"
      onClick={() => {
        if (didLongPressRef.current) {
          didLongPressRef.current = false;
          return;
        }
        onClick();
      }}
      onContextMenu={(e) => {
        // Always kill iOS/Android image callout / context menu on these posters.
        e.preventDefault();
        e.stopPropagation();
        if (onLongPress) fireLongPress();
      }}
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
      onTouchCancel={onTouchEnd}
      onMouseDown={() => {
        setPressed(true);
        if (!onLongPress) return;
        didLongPressRef.current = false;
        clearLongPress();
        longTimerRef.current = window.setTimeout(() => fireLongPress(), 450);
      }}
      onMouseUp={() => { setPressed(false); clearLongPress(); }}
      onMouseLeave={() => { setPressed(false); clearLongPress(); }}
      style={{
        outline: 'none', background: 'none', border: 'none', padding: 0, cursor: 'pointer',
        touchAction: 'manipulation',
        transform: pressed ? 'scale(0.93)' : 'scale(1)',
        transition: pressed ? 'transform 0.1s ease-out' : 'transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1)',
        willChange: 'transform',
        // Critical for iOS: kill native gray long-press / tap highlight flash
        WebkitTapHighlightColor: 'transparent',
        WebkitTouchCallout: suppressNative ? 'none' : undefined,
        WebkitUserSelect: suppressNative ? 'none' : undefined,
        userSelect: suppressNative ? 'none' : undefined,
      } as React.CSSProperties}
      className={`group relative flex-shrink-0 text-left${suppressNative ? ' longpress-poster' : ''}`}
    >
      <div
        style={{
          width: POSTER_W, height: POSTER_H, borderRadius: 10, overflow: 'hidden',
          background: 'rgba(255,255,255,0.06)', position: 'relative', flexShrink: 0,
          WebkitTapHighlightColor: 'transparent',
          WebkitTouchCallout: suppressNative ? 'none' : undefined,
          WebkitUserSelect: suppressNative ? 'none' : undefined,
          userSelect: suppressNative ? 'none' : undefined,
        } as React.CSSProperties}
      >
        {item.posterUrl && !imgError ? (
          <img
            src={item.posterUrl}
            alt={item.title}
            loading="lazy"
            draggable={false}
            onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); }}
            onLoad={() => setImgLoaded(true)}
            onError={() => setImgError(true)}
            style={{
              width: '100%', height: '100%', objectFit: 'cover',
              opacity: imgLoaded ? 1 : 0, transition: 'opacity 0.3s ease', display: 'block',
              // Critical for iOS: image must not be the long-press target.
              pointerEvents: suppressNative ? 'none' : undefined,
              WebkitTouchCallout: 'none',
              WebkitUserSelect: 'none',
              userSelect: 'none',
            } as React.CSSProperties}
          />
        ) : (
          <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'rgba(255,255,255,0.15)', fontSize: 11 }}>
            No Poster
          </div>
        )}
        {item.progressPercent != null && item.progressPercent > 0 && (
          <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: 3, background: 'rgba(0,0,0,0.5)', borderBottomLeftRadius: 10, borderBottomRightRadius: 10, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${item.progressPercent}%`, background: '#e5a00d', borderRadius: 3 }} />
          </div>
        )}
        {/* No ownership orb in compact mode — the detail modal is the single
            source of truth for whether we have an item. */}
        {/* IMDb rating badge (bottom-right) — only for unowned items with a rating */}
        {!item.owned && item.voteAverage != null && item.voteAverage > 0 && (
          <div style={{ position: 'absolute', bottom: 6, right: 6, display: 'flex', alignItems: 'center', gap: 3, background: 'rgba(0,0,0,0.7)', borderRadius: 6, padding: '1px 5px' }}>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="#fbbf24" stroke="none">
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
            </svg>
            <span style={{ fontSize: 10, fontWeight: 600, color: 'rgba(255,255,255,0.9)' }}>{item.voteAverage.toFixed(1)}</span>
          </div>
        )}
      </div>
      <div style={{ marginTop: 6, width: POSTER_W }}>
        {item.digitalReleaseLabel && !(item.progressPercent != null && item.progressPercent > 0) && (
          <p style={{ margin: '0 0 2px', fontSize: 10, fontWeight: 600, color: 'rgba(255,255,255,0.7)', textAlign: 'center', lineHeight: 1.2 }}>
            {item.digitalReleaseLabel}
          </p>
        )}
        <p style={{ margin: 0, fontSize: 12, fontWeight: 500, color: 'rgba(255,255,255,0.88)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', lineHeight: 1.3 }}>
          {item.title}
        </p>
        {item.subtitle ? (
          <p style={{ margin: '2px 0 0', fontSize: 10, color: 'rgba(255,255,255,0.38)' }}>{item.subtitle}</p>
        ) : item.year ? (
          <p style={{ margin: '2px 0 0', fontSize: 10, color: 'rgba(255,255,255,0.38)' }}>{item.year}</p>
        ) : null}
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Skeleton card
// ---------------------------------------------------------------------------

function SkeletonCard() {
  return (
    <div style={{ flexShrink: 0 }}>
      <div className="skeleton-shimmer" style={{ width: POSTER_W, height: POSTER_H, borderRadius: 10 }} />
      <div className="skeleton-shimmer" style={{ width: 80, height: 10, borderRadius: 5, marginTop: 6 }} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Content rail
// ---------------------------------------------------------------------------

function Rail({ rail, railId, onItemClick, onItemLongPress, trailing }: {
  rail: HomeRailState;
  railId: HomeRailId;
  onItemClick: (item: NormalizedMediaItem, source: ModalSource) => void;
  onItemLongPress?: (item: NormalizedMediaItem) => void;
  trailing?: React.ReactNode;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  if (!rail.isLoading && rail.items.length === 0) return null;

  return (
    <section style={{ marginBottom: 28 }}>
      <div style={{ paddingLeft: 12, paddingRight: 12, marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: 'rgba(255,255,255,0.92)', letterSpacing: '-0.3px' }}>
          {rail.title}
        </h2>
      </div>
      <div ref={scrollRef} style={{ display: 'flex', gap: 12, paddingLeft: 12, paddingRight: 12, overflowX: 'auto', paddingBottom: 8, scrollbarWidth: 'none', msOverflowStyle: 'none' }}>
        {rail.isLoading
          ? Array.from({ length: 7 }).map((_, i) => <SkeletonCard key={i} />)
          : rail.items.map((item) => (
              <PosterCard
                key={item.id}
                item={item}
                onClick={() => onItemClick(item, RAIL_MODAL_SOURCE[railId])}
                onLongPress={onItemLongPress ? () => onItemLongPress(item) : undefined}
              />
            ))}
        {trailing}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Navigation pills
// ---------------------------------------------------------------------------

interface NavPillDef {
  label: string;
  id: string;
  path?: string;
  icon: React.ReactNode;
}

const NAV_PILLS: NavPillDef[] = [
  { label: 'Latest', id: 'latest', icon: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" />
    </svg>
  )},
  { label: 'Activity', id: 'activity', icon: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
    </svg>
  )},
  { label: 'Luma AI', id: 'luma-ai', icon: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2l2 4 4 1-3 3 1 4-4-2-4 2 1-4-3-3 4-1z" /><path d="M5 18l1.5 3 3 .5-2 2.5-.5-3z" /><path d="M19 18l-1.5 3-3 .5 2 2.5.5-3z" />
    </svg>
  )},
  { label: 'Library', id: 'library', icon: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="3" width="8" height="8" rx="1" /><rect x="14" y="3" width="8" height="8" rx="1" /><rect x="2" y="13" width="8" height="8" rx="1" /><rect x="14" y="13" width="8" height="8" rx="1" />
    </svg>
  )},
  { label: 'Watchlist', id: 'watchlist', icon: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
    </svg>
  )},
  { label: 'Collections', id: 'collections', icon: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="6" y="4" width="14" height="18" rx="2" /><path d="M6 8H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h12" /><path d="M10 10h6M10 14h6M10 18h3" />
    </svg>
  )},
  { label: 'TV calendar', id: 'tv-calendar', icon: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="18" rx="2" ry="2" /><line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" /><line x1="3" y1="10" x2="21" y2="10" />
    </svg>
  )},
  { label: 'Rooms', id: 'rooms', icon: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="7" width="20" height="15" rx="2" ry="2" /><polyline points="17 2 12 7 7 2" />
    </svg>
  )},
  { label: 'Upgrades', id: 'upgrades', icon: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 19V5" /><polyline points="5 12 12 5 19 12" />
    </svg>
  )},
];

function NavPills({ onRoomsOpen, onUpgradesOpen, onWatchlistOpen, onCollectionsOpen, onLumaOpen, onTvCalendarOpen, onLibrariesOpen, onLatestOpen, onActivityOpen }: { onRoomsOpen: () => void; onUpgradesOpen: () => void; onWatchlistOpen: () => void; onCollectionsOpen: () => void; onLumaOpen: () => void; onTvCalendarOpen: () => void; onLibrariesOpen: () => void; onLatestOpen: () => void; onActivityOpen: () => void }) {
  const navigate = useNavigate();
  const [pressed, setPressed] = React.useState<string | null>(null);

  return (
    <div style={{ display: 'flex', gap: 10, paddingLeft: 12, paddingRight: 12, overflowX: 'auto', paddingBottom: 8, marginBottom: 20, scrollbarWidth: 'none', msOverflowStyle: 'none' }}>
      {/* Downloads free space — leftmost, opens the free-up-space sheet */}
      <StoragePill />
      {NAV_PILLS.map((pill) => (
        <button
          key={pill.id}
          type="button"
          data-testid={`home-nav-${pill.id}`}
          data-nav="home"
          onClick={() => pill.path ? navigate(pill.path) : pill.id === 'luma-ai' ? onLumaOpen() : pill.id === 'tv-calendar' ? onTvCalendarOpen() : pill.id === 'rooms' ? onRoomsOpen() : pill.id === 'upgrades' ? onUpgradesOpen() : pill.id === 'latest' ? onLatestOpen() : pill.id === 'activity' ? onActivityOpen() : pill.id === 'watchlist' ? onWatchlistOpen() : pill.id === 'collections' ? onCollectionsOpen() : pill.id === 'library' ? onLibrariesOpen() : undefined}
          onTouchStart={() => setPressed(pill.id)}
          onTouchEnd={() => setPressed(null)}
          onTouchCancel={() => setPressed(null)}
          style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '10px 18px', borderRadius: 999,
            background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.1)',
            color: 'rgba(255,255,255,0.85)', fontSize: 14, fontWeight: 500,
            cursor: 'pointer', flexShrink: 0, fontFamily: 'inherit',
            touchAction: 'manipulation',
            transform: pressed === pill.id ? 'scale(0.95)' : 'scale(1)',
            transition: pressed === pill.id ? 'transform 0.1s ease-out' : 'transform 0.25s cubic-bezier(0.34, 1.56, 0.64, 1)',
            outline: 'none',
          }}
        >
          {pill.icon}
          {pill.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Top bar: luma text logo (left) | search icon + user icon (right)
// ---------------------------------------------------------------------------

function TopBar({ onSearchOpen }: { onSearchOpen: () => void }) {
  return (
    <div
      style={{
        position: 'fixed', top: 0, left: 0, right: 0, zIndex: 50,
        paddingTop: 'env(safe-area-inset-top, 0px)',
        background: 'var(--color-kiosk-black)',
      }}
    >
      <div style={{ height: 56, display: 'flex', alignItems: 'center', paddingLeft: 6, paddingRight: 12 }}>
        {/* Luma text logo */}
        <img src={assetUrl('/static/images/luma_text.webp')} alt="Luma" style={{ height: 100, objectFit: 'contain', marginTop: -2 }} />

        {/* Spacer */}
        <div style={{ flex: 1 }} />

        {/* Search — sits next to User (refresh removed; pull-to-refresh on page) */}
        <button
          type="button"
          onClick={onSearchOpen}
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 46, height: 46, marginRight: 10, background: 'none', border: 'none', cursor: 'pointer', padding: 0, flexShrink: 0, color: 'rgba(255,255,255,0.85)', touchAction: 'manipulation' }}
          aria-label="Search"
        >
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="7" />
            <line x1="16.5" y1="16.5" x2="21" y2="21" />
          </svg>
        </button>

        {/* User icon */}
        <UserMenu />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main authenticated home page
// ---------------------------------------------------------------------------

const RAIL_ORDER: HomeRailId[] = [
  'continue_watching',
  'what_youre_missing', // TOP discovery shelf
  'upcoming_tv',
  'watchlist',
  'recent_movies',
  'recent_tv',
  'recently_watched',
];

export function AuthenticatedHomePage() {
  const rails = useHomeRails();
  const modalHost = useModalHost();
  const handleHomeRefresh = useCallback(() => {
    // Visual feedback first; soft refetch isn't on useHomeRails.
    window.setTimeout(() => window.location.reload(), 420);
  }, []);
  const { pullPx, refreshing: ptrRefreshing, progress: ptrProgress } = usePullToRefresh(handleHomeRefresh);
  const [searchOpen, setSearchOpen] = useState(false);
  const [roomsOpen, setRoomsOpen] = useState(false);
  const [upgradesOpen, setUpgradesOpen] = useState(false);
  const [latestOpen, setLatestOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [watchlistOpen, setWatchlistOpen] = useState(false);
  const [collectionsOpen, setCollectionsOpen] = useState(false);
  const [lumaOpen, setLumaOpen] = useState(false);
  const [tvCalendarOpen, setTvCalendarOpen] = useState(false);
  const [librariesOpen, setLibrariesOpen] = useState(false);
  const [wymOverride, setWymOverride] = useState<NormalizedMediaItem[] | null>(null);
  const [wlOverride, setWlOverride] = useState<NormalizedMediaItem[] | null>(null);
  const [dismissTarget, setDismissTarget] = useState<NormalizedMediaItem | null>(null);

  useBodyScroll();

  const railMap = useMemo(() => {
    const map: Partial<Record<HomeRailId, HomeRailState>> = {};
    for (const r of rails) {
      if (r.id === 'what_youre_missing' && wymOverride) {
        map[r.id] = { ...r, items: wymOverride };
      } else if (r.id === 'watchlist' && wlOverride) {
        map[r.id] = { ...r, items: wlOverride };
      } else {
        map[r.id] = r;
      }
    }
    return map;
  }, [rails, wymOverride, wlOverride]);

  const [searchSeed, setSearchSeed] = useState<string | undefined>(undefined);

  // Cast/crew tap inside a modal: open the person modal when we have an id,
  // otherwise open the search sheet pre-filled with the name (was a dead tap).
  const handlePersonSearch = useCallback((name: string, personId?: number) => {
    modalHost.close();
    if (personId) {
      setTimeout(() => modalHost.openPerson({ personId, personName: name }), 320);
    } else {
      setSearchSeed(name);
      setSearchOpen(true);
    }
  }, [modalHost]);

  const handleItemClick = useCallback(
    (item: NormalizedMediaItem, source: ModalSource) => {
      openModalForItem(modalHost, item, source, { onPersonSearch: handlePersonSearch });
    },
    [modalHost, handlePersonSearch],
  );

  const handleMissingLongPress = useCallback((item: NormalizedMediaItem) => {
    if (item.tmdbId == null) return;
    setDismissTarget(item);
  }, []);

  const confirmDismissMissing = useCallback(async () => {
    const item = dismissTarget;
    setDismissTarget(null);
    if (!item?.tmdbId) return;
    const tid = item.tmdbId;
    const base = wymOverride ?? rails.find((r) => r.id === 'what_youre_missing')?.items ?? [];
    setWymOverride(base.filter((it) => it.tmdbId !== tid));
    try {
      const result = await dismissWhatYoureMissing(tid);
      if (result.ok) setWymOverride(result.items);
    } catch {
      // Keep optimistic removal; rail refresh on next visit will reconcile.
    }
  }, [dismissTarget, wymOverride, rails]);


  // Keep Home watchlist rail in sync when modal adds/removes
  useEffect(() => {
    const onChanged = (e: Event) => {
      const detail = (e as CustomEvent<{ tmdbId: number; action: 'added' | 'removed' }>).detail;
      if (!detail?.tmdbId) return;
      const base = wlOverride ?? rails.find((r) => r.id === 'watchlist')?.items ?? [];
      if (detail.action === 'removed') {
        setWlOverride(base.filter((it) => it.tmdbId !== detail.tmdbId));
        return;
      }
      // Added — refetch rail so digital date + poster land correctly
      void apiFetch('/api/watchlist')
        .then((r) => (r.ok ? r.json() : []))
        .then((data: unknown) => {
          const raw = (Array.isArray(data) ? data : []) as Record<string, unknown>[];
          const items = raw.slice(0, 25).map((m) => {
            const tmdbId = (m.tmdb_id as number) ?? null;
            const pp = (m.poster_path as string) || null;
            const path = pp
              ? (pp.startsWith('http') ? pp : `https://image.tmdb.org/t/p/w342${pp.startsWith('/') ? '' : '/'}${pp}`)
              : null;
            return {
              id: `home-wl-${tmdbId ?? m.title}`,
              title: (m.title as string) ?? '',
              year: (m.year as number) ?? null,
              posterUrl: path,
              mediaType: 'movie' as const,
              tmdbId,
              ratingKey: null,
              owned: false,
              inWatchlist: true,
              digitalReleaseDate: (m.digital_release_date as string) || null,
              digitalReleaseLabel: (m.digital_release_label as string) || null,
            };
          });
          setWlOverride(items as NormalizedMediaItem[]);
        })
        .catch(() => {});
    };
    window.addEventListener('watchlist-changed', onChanged);
    return () => window.removeEventListener('watchlist-changed', onChanged);
  }, [rails, wlOverride]);

  const handleRoomsOpen = useCallback(() => setRoomsOpen(true), []);
  const handleUpgradesOpen = useCallback(() => setUpgradesOpen(true), []);
  const handleLatestOpen = useCallback(() => setLatestOpen(true), []);
  const handleActivityOpen = useCallback(() => setActivityOpen(true), []);
  const handleWatchlistOpen = useCallback(() => setWatchlistOpen(true), []);
  const handleCollectionsOpen = useCallback(() => setCollectionsOpen(true), []);
  const handleLumaOpen = useCallback(() => setLumaOpen(true), []);
  const handleTvCalendarOpen = useCallback(() => setTvCalendarOpen(true), []);
  const handleLibrariesOpen = useCallback(() => setLibrariesOpen(true), []);

  // Insert nav pills after continue_watching
  const renderRails = () => {
    const elements: React.ReactNode[] = [];
    let addedPills = false;

    for (const railId of RAIL_ORDER) {
      const rail = railMap[railId];
      if (!rail) continue;

      elements.push(
        <Rail
          key={railId}
          rail={rail}
          railId={railId}
          onItemClick={handleItemClick}
          onItemLongPress={railId === 'what_youre_missing' ? handleMissingLongPress : undefined}
        />
      );

      // Insert nav pills after continue_watching (or after first rail if CW is empty)
      if (!addedPills && (railId === 'continue_watching' || elements.length === 1)) {
        elements.push(<NavPills key="nav-pills" onRoomsOpen={handleRoomsOpen} onUpgradesOpen={handleUpgradesOpen} onWatchlistOpen={handleWatchlistOpen} onCollectionsOpen={handleCollectionsOpen} onLumaOpen={handleLumaOpen} onTvCalendarOpen={handleTvCalendarOpen} onLibrariesOpen={handleLibrariesOpen} onLatestOpen={handleLatestOpen} onActivityOpen={handleActivityOpen} />);
        addedPills = true;
      }
    }

    // If no rails rendered yet, still show pills
    if (!addedPills) {
      elements.unshift(<NavPills key="nav-pills" onRoomsOpen={handleRoomsOpen} onUpgradesOpen={handleUpgradesOpen} onWatchlistOpen={handleWatchlistOpen} onCollectionsOpen={handleCollectionsOpen} onLumaOpen={handleLumaOpen} onTvCalendarOpen={handleTvCalendarOpen} onLibrariesOpen={handleLibrariesOpen} onLatestOpen={handleLatestOpen} onActivityOpen={handleActivityOpen} />);
    }

    return elements;
  };

  return (
    <>
      <div
        style={{
          minHeight: '100dvh',
          background: 'var(--color-kiosk-black)',
          color: 'white',
          position: 'relative',
          transform: pullPx > 0 ? `translateY(${pullPx}px)` : undefined,
          transition: pullPx > 0 || ptrRefreshing ? 'none' : 'transform 180ms ease-out',
        }}
      >
        {/* Pull-to-refresh indicator — Luma purple spinner */}
        {(pullPx > 0 || ptrRefreshing) && (
          <div
            aria-hidden={!ptrRefreshing}
            aria-busy={ptrRefreshing || undefined}
            style={{
              position: 'absolute',
              top: 'calc(56px + env(safe-area-inset-top, 0px) - 8px)',
              left: 0,
              right: 0,
              height: 40,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              zIndex: 2,
              pointerEvents: 'none',
              opacity: ptrRefreshing ? 1 : Math.max(0.25, ptrProgress),
            }}
          >
            <div
              style={{
                width: 26,
                height: 26,
                borderRadius: '50%',
                border: '3px solid hsla(280, 85%, 65%, 0.22)',
                borderTopColor: 'hsla(280, 90%, 68%, 1)',
                boxShadow: '0 0 12px hsla(280, 90%, 55%, 0.35)',
                animation: (ptrRefreshing || ptrProgress >= 1)
                  ? 'luma-ptr-spin 0.75s linear infinite'
                  : undefined,
                transform: ptrRefreshing ? undefined : `rotate(${pullPx * 2.4}deg)`,
              }}
            />
          </div>
        )}
        <div style={{ position: 'relative', zIndex: 1, paddingTop: 'calc(56px + env(safe-area-inset-top, 0px) + 16px)' }}>
          <MobilePushBanner />
          {renderRails()}
          <div style={{ height: 48 }} />
        </div>
      </div>
      <style>{`@keyframes luma-ptr-spin { to { transform: rotate(360deg); } }`}</style>

      {dismissTarget && (
        <DismissMissingSheet
          item={dismissTarget}
          onConfirm={() => { void confirmDismissMissing(); }}
          onClose={() => setDismissTarget(null)}
        />
      )}

      {/* Rooms sheet */}
      <RoomsSheet open={roomsOpen} onClose={() => setRoomsOpen(false)} />

      {/* Upgrades sheet */}
      <UpgradesSheet open={upgradesOpen} onClose={() => setUpgradesOpen(false)} />

      {/* Latest releases sheet */}
      <LatestSheet open={latestOpen} onClose={() => setLatestOpen(false)} />

      {/* Activity (recent downloads) sheet */}
      <ActivitySheet open={activityOpen} onClose={() => setActivityOpen(false)} />

      {/* Watchlist sheet */}
      <WatchlistSheet open={watchlistOpen} onClose={() => setWatchlistOpen(false)} />

      {/* Incomplete franchise collections */}
      <CollectionsSheet open={collectionsOpen} onClose={() => setCollectionsOpen(false)} />

      {/* Luma AI sheet */}
      <LumaAISheet open={lumaOpen} onClose={() => setLumaOpen(false)} />

      {/* Downloads sheet */}
      <TvCalendarSheet open={tvCalendarOpen} onClose={() => setTvCalendarOpen(false)} />

      {/* Libraries sheet */}
      <LibrariesSheet open={librariesOpen} onClose={() => setLibrariesOpen(false)} />


      {/* Search shelf */}
      {searchOpen && (
        <SearchShelf onClose={() => { setSearchOpen(false); setSearchSeed(undefined); }} initialQuery={searchSeed} />
      )}

      {/* TopBar — z:50 */}
      <TopBar onSearchOpen={() => setSearchOpen(true)} />
    </>
  );
}
