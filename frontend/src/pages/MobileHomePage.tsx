import { useCallback, useEffect, useMemo, useRef, useState, memo } from 'react';
import { useModalHost, type ModalSource } from '@/contexts/ModalHostContext';
import { useHomeRails, type HomeRailState } from '@/features/home/useHomeRails';
import type { HomeRailId } from '@/features/home/homeRails';
import type { NormalizedMediaItem } from '@/types/media';
import { openModalForItem } from '@/utils/modalNavigation';
import { itemIds, openItemWhenReady, warmIds, warmKey } from '@/services/modalOpen';
import { MobileHeader } from '@/components/mobile/MobileHeader';
import { MobilePushBanner } from '@/components/mobile/MobilePushBanner';
import { MobileRail } from '@/components/mobile/MobileRail';
import { MobilePosterCard } from '@/components/mobile/MobilePosterCard';
import { DismissMissingSheet } from '@/features/home/DismissMissingSheet';
import { dismissWhatYoureMissing } from '@/features/home/wymDismissApi';
import { apiFetch } from '../apiClient';

// ---------------------------------------------------------------------------
// Modal source mapping per rail
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

// Rails whose items are owned (don't show watchlist orb)
const OWNED_RAILS = new Set<HomeRailId>(['continue_watching', 'recent_movies', 'recent_tv', 'recently_watched']);

// Skeleton placeholder for a rail that hasn't loaded yet
const SkeletonRail = memo(function SkeletonRail() {
  return (
    <div className="mb-6">
      <div className="skeleton-shimmer h-5 w-32 rounded bg-white/5 mb-2" style={{ marginLeft: 12 }} />
      <div
        className="flex items-start gap-3 overflow-hidden pb-2"
        style={{ paddingLeft: 12 }}
      >
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="w-[115px] shrink-0">
            <div className="skeleton-shimmer aspect-[2/3] rounded-lg bg-white/5" />
            <div className="skeleton-shimmer mt-1.5 h-3 w-3/4 rounded bg-white/5" />
          </div>
        ))}
      </div>
    </div>
  );
});

export function MobileHomePage() {
  const rails = useHomeRails();
  const modalHost = useModalHost();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [wymOverride, setWymOverride] = useState<NormalizedMediaItem[] | null>(null);
  const [wlOverride, setWlOverride] = useState<NormalizedMediaItem[] | null>(null);
  const [dismissTarget, setDismissTarget] = useState<NormalizedMediaItem | null>(null);

  // Build a lookup map for per-rail rendering
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

  const onPersonSearch = useCallback((name: string, personId?: number) => {
    modalHost.close();
    window.dispatchEvent(new CustomEvent('mobilePersonSearch', { detail: { name, personId } }));
  }, [modalHost]);

  const handleTap = useCallback(
    (item: NormalizedMediaItem, source: ModalSource, el?: HTMLElement | null) => {
      // The popup opens only once it can show fully composed (the tapped
      // poster shows a pressed state meanwhile, 600 ms cap).
      openItemWhenReady(item, el ?? null, () => { openModalForItem(modalHost, item, source, { onPersonSearch }); });
    },
    [modalHost, onPersonSearch],
  );

  const handleMissingLongPress = useCallback((item: NormalizedMediaItem) => {
    if (item.tmdbId == null) return;
    setDismissTarget(item);
  }, []);


  useEffect(() => {
    const onChanged = (e: Event) => {
      const detail = (e as CustomEvent<{ tmdbId: number; action: 'added' | 'removed' }>).detail;
      if (!detail?.tmdbId) return;
      const base = wlOverride ?? rails.find((r) => r.id === 'watchlist')?.items ?? [];
      if (detail.action === 'removed') {
        setWlOverride(base.filter((it) => it.tmdbId !== detail.tmdbId));
        return;
      }
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
      // Keep optimistic removal.
    }
  }, [dismissTarget, wymOverride, rails]);

  // Compute stagger indices for visible (non-empty, loaded) rails only
  const railIndices = useMemo(() => {
    let idx = 0;
    const indices: Partial<Record<HomeRailId, number>> = {};
    for (const r of rails) {
      if (!r.isLoading && r.items.length > 0) {
        indices[r.id] = idx++;
      }
    }
    return indices;
  }, [rails]);

  // Render helper for a single rail
  const renderRail = (id: HomeRailId) => {
    const rail = railMap[id];
    const isOwned = OWNED_RAILS.has(id);

    // Conditional rail: absent if no data
    if (!rail) return null;

    // Still loading: show skeleton for non-conditional rails
    if (rail.isLoading) return <SkeletonRail key={id} />;

    // Loaded but empty
    if (rail.items.length === 0) return null;

    return (
      <MobileRail key={id} title={rail.title} index={railIndices[id] ?? 0}>
        {rail.items.map((item) => (
          <MobilePosterCard
            key={item.id}
            posterUrl={item.posterUrl}
            title={item.title}
            subtitle={item.subtitle ?? (item.year ? `(${item.year})` : undefined)}
            progressPercent={item.progressPercent}
            voteAverage={item.voteAverage}
            owned={item.owned}
            inWatchlist={item.inWatchlist}
            showOrb={!isOwned}
            digitalReleaseLabel={id === 'watchlist' ? item.digitalReleaseLabel : undefined}
            onTap={(el) => handleTap(item, RAIL_MODAL_SOURCE[id], el)}
            warmKey={warmKey(itemIds(item))}
            warm={() => warmIds(itemIds(item))}
            onLongPress={id === 'what_youre_missing' ? () => handleMissingLongPress(item) : undefined}
          />
        ))}
      </MobileRail>
    );
  };

  return (
    <div
      ref={scrollRef}
      data-scroll-container
      className="luma-scroll-y mobile-page-enter fixed inset-0 bg-kiosk-black text-white"
      style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y', animation: 'mobilePageEnter 0.25s ease-out' }}
    >
      <MobileHeader scrollRef={scrollRef} />

      {/* Spacer for fixed header + content area */}
      <div className="pt-20 pb-8">

      <MobilePushBanner />

      {renderRail('continue_watching')}
      {renderRail('what_youre_missing')}
      {renderRail('upcoming_tv')}
      {renderRail('watchlist')}
      {renderRail('recent_movies')}
      {renderRail('recent_tv')}
      {renderRail('recently_watched')}
      </div>

      {dismissTarget && (
        <DismissMissingSheet
          item={dismissTarget}
          onConfirm={() => { void confirmDismissMissing(); }}
          onClose={() => setDismissTarget(null)}
        />
      )}

    </div>
  );
}
