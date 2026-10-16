/**
 * NowAvailablePage — full filterable grid of tracker movies.
 *
 * Data: /api/explore/cached?limit=1000
 * Filters: sort (seeders/newest), quality combo, tracker
 * Click card → MediaDetailModal via openModalForItem
 */

import { useState, useEffect, useMemo, useCallback, useRef, memo } from 'react';
import { useNavigate } from 'react-router';
import { useBodyScroll } from '@/hooks/useBodyScroll';
import { useModalHost, type ModalSource } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { enrichOwnership } from '@/services/ownership';
import { fetchWatchlistTmdbIds, enrichWatchlist } from '@/services/watchlistApi';
import { posterUrl, backdropUrl } from '@/utils/image';
import { fetchWithTimeout } from '@/utils/fetchWithTimeout';
import { cn } from '@/utils/cn';
import { useUiDensity } from '@/hooks/useUiDensity';
import { uiDensityTokens } from '@/ui/uiDensityTokens';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface NowAvailableMovie {
  id: string;
  title: string;
  year: number | null;
  posterUrl: string | null;
  backdropUrl: string | null;
  mediaType: 'movie';
  tmdbId: number | null;
  ratingKey: number | null;
  owned: boolean;
  inWatchlist: boolean;
  overview: string | null;
  voteAverage: number | null;
  // Tracker-specific fields
  resolution: string | null;
  source: string | null;
  tracker: string | null;
  seeders: number;
  sizeGb: number | null;
  pubDate: string | null;
  dateAdded: string | null;
  createdAt: string | null;
  torrentName: string | null;
}

type SortKey = 'seeders' | 'newest';

type QualityFilter = 'all' | '4k_remux' | '4k_web' | '1080_remux' | '1080_web';


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getUploadDate(m: NowAvailableMovie): string | null {
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

function getDateTimestamp(m: NowAvailableMovie): number {
  const raw = m.pubDate || m.dateAdded || m.createdAt;
  if (!raw) return 0;
  const t = new Date(raw).getTime();
  return isNaN(t) ? 0 : t;
}

function matchesQuality(m: NowAvailableMovie, q: QualityFilter): boolean {
  if (q === 'all') return true;
  // Match against the raw torrent filename — same approach as legacy
  // _matches_quality_filter. The parsed `source` field is unreliable because
  // regex order can mis-classify "BluRay REMUX" as just "BluRay".
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
// Data fetching
// ---------------------------------------------------------------------------

async function fetchAllTrackerMovies(): Promise<NowAvailableMovie[]> {
  const resp = await fetchWithTimeout('/api/explore/cached?limit=1000');
  if (!resp.ok) return [];
  const data = await resp.json();
  const raw = ((data.movies ?? []) as Record<string, unknown>[]).map((m) => {
    const tmdbId = (m.tmdb_id as number) ?? (typeof m.tmdb_id === 'string' ? parseInt(m.tmdb_id as string, 10) || null : null);
    // Match legacy poster resolution order: poster_url → poster → construct from poster_path
    const rawPosterUrl = (m.poster_url as string) || (m.poster as string) || null;
    const posterPath = (m.poster_path as string) || null;
    const backdropPath = (m.backdrop_path as string) || null;
    return {
      id: `tracker-${tmdbId ?? m.title}`,
      title: (m.title as string) ?? '',
      year: (m.year as number) ?? (typeof m.year === 'string' ? parseInt(m.year as string, 10) || null : null),
      posterUrl: rawPosterUrl || posterUrl(posterPath, 'w342'),
      backdropUrl: backdropUrl(backdropPath),
      mediaType: 'movie' as const,
      tmdbId,
      ratingKey: null,
      owned: false,
      inWatchlist: false,
      overview: (m.overview as string) ?? null,
      voteAverage: null,
      resolution: (m.resolution as string) || null,
      source: (m.source as string) || null,
      tracker: (m.tracker as string) || null,
      seeders: (m.seeders as number) ?? 0,
      sizeGb: (m.size_gb as number) ?? null,
      pubDate: (m.pub_date as string) || (m.publish_date as string) || null,
      dateAdded: (m.date_added as string) || null,
      createdAt: (m.created_at as string) || null,
      torrentName: (m.torrent_name as string) || (m.name as string) || null,
    };
  });

  // Deduplicate: prefer tmdbId (exact), fall back to normalized title
  const deduped = new Map<string, NowAvailableMovie>();
  for (const movie of raw) {
    const key = movie.tmdbId ? `tmdb_${movie.tmdbId}` : movie.title.toLowerCase().replace(/[^\w\s]/g, '').trim();
    const existing = deduped.get(key);
    if (!existing || movie.seeders > existing.seeders) {
      deduped.set(key, movie);
    }
  }
  return Array.from(deduped.values()).filter(m => m.posterUrl !== null);
}

// ---------------------------------------------------------------------------
// NowAvailableCard — inline memo grid card
// ---------------------------------------------------------------------------

function getOrbColor(movie: NowAvailableMovie): { bg: string; glow: string } {
  if (movie.owned) return { bg: 'bg-green-400', glow: '0 0 8px rgba(74,222,128,0.6)' };
  if (movie.inWatchlist) return { bg: 'bg-blue-400', glow: '0 0 8px rgba(96,165,250,0.6)' };
  return { bg: 'bg-red-400', glow: '0 0 8px rgba(248,113,113,0.6)' };
}

const NowAvailableCard = memo(function NowAvailableCard({
  movie,
  onClick,
}: {
  movie: NowAvailableMovie;
  onClick: () => void;
}) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const orb = getOrbColor(movie);

  // Subtitle: year • resolution • source • tracker (skip empty)
  const subtitle = [movie.year, movie.resolution, movie.source, movie.tracker]
    .filter(Boolean)
    .join(' \u2022 ');

  const uploadDate = getUploadDate(movie);

  return (
    <button
      type="button"
      onClick={onClick}
      className="group relative text-left outline-none focus-visible:ring-2 focus-visible:ring-accent/60 rounded-xl"
    >
      <div className="relative aspect-[2/3] overflow-hidden rounded-xl">
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
          <div className="flex h-full w-full items-center justify-center bg-glass-10 text-sm text-text-muted">
            No Poster
          </div>
        )}

        {/* Hover gradient */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />

        {/* Status orb (top-left) */}
        <div className="absolute left-2.5 top-2.5">
          <span
            className={cn('block h-3 w-3 rounded-full', orb.bg)}
            style={{ boxShadow: orb.glow }}
          />
        </div>

      </div>

      {/* Title + metadata below card */}
      <div className="mt-2 px-0.5">
        <p className="truncate text-[14px] font-medium text-text-primary">{movie.title}</p>
        {subtitle && (
          <p className="mt-0.5 truncate text-[12px] text-text-muted">{subtitle}</p>
        )}
        {/* Stats row: size | date | seeders */}
        <div className="mt-1 flex items-center gap-2 text-[12px] text-text-muted">
          {movie.sizeGb != null && movie.sizeGb > 0 && (
            <span>{movie.sizeGb.toFixed(1)} GB</span>
          )}
          {uploadDate && <span>{uploadDate}</span>}
          {movie.seeders > 0 && (
            <span className="text-green-400">&uarr;{movie.seeders}</span>
          )}
        </div>
      </div>
    </button>
  );
});

// ---------------------------------------------------------------------------
// Filter dropdown component (custom popover — matches glassmorphic theme)
// ---------------------------------------------------------------------------

function FilterDropdown({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const selectedLabel = options.find((o) => o.value === value)?.label ?? value;

  // Close on click outside
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
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((p) => !p)}
        className={cn(
          'flex items-center gap-2 rounded-lg px-3 py-1.5 text-[14px] transition-colors',
          'bg-glass-10 hover:bg-glass-20',
          open ? 'ring-2 ring-accent/60' : '',
        )}
      >
        <span className="text-text-muted">{label}</span>
        <span className="text-text-primary font-medium">{selectedLabel}</span>
        <svg
          width="14" height="14" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          className={cn('text-text-muted transition-transform', open && 'rotate-180')}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 min-w-[160px] overflow-hidden rounded-xl border border-white/10 bg-black/90 py-1 shadow-2xl backdrop-blur-xl">
          {options.map((o) => (
            <button
              key={o.value}
              type="button"
              onClick={() => { onChange(o.value); setOpen(false); }}
              className={cn(
                'flex w-full items-center px-4 py-2 text-left text-[14px] transition-colors',
                o.value === value
                  ? 'bg-accent/20 text-accent font-medium'
                  : 'text-text-primary hover:bg-white/8',
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
// Main page component
// ---------------------------------------------------------------------------

export function NowAvailablePage() {
  useBodyScroll();
  const navigate = useNavigate();
  const { openMovie, openTv, close } = useModalHost();
  const d = useUiDensity();
  const tk = uiDensityTokens(d);

  const [movies, setMovies] = useState<NowAvailableMovie[]>([]);
  const [loading, setLoading] = useState(true);

  // Filter state
  const [sort, setSort] = useState<SortKey>('newest');
  const [quality, setQuality] = useState<QualityFilter>('all');
  const [tracker, setTracker] = useState('all');

  // Fetch data on mount
  useEffect(() => {
    let cancelled = false;

    (async () => {
      const [raw, wlIds] = await Promise.all([
        fetchAllTrackerMovies(),
        fetchWatchlistTmdbIds(),
      ]);

      if (cancelled) return;

      // Enrich ownership
      const enriched = await enrichOwnership(raw);
      if (cancelled) return;

      // Enrich watchlist
      const final = enrichWatchlist(enriched, wlIds);

      setMovies(final);
      setLoading(false);
    })();

    return () => { cancelled = true; };
  }, []);

  // Derive tracker names + counts (counts reflect current quality filter)
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
      if (sort === 'seeders') return b.seeders - a.seeders;
      if (sort === 'newest') return getDateTimestamp(b) - getDateTimestamp(a);
      return 0;
    });

    return result;
  }, [movies, quality, tracker, sort]);

  const makePersonSearch = useCallback((name: string, personId?: number) => {
    close();
    const params = new URLSearchParams({ q: name, type: 'people' });
    if (personId) params.set('personId', String(personId));
    navigate(`/?${params.toString()}`);
  }, [close, navigate]);

  const handleCardClick = useCallback((movie: NowAvailableMovie) => {
    openModalForItem(
      { openMovie, openTv },
      movie,
      'now_available_grid' as ModalSource,
      { onPersonSearch: makePersonSearch },
    );
  }, [openMovie, openTv, makePersonSearch]);

  return (
    <div className="bg-kiosk-black" style={{ minHeight: '100dvh' }}>
      <div className="mx-auto max-w-[1600px] px-8 pb-20 pt-20">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-[36px] font-bold text-text-primary">Now Available</h1>
          {!loading && (
            <p className="mt-1 text-[15px] text-text-muted">
              {filteredMovies.length} movie{filteredMovies.length !== 1 ? 's' : ''}
            </p>
          )}
        </div>

        {/* Filter bar */}
        <div className="mb-8 flex flex-wrap items-center gap-4">
          <FilterDropdown
            label="Sort"
            value={sort}
            options={[
              { value: 'seeders', label: 'Seeders' },
              { value: 'newest', label: 'Date Added' },
            ]}
            onChange={(v) => setSort(v as SortKey)}
          />
          <FilterDropdown
            label="Quality"
            value={quality}
            options={QUALITY_OPTIONS}
            onChange={(v) => setQuality(v as QualityFilter)}
          />

          {/* Tracker pills with counts */}
          {trackerInfo.length > 1 && (
            <div className="flex items-center gap-2 ml-2">
              <button
                type="button"
                onClick={() => setTracker('all')}
                className={cn(
                  'rounded-full px-3 py-1 text-[13px] font-medium transition-colors',
                  tracker === 'all'
                    ? 'bg-accent text-white'
                    : 'bg-glass-10 text-text-muted hover:bg-glass-20',
                )}
              >
                All
              </button>
              {trackerInfo.map((t) => (
                <button
                  key={t.name}
                  type="button"
                  onClick={() => setTracker(t.name)}
                  className={cn(
                    'rounded-full px-3 py-1 text-[13px] font-medium transition-colors',
                    tracker === t.name
                      ? 'bg-accent text-white'
                      : 'bg-glass-10 text-text-muted hover:bg-glass-20',
                  )}
                >
                  {t.name} ({t.count})
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Grid */}
        {loading ? (
          <div className={cn('grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))]', tk.spacing.railGap)}>
            {Array.from({ length: 20 }).map((_, i) => (
              <div key={i}>
                <div className="aspect-[2/3] animate-pulse rounded-xl bg-glass-5" />
                <div className="mt-2 h-4 w-3/4 animate-pulse rounded bg-glass-5" />
              </div>
            ))}
          </div>
        ) : filteredMovies.length === 0 ? (
          <div className="flex h-64 items-center justify-center">
            <p className="text-[16px] text-text-muted">No movies match the selected filters</p>
          </div>
        ) : (
          <div className={cn('grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))]', tk.spacing.railGap)}>
            {filteredMovies.map((movie) => (
              <NowAvailableCard
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
