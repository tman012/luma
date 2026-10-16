/**
 * MobileExplorePage — filterable grid of tracker movies for mobile.
 *
 * Uses shared useExploreRails hook for data, with mobile-specific
 * filters and list rendering.
 */

import { useState, useMemo, useCallback, useRef, useEffect, memo } from 'react';
import { useModalHost, type ModalSource } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { MobileHeader } from '@/components/mobile/MobileHeader';
import { cn } from '@/utils/cn';
import { useExploreRails } from '@/features/explore/useExploreRails';
import type { NormalizedMediaItem } from '@/types/media';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SortKey = 'seeders' | 'newest';
type QualityFilter = 'all' | '4k_remux' | '4k_web' | '1080_remux' | '1080_web';

// ---------------------------------------------------------------------------
// Helpers (same logic as NowAvailablePage)
// ---------------------------------------------------------------------------

function getUploadDate(m: NormalizedMediaItem): string | null {
  const raw = m.pubDate || m.dateAdded || m.createdAt;
  if (!raw) return null;
  const parsed = new Date(raw);
  if (isNaN(parsed.getTime())) return null;
  const days = Math.floor((Date.now() - parsed.getTime()) / 86_400_000);
  if (days <= 0) return 'Today';
  if (days === 1) return '1 day ago';
  if (days < 7) return `${days} days ago`;
  if (days < 30) return `${Math.floor(days / 7)} weeks ago`;
  if (days < 365) return `${Math.floor(days / 30)} months ago`;
  return parsed.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function getDateTimestamp(m: NormalizedMediaItem): number {
  const raw = m.pubDate || m.dateAdded || m.createdAt;
  if (!raw) return 0;
  const t = new Date(raw).getTime();
  return isNaN(t) ? 0 : t;
}

function matchesQuality(m: NormalizedMediaItem, q: QualityFilter): boolean {
  if (q === 'all') return true;
  const tn = (m.torrentName ?? '').toUpperCase();
  const res = (m.resolution ?? '').toLowerCase();
  switch (q) {
    case '4k_remux':
      return (res.includes('2160') || tn.includes('4K')) && tn.includes('REMUX');
    case '4k_web':
      return (res.includes('2160') || tn.includes('4K')) && (tn.includes('WEB-DL') || tn.includes('WEBDL'));
    case '1080_remux':
      return res.includes('1080') && tn.includes('REMUX');
    case '1080_web':
      return res.includes('1080') && (tn.includes('WEB-DL') || tn.includes('WEBDL'));
    default: return true;
  }
}

const QUALITY_OPTIONS: { value: QualityFilter; label: string }[] = [
  { value: 'all', label: 'All Quality' },
  { value: '4k_remux', label: '4K Remux' },
  { value: '4k_web', label: '4K WEB-DL' },
  { value: '1080_remux', label: '1080p Remux' },
  { value: '1080_web', label: '1080p WEB-DL' },
];

// ---------------------------------------------------------------------------
// MobileExploreCard
// ---------------------------------------------------------------------------

function getOrbColor(movie: NormalizedMediaItem): { bg: string; glow: string } | null {
  if (movie.inWatchlist) return { bg: 'bg-blue-400', glow: '0 0 8px rgba(96,165,250,0.6)' };
  return null;
}

const MobileExploreCard = memo(function MobileExploreCard({
  movie,
  onClick,
}: {
  movie: NormalizedMediaItem;
  onClick: () => void;
}) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const orb = getOrbColor(movie);
  const uploadDate = getUploadDate(movie);

  return (
    <button
      type="button"
      onClick={onClick}
      className="flex gap-3 w-full text-left outline-none active:bg-white/5 rounded-lg p-2 -mx-2 transition-colors"
    >
      {/* Small poster */}
      <div className="relative w-[72px] shrink-0 aspect-[2/3] overflow-hidden rounded-md">
        {movie.posterUrl ? (
          <img
            src={movie.posterUrl}
            alt={movie.title}
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
          <div className="flex h-full w-full items-center justify-center bg-[#333] text-[10px] text-[#666]">
            No Poster
          </div>
        )}
      </div>

      {/* Details to the right */}
      <div className="flex-1 min-w-0 py-0.5">
        <div className="flex items-center gap-2">
          {orb && (
            <span
              className={cn('block h-2 w-2 rounded-full shrink-0', orb.bg)}
              style={{ boxShadow: orb.glow }}
            />
          )}
          <p className="truncate text-[14px] font-medium text-white">{movie.title}</p>
        </div>

        {/* Year + resolution + source */}
        <p className="mt-1 truncate text-[12px] text-[#999]">
          {[movie.year, movie.resolution, movie.source].filter(Boolean).join(' \u2022 ')}
        </p>

        {/* Tracker */}
        {movie.tracker && (
          <p className="mt-0.5 text-[11px] text-[#666]">{movie.tracker}</p>
        )}

        {/* Stats row: size | date | seeders */}
        <div className="mt-1.5 flex items-center gap-2 text-[11px] text-[#777]">
          {movie.sizeGb != null && movie.sizeGb > 0 && (
            <span>{movie.sizeGb.toFixed(1)} GB</span>
          )}
          {uploadDate && <span>{uploadDate}</span>}
          {(movie.seeders ?? 0) > 0 && (
            <span className="text-green-400">&uarr;{movie.seeders}</span>
          )}
        </div>
      </div>
    </button>
  );
});

// ---------------------------------------------------------------------------
// MobileFilterDropdown
// ---------------------------------------------------------------------------

function MobileFilterDropdown({
  value,
  options,
  onChange,
}: {
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const selectedLabel = options.find((o) => o.value === value)?.label ?? value;

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('pointerdown', handleClick);
    return () => document.removeEventListener('pointerdown', handleClick);
  }, [open]);

  return (
    <div ref={containerRef} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((p) => !p)}
        className={cn(
          'flex items-center gap-2 rounded-full px-5 h-12 text-[15px] font-medium transition-colors',
          'bg-white/8 border border-white/10 shadow-sm shadow-black/30',
          open ? 'ring-1 ring-white/20 bg-white/14' : 'active:bg-white/14',
        )}
      >
        <span className="text-white">{selectedLabel}</span>
        <svg
          width="14" height="14" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          className={cn('text-[#999] transition-transform', open && 'rotate-180')}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-1.5 min-w-[160px] rounded-2xl border border-white/10 bg-zinc-900/70 p-2 shadow-xl shadow-black/50 backdrop-blur-xl">
          {options.map((o) => (
            <button
              key={o.value}
              type="button"
              onClick={() => { onChange(o.value); setOpen(false); }}
              className={cn(
                'flex w-full items-center h-11 px-4 rounded-lg text-left text-[15px] transition-colors',
                o.value === value
                  ? 'bg-white/12 text-white font-medium ring-1 ring-white/10'
                  : 'text-[#ccc] active:bg-white/10',
              )}
            >
              {o.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// MobilePosterRail — horizontal poster strip (Anticipated rails)
// ---------------------------------------------------------------------------

const MobilePosterRail = memo(function MobilePosterRail({
  title,
  items,
  onCardClick,
}: {
  title: string;
  items: NormalizedMediaItem[] | undefined;
  onCardClick: (item: NormalizedMediaItem) => void;
}) {
  if (!items || items.length === 0) return null;
  return (
    <section className="mb-5">
      <h2 className="mb-2 px-1 text-[16px] font-semibold text-white">{title}</h2>
      <div
        className="flex gap-3 overflow-x-auto pb-1"
        style={{ scrollbarWidth: 'none', WebkitOverflowScrolling: 'touch' }}
      >
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => onCardClick(item)}
            className="w-[120px] shrink-0 text-left"
          >
            <div className="aspect-[2/3] w-full overflow-hidden rounded-lg bg-white/5">
              {item.posterUrl ? (
                <img
                  src={item.posterUrl}
                  alt={item.title}
                  className="h-full w-full object-cover"
                  loading="lazy"
                />
              ) : (
                <div className="flex h-full items-center justify-center px-2 text-center text-[11px] text-white/40">
                  {item.title}
                </div>
              )}
            </div>
            <p className="mt-1 truncate text-[12px] text-white/80">{item.title}</p>
          </button>
        ))}
      </div>
    </section>
  );
});

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export function MobileExplorePage() {
  const modalHost = useModalHost();

  const rails = useExploreRails({
    railIds: ['trakt_anticipated_movies', 'trakt_anticipated_shows', 'now_available'],
    paramOverrides: { now_available: { limit: 1000 } },
    enableOwnership: false,
  });

  const nowAvailable = rails.find((r) => r.id === 'now_available');
  const anticipatedMovies = rails.find((r) => r.id === 'trakt_anticipated_movies');
  const anticipatedShows = rails.find((r) => r.id === 'trakt_anticipated_shows');
  const movies = nowAvailable?.items ?? [];
  const loading = nowAvailable?.isLoading ?? true;

  // Filter state
  const [sort, setSort] = useState<SortKey>('newest');
  const [quality, setQuality] = useState<QualityFilter>('all');
  const [tracker, setTracker] = useState('all');

  // Tracker names + counts (reflect current quality filter)
  const trackerInfo = useMemo(() => {
    const counts = new Map<string, number>();
    for (const m of movies) {
      if (m.tracker && matchesQuality(m, quality)) {
        counts.set(m.tracker, (counts.get(m.tracker) ?? 0) + 1);
      }
    }
    return Array.from(counts.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([name, count]) => ({ name, count }));
  }, [movies, quality]);

  // Filtered + sorted movies
  const filteredMovies = useMemo(() => {
    let result = movies;

    if (quality !== 'all') {
      result = result.filter((m) => matchesQuality(m, quality));
    }
    if (tracker !== 'all') {
      result = result.filter((m) => m.tracker === tracker);
    }

    result = [...result].sort((a, b) => {
      if (sort === 'seeders') return (b.seeders ?? 0) - (a.seeders ?? 0);
      if (sort === 'newest') return getDateTimestamp(b) - getDateTimestamp(a);
      return 0;
    });

    return result;
  }, [movies, quality, tracker, sort]);

  // Track actual rendered header height so content is never clipped regardless
  // of safe-area-inset-top size. Default 120px covers most devices before measurement.
  const [headerHeight, setHeaderHeight] = useState(120);

  const onPersonSearch = useCallback((name: string, personId?: number) => {
    modalHost.close();
    window.dispatchEvent(new CustomEvent('mobilePersonSearch', { detail: { name, personId } }));
  }, [modalHost]);

  const handleCardClick = useCallback((movie: NormalizedMediaItem) => {
    openModalForItem(
      modalHost,
      movie,
      'mobile_explore_grid' as ModalSource,
      { onPersonSearch },
    );
  }, [modalHost, onPersonSearch]);

  return (
    <div
      data-scroll-container
      className="luma-scroll-y mobile-page-enter fixed inset-0 bg-kiosk-black text-white"
      style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y', animation: 'mobilePageEnter 0.25s ease-out' }}
    >
      {/* MobileHeader measures its own offsetHeight via ResizeObserver and reports it up */}
      <MobileHeader onHeightChange={setHeaderHeight} />

      {/* paddingTop = measured header height + 20px buffer — guaranteed to clear the fixed header */}
      <div className="pb-8 px-4" style={{ paddingTop: headerHeight + 20 }}>
        {/* Anticipated rails (Trakt — hidden when disabled/empty) */}
        <MobilePosterRail title="Anticipated Movies" items={anticipatedMovies?.items} onCardClick={handleCardClick} />
        <MobilePosterRail title="Anticipated Shows" items={anticipatedShows?.items} onCardClick={handleCardClick} />

        {/* Filter bar */}
        <div className="relative z-[60] mb-4 flex flex-col gap-2">
          {/* Sort + Quality dropdowns */}
          <div className="flex items-center justify-center gap-2">
            <MobileFilterDropdown
              value={sort}
              options={[
                { value: 'seeders', label: 'Seeders' },
                { value: 'newest', label: 'Date Added' },
              ]}
              onChange={(v) => setSort(v as SortKey)}
            />
            <MobileFilterDropdown
              value={quality}
              options={QUALITY_OPTIONS}
              onChange={(v) => setQuality(v as QualityFilter)}
            />
          </div>

          {/* Tracker pills row */}
          {trackerInfo.length > 1 && (
            <div className="flex flex-wrap items-center justify-center gap-2">
              {trackerInfo.map((t) => (
                <button
                  key={t.name}
                  type="button"
                  onClick={() => setTracker(tracker === t.name ? 'all' : t.name)}
                  className={cn(
                    'shrink-0 rounded-full px-4 h-11 text-[14px] font-medium transition-colors whitespace-nowrap',
                    tracker === t.name
                      ? 'bg-white text-black shadow-sm shadow-black/30'
                      : 'bg-white/8 border border-white/10 text-[#999] active:bg-white/14',
                  )}
                >
                  {t.name} ({t.count})
                </button>
              ))}
            </div>
          )}

          {/* Movie count */}
          {!loading && (
            <p className="text-center text-[13px] text-[#888]">
              {filteredMovies.length} movie{filteredMovies.length !== 1 ? 's' : ''}
            </p>
          )}
        </div>

        {/* List */}
        {loading ? (
          <div className="flex flex-col gap-1">
            {Array.from({ length: 10 }).map((_, i) => (
              <div key={i} className="flex gap-3 p-2">
                <div className="w-[72px] shrink-0 aspect-[2/3] animate-pulse rounded-md bg-[#333]" />
                <div className="flex-1 py-1">
                  <div className="h-4 w-3/4 animate-pulse rounded bg-[#333]" />
                  <div className="mt-2 h-3 w-1/2 animate-pulse rounded bg-[#333]" />
                  <div className="mt-2 h-3 w-1/3 animate-pulse rounded bg-[#333]" />
                </div>
              </div>
            ))}
          </div>
        ) : filteredMovies.length === 0 ? (
          <div className="flex h-48 items-center justify-center">
            <p className="text-[14px] text-[#888]">No movies match the selected filters</p>
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            {filteredMovies.map((movie) => (
              <MobileExploreCard
                key={movie.id}
                movie={movie}
                onClick={() => handleCardClick(movie)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
