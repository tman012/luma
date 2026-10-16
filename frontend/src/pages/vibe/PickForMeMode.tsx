/**
 * Pick For Me — guided walkthrough + spin roulette.
 *
 * 4-step conversational flow narrows preferences, then spins for 5 curated picks.
 * Supports "Discover" mode that draws from Trakt for unowned items.
 */

import { useState, useEffect, useCallback, useRef, type SyntheticEvent } from 'react';
import { pickRandom, pickRandomUnowned, tmdbToVibeItem } from '@/services/vibeEngine';
import { enrichOwnership } from '@/services/ownership';
import { cn } from '@/utils/cn';
import { rtCriticIcon, rtAudienceIcon } from '@/utils/ratingIcons';
import type { VibeItem } from '@/types/vibe';
import type { VibeSettings } from '@/services/vibeApi';

const PICK_COUNT = 5;
const STEP_COUNT = 4;

const DEFAULT_GENRES = [
  'Action', 'Adventure', 'Animation', 'Comedy', 'Crime',
  'Documentary', 'Drama', 'Family', 'Fantasy', 'History',
  'Horror', 'Music', 'Mystery', 'Romance', 'Sci-Fi',
  'Thriller', 'War', 'Western',
];

interface Props {
  library: VibeItem[];
  onOpenModal: (item: VibeItem) => void;
  vibeSettings?: VibeSettings | null;
  /** Lazy library load (started when user selects "In Your Library"); resolves with the items. */
  loadLibrary?: () => Promise<VibeItem[]>;
}

// --- Internal filter types (built from walkthrough answers) ---

type TypeFilter = 'both' | 'movie' | 'tv';
type EraFilter = 'any' | 'newer' | 'older';
type RatingFilter = 'any' | '70' | '80';

interface Filters {
  type: TypeFilter;
  unwatchedBoost: boolean;
  tonightMode: boolean;
  era: EraFilter;
  genres: string[];
  unowned: boolean;
  rating: RatingFilter;
}

// --- Walkthrough answers ---

type Phase = 'walkthrough' | 'spinning' | 'results';

interface Answers {
  type: TypeFilter;
  source: 'library' | 'discover';
  genres: string[];
  era: EraFilter;
  quickWatch: boolean;
  unwatchedFirst: boolean;
}

const INITIAL_ANSWERS: Answers = {
  type: 'both',
  source: 'library',
  genres: [],
  era: 'any',
  quickWatch: false,
  unwatchedFirst: true,
};

// ---------------------------------------------------------------------------
// Utility functions (unchanged from original)
// ---------------------------------------------------------------------------

function discoverFilterKey(filters: Filters): string {
  return [
    filters.type,
    filters.era,
    ...filters.genres.slice().sort(),
  ].join('|');
}

/** TMDb genre name → ID mapping for the discover API */
const TMDB_GENRE_IDS: Record<string, number> = {
  Action: 28, Adventure: 12, Animation: 16, Comedy: 35, Crime: 80,
  Documentary: 99, Drama: 18, Family: 10751, Fantasy: 14, History: 36,
  Horror: 27, Music: 10402, Mystery: 9648, Romance: 10749,
  'Sci-Fi': 878, Thriller: 53, War: 10752, Western: 37,
};
const TMDB_TV_GENRE_IDS: Record<string, number> = {
  Action: 10759, Adventure: 10759, Animation: 16, Comedy: 35, Crime: 80,
  Documentary: 99, Drama: 18, Family: 10751, Fantasy: 10765, History: 36,
  Horror: 27, Music: 10402, Mystery: 9648, Romance: 10749,
  'Sci-Fi': 10765, Thriller: 53, War: 10768, Western: 37,
};

/** Fetch a large pool of unowned items from TMDb discover (10 pages = ~200 items). */
async function fetchDiscoverPool(
  filters: Filters,
  newerThanYears: number,
): Promise<VibeItem[]> {
  const params = new URLSearchParams();
  params.set('type', filters.type === 'both' ? 'both' : filters.type);
  params.set('pages', '50');

  // Map genre names to TMDb IDs
  if (filters.genres.length > 0) {
    const isTV = filters.type === 'tv';
    const genreMap = isTV ? TMDB_TV_GENRE_IDS : TMDB_GENRE_IDS;
    const ids = filters.genres.map((g) => genreMap[g]).filter((id): id is number => !!id);
    if (ids.length > 0) params.set('with_genres', ids.join(','));
  }

  // Era filter via release date
  const now = new Date().getFullYear();
  if (filters.era === 'newer') {
    params.set('primary_release_date.gte', `${now - newerThanYears}-01-01`);
    params.set('first_air_date.gte', `${now - newerThanYears}-01-01`);
  } else if (filters.era === 'older') {
    params.set('primary_release_date.lte', `${now - newerThanYears - 1}-12-31`);
    params.set('first_air_date.lte', `${now - newerThanYears - 1}-12-31`);
  }

  const resp = await fetch(`/api/tmdb/discover?${params.toString()}`);
  if (!resp.ok) return [];
  const data = await resp.json();
  const rawResults = (data.results ?? []) as Record<string, unknown>[];

  // Convert to VibeItems
  const items = rawResults.map((raw, i) => {
    const mtype = (raw.media_type as 'movie' | 'tv') ?? 'movie';
    return tmdbToVibeItem(raw, mtype, i);
  });

  // Deduplicate by tmdbId
  const seen = new Set<number>();
  const deduped = items.filter((i) => {
    if (!i.tmdbId) return true;
    if (seen.has(i.tmdbId)) return false;
    seen.add(i.tmdbId);
    return true;
  });

  // Check ownership and filter out items the user already has
  const keyMap = new Map<string, number>();
  const withOwnership = deduped.map((i) => {
    const key = `${i.title.toLowerCase()}_${i.year ?? ''}`;
    keyMap.set(key, i.ratingKey);
    return { ...i, owned: false, ratingKey: null as number | null };
  });
  const enriched = await enrichOwnership(withOwnership);
  return enriched
    .filter((i) => !i.owned)
    .map((i) => {
      const key = `${i.title.toLowerCase()}_${i.year ?? ''}`;
      return { ...i, ratingKey: keyMap.get(key) ?? -1 } as VibeItem;
    });
}

async function fetchRtScores(
  item: VibeItem,
): Promise<{ critic: number | null; audience: number | null }> {
  const titleEnc = encodeURIComponent(item.title);
  const yearParam = item.year ? `year=${item.year}` : '';

  const [omdbResp, rtResp] = await Promise.all([
    fetch(`/api/ratings/${titleEnc}${yearParam ? `?${yearParam}` : ''}`).catch(() => null),
    fetch(`/api/rt/reviews?title=${titleEnc}${yearParam ? `&${yearParam}` : ''}&limit=1`).catch(() => null),
  ]);

  let critic: number | null = null;
  let audience: number | null = null;

  if (omdbResp?.ok) {
    try {
      const data = await omdbResp.json();
      critic = data.rotten_tomatoes_critic?.score != null ? Number(data.rotten_tomatoes_critic.score) : null;
    } catch { /* ignore */ }
  }

  if (rtResp?.ok) {
    try {
      const data = await rtResp.json();
      if (data.critic_score != null) critic = Number(data.critic_score);
      if (data.audience_score != null) audience = Number(data.audience_score);
    } catch { /* ignore */ }
  }

  return { critic, audience };
}

function orderWithCenterPremium(items: VibeItem[]): VibeItem[] {
  if (items.length <= 1) return items;

  const bestRating = (i: VibeItem) => i.audienceRating ?? i.rating ?? 0;
  let bestIdx = 0;
  for (let i = 1; i < items.length; i++) {
    if (bestRating(items[i]!) > bestRating(items[bestIdx]!)) bestIdx = i;
  }

  const centerIdx = Math.floor(items.length / 2);
  if (bestIdx === centerIdx) return items;

  const result = [...items];
  [result[centerIdx], result[bestIdx]] = [result[bestIdx]!, result[centerIdx]!];
  return result;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

function useIsMobile() {
  const [mobile, setMobile] = useState(() => window.innerWidth < 640);
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 639px)');
    const handler = (e: MediaQueryListEvent) => setMobile(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);
  return mobile;
}

// ---------------------------------------------------------------------------
// Results sub-components (extracted for readability)
// ---------------------------------------------------------------------------

interface ResultsProps {
  picks: VibeItem[];
  noResults: boolean;
  loadError?: boolean;
  onOpenModal: (item: VibeItem) => void;
  onStartOver: () => void;
  onSpinAgain: () => void;
}

/** Mobile results: 3+2 poster grid (all visible, no scrolling) + bottom-pinned buttons */
function MobileResults({ picks, noResults, loadError, onOpenModal, onStartOver, onSpinAgain }: ResultsProps) {
  // Split into top row (3) and bottom row (2)
  const topRow = picks.slice(0, 3);
  const bottomRow = picks.slice(3, 5);

  const renderCard = (item: VibeItem, idx: number, isCenter: boolean) => (
    <button
      key={item.tmdbId ?? item.ratingKey}
      type="button"
      onClick={() => onOpenModal(item)}
      className={cn(
        'group relative w-full text-left outline-none',
        'transition-transform duration-300 active:scale-[0.95]',
      )}
      style={{
        animation: `mobileCardReveal 0.5s ${idx * 0.08}s ease-out both`,
      }}
    >
      {/* Poster */}
      <div className={cn(
        'relative w-full overflow-hidden rounded-xl shadow-lg',
        isCenter && 'ring-2 ring-amber-400/40',
      )}>
        <div className="aspect-[2/3] bg-white/5">
          {item.posterUrl ? (
            <img
              src={item.posterUrl}
              alt={item.title}
              className="h-full w-full object-cover opacity-0 transition-opacity duration-300"
              onLoad={(e: SyntheticEvent<HTMLImageElement>) => {
                e.currentTarget.classList.remove('opacity-0');
              }}
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-xs text-white/30">
              No Poster
            </div>
          )}
        </div>

        {/* Watched badge */}
        {item.viewCount > 0 && (
          <div className="absolute right-1.5 top-1.5 rounded-full bg-black/60 px-1.5 py-0.5 text-[9px] font-semibold text-green-400 backdrop-blur-sm">
            Watched
          </div>
        )}

        {/* RT critic score */}
        {item.rtCriticScore != null && (
          <div className="absolute bottom-1.5 left-1.5 flex items-center gap-0.5 rounded-full bg-black/70 px-1.5 py-0.5 backdrop-blur-sm">
            <img src={rtCriticIcon(item.rtCriticScore)} alt="RT" className="h-3 w-auto" />
            <span className="text-[9px] font-bold text-white">{item.rtCriticScore}%</span>
          </div>
        )}
      </div>

      {/* Title + meta */}
      <div className="mt-1.5 text-center">
        <p className="truncate text-[12px] font-semibold leading-tight text-white">
          {item.title}
        </p>
        <p className="text-[10px] text-white/40">
          {item.year ?? ''}{item.runtimeMinutes > 0 ? ` \u{00B7} ${item.runtimeMinutes}m` : ''}
        </p>
      </div>
    </button>
  );

  return (
    <div className="flex h-full w-full flex-col">
      {/* Poster grid — vertically centered, all visible */}
      <div className="flex flex-1 flex-col items-center justify-center px-3">
        {picks.length > 0 && (
          <div className="grid w-full grid-cols-3 gap-3 px-1">
            {/* Top row: 3 cards */}
            {topRow.map((item, idx) => (
              <div key={item.tmdbId ?? item.ratingKey} className="overflow-hidden">
                {renderCard(item, idx, idx === 1)}
              </div>
            ))}
            {/* Bottom row: 2 cards, centered via offset grid */}
            <div className="col-span-3 grid grid-cols-2 gap-3 px-[16.67%]">
              {bottomRow.map((item, idx) => (
                <div key={item.tmdbId ?? item.ratingKey} className="overflow-hidden">
                  {renderCard(item, idx + 3, false)}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Library failed to load */}
        {loadError && (
          <div className="text-center">
            <p className="text-[20px] font-medium text-white/60">Couldn{'\u2019'}t load your library</p>
          </div>
        )}

        {/* No results */}
        {noResults && !loadError && (
          <div className="text-center">
            <p className="text-[20px] font-medium text-white/50">No matches found</p>
            <p className="mt-1 text-[14px] text-white/30">Try different preferences</p>
          </div>
        )}
      </div>

      {/* Bottom-pinned action buttons */}
      <div className="shrink-0 px-5 pb-8 pt-4">
        <div className="flex gap-3">
          <button
            type="button"
            onClick={onStartOver}
            className={cn(
              'flex-1 rounded-2xl bg-white/10 py-4 text-[17px] font-semibold',
              'text-white/70 backdrop-blur-sm transition-all',
              'active:scale-[0.97]',
            )}
          >
            Start Over
          </button>
          <button
            type="button"
            onClick={onSpinAgain}
            className={cn(
              'flex-1 rounded-2xl py-4 text-[17px] font-bold text-white',
              'bg-gradient-to-r from-amber-500 to-red-500',
              'shadow-[0_0_30px_rgba(245,158,11,0.3)]',
              'transition-all active:scale-[0.97]',
            )}
          >
            Spin Again
          </button>
        </div>
      </div>
    </div>
  );
}

/** Desktop results: 5 cards fanned across center + centered action buttons (unchanged) */
function DesktopResults({ picks, noResults, loadError, onOpenModal, onStartOver, onSpinAgain }: ResultsProps) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-4">
      {/* Card reveal — 5 cards on desktop */}
      {picks.length > 0 && (
        <div
          className="mb-10 flex w-full items-end justify-center gap-[clamp(10px,1.2vw,24px)] pb-2"
        >
          {picks.map((item, idx) => {
            const centerIdx = Math.floor(picks.length / 2);
            const isCenter = idx === centerIdx;
            const distFromCenter = Math.abs(idx - centerIdx);
            const delay = `${idx * 0.12}s`;

            const cardWidth = isCenter
              ? Math.min(360, window.innerWidth * 0.28)
              : distFromCenter === 1
                ? Math.min(280, window.innerWidth * 0.22)
                : Math.min(220, window.innerWidth * 0.17);

            return (
              <button
                key={item.tmdbId ?? item.ratingKey}
                type="button"
                onClick={() => onOpenModal(item)}
                className={cn(
                  'group relative shrink-0 text-left outline-none',
                  'transition-transform duration-300 hover:scale-[1.06] active:scale-[0.97]',
                )}
                style={{
                  width: cardWidth,
                  animation: `cardReveal 0.6s ${delay} cubic-bezier(0.34, 1.56, 0.64, 1) both`,
                  perspective: '800px',
                }}
              >
                <div>
                  {/* Poster */}
                  <div className="relative overflow-hidden rounded-2xl shadow-2xl">
                    <div className="aspect-[2/3] bg-white/5">
                      {item.posterUrl ? (
                        <img
                          src={item.posterUrl}
                          alt={item.title}
                          className="h-full w-full object-cover opacity-0 transition-opacity duration-300"
                          onLoad={(e: SyntheticEvent<HTMLImageElement>) => {
                            e.currentTarget.classList.remove('opacity-0');
                          }}
                        />
                      ) : (
                        <div className="flex h-full w-full items-center justify-center text-base text-white/30">
                          No Poster
                        </div>
                      )}
                    </div>

                    {/* Watched badge */}
                    {item.viewCount > 0 && (
                      <div className="absolute right-3 top-3 rounded-full bg-black/60 px-3 py-1 text-[clamp(12px,1vw,14px)] font-semibold text-green-400 backdrop-blur-sm">
                        Watched
                      </div>
                    )}

                    {/* RT scores */}
                    {(item.rtCriticScore != null || item.rtAudienceScore != null) && (
                      <div className="absolute bottom-2.5 left-2.5 flex items-center gap-2">
                        {item.rtCriticScore != null && (
                          <div className="flex items-center gap-1 rounded-full bg-black/70 px-2.5 py-1 backdrop-blur-sm">
                            <img src={rtCriticIcon(item.rtCriticScore)} alt="Critics" className="h-4 w-auto" />
                            <span className="text-[clamp(12px,1vw,14px)] font-bold text-white">{item.rtCriticScore}%</span>
                          </div>
                        )}
                        {item.rtAudienceScore != null && (
                          <div className="flex items-center gap-1 rounded-full bg-black/70 px-2.5 py-1 backdrop-blur-sm">
                            <img src={rtAudienceIcon(item.rtAudienceScore)} alt="Audience" className="h-4 w-auto" />
                            <span className="text-[clamp(12px,1vw,14px)] font-bold text-white">{item.rtAudienceScore}%</span>
                          </div>
                        )}
                      </div>
                    )}

                    {/* Glow ring on center card */}
                    {isCenter && (
                      <div className="absolute inset-0 rounded-2xl ring-2 ring-white/20" />
                    )}
                  </div>

                  {/* Title + meta */}
                  <div className="mt-3">
                    <p className={cn(
                      'font-bold leading-tight text-white line-clamp-2',
                      'text-[clamp(14px,1.3vw,18px)]',
                      isCenter && 'text-[clamp(17px,1.6vw,22px)]',
                    )}>
                      {item.title}
                    </p>
                    <p className={cn(
                      'mt-0.5 text-white/50',
                      'text-[clamp(12px,1.1vw,15px)]',
                      isCenter && 'text-[clamp(14px,1.3vw,17px)]',
                    )}>
                      {item.year ?? ''}{item.runtimeMinutes > 0 ? ` \u{00B7} ${item.runtimeMinutes}m` : ''}
                    </p>
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}

      {/* Library failed to load */}
      {loadError && (
        <div className="mb-10 text-center">
          <p className="text-[clamp(24px,3vw,36px)] font-medium text-white/60">Couldn{'\u2019'}t load your library</p>
        </div>
      )}

      {/* No results */}
      {noResults && !loadError && (
        <div className="mb-10 text-center">
          <p className="text-[clamp(24px,3vw,36px)] font-medium text-white/50">No matches found</p>
          <p className="mt-2 text-[clamp(16px,1.8vw,22px)] text-white/30">Try different preferences</p>
        </div>
      )}

      {/* Action buttons */}
      <div className="flex items-center gap-5">
        <button
          type="button"
          onClick={onStartOver}
          className={cn(
            'rounded-full bg-white/10 px-10 py-4 text-[clamp(18px,1.8vw,24px)] font-medium',
            'text-white/70 backdrop-blur-sm transition-all',
            'hover:bg-white/15 hover:text-white active:scale-95',
          )}
        >
          Start Over
        </button>
        <button
          type="button"
          onClick={onSpinAgain}
          className={cn(
            'rounded-full px-12 py-4 text-[clamp(18px,1.8vw,24px)] font-bold text-white',
            'bg-gradient-to-r from-amber-500 to-red-500',
            'shadow-[0_0_40px_rgba(245,158,11,0.3)]',
            'transition-all duration-300 hover:shadow-[0_0_60px_rgba(245,158,11,0.5)]',
            'active:scale-95',
          )}
        >
          Spin Again
        </button>
      </div>
    </div>
  );
}

export function PickForMeMode({ library, onOpenModal, vibeSettings, loadLibrary }: Props) {
  const isMobile = useIsMobile();
  const genreOptions = vibeSettings?.genres ?? DEFAULT_GENRES;
  const tonightMaxRuntime = vibeSettings?.tonightMaxRuntime ?? 100;
  const newerThanYears = vibeSettings?.newerThanYears ?? 1;
  const unwatchedBoostWeight = vibeSettings?.unwatchedBoostWeight ?? 3;

  // --- Phase & walkthrough state ---
  const [phase, setPhase] = useState<Phase>('walkthrough');
  const [step, setStep] = useState(0);
  const [direction, setDirection] = useState<'forward' | 'back'>('forward');
  const [answers, setAnswers] = useState<Answers>(INITIAL_ANSWERS);
  const animKey = useRef(0);

  // --- Spin state ---
  const [picks, setPicks] = useState<VibeItem[]>([]);
  const [noResults, setNoResults] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const shownKeys = useRef(new Set<number>());
  const lastBatchKeys = useRef(new Set<number>());
  const [unownedPool, setUnownedPool] = useState<VibeItem[]>([]);
  const poolFilterKeyRef = useRef<string>('');
  const enrichRef = useRef(0);

  // Ref to always call the latest spin function (avoids stale closure in timeouts)
  const spinRef = useRef<() => void>(() => {});

  // --- Navigation helpers ---

  const advanceStep = useCallback((dir: 'forward' | 'back', targetStep?: number) => {
    setDirection(dir);
    animKey.current++;
    if (targetStep !== undefined) {
      setStep(targetStep);
    } else if (dir === 'forward') {
      setStep((s) => Math.min(s + 1, STEP_COUNT - 1));
    } else {
      setStep((s) => Math.max(s - 1, 0));
    }
  }, []);

  const selectAndAdvance = useCallback(
    (update: Partial<Answers>) => {
      setAnswers((prev) => ({ ...prev, ...update }));
      // Trigger lazy library load when user selects "In Your Library"
      if (update.source === 'library' && loadLibrary) {
        loadLibrary().catch(() => {});
      }
      setTimeout(() => {
        if (step < STEP_COUNT - 1) {
          advanceStep('forward');
        } else {
          // Last step — go straight to spin
          spinRef.current();
        }
      }, 250);
    },
    [step, advanceStep, loadLibrary],
  );

  const goBack = useCallback(() => {
    if (step > 0) {
      advanceStep('back');
    }
  }, [step, advanceStep]);

  const goForward = useCallback(() => {
    if (step < STEP_COUNT - 1) {
      advanceStep('forward');
    } else {
      spinRef.current();
    }
  }, [step, advanceStep]);

  const startOver = useCallback(() => {
    setPhase('walkthrough');
    setStep(0);
    setDirection('forward');
    animKey.current++;
    setAnswers(INITIAL_ANSWERS);
    setPicks([]);
    setNoResults(false);
    setLoadError(false);
    shownKeys.current.clear();
    lastBatchKeys.current.clear();
    setUnownedPool([]);
    poolFilterKeyRef.current = '';
  }, []);

  // --- Build Filters from Answers ---

  const buildFilters = useCallback((): Filters => ({
    type: answers.type,
    unwatchedBoost: answers.unwatchedFirst && answers.source === 'library',
    tonightMode: answers.quickWatch && answers.type !== 'tv',
    era: answers.era,
    genres: answers.genres,
    unowned: answers.source === 'discover',
    rating: 'any',
  }), [answers]);

  // --- RT enrichment ---

  const enrichWithRt = useCallback(async (items: VibeItem[]) => {
    const batch = ++enrichRef.current;
    const enriched = await Promise.all(
      items.map(async (item) => {
        if (item.rtCriticScore != null && item.rtAudienceScore != null) return item;
        try {
          const { critic, audience } = await fetchRtScores(item);
          return {
            ...item,
            rtCriticScore: critic ?? item.rtCriticScore,
            rtAudienceScore: audience ?? item.rtAudienceScore,
          };
        } catch { /* ignore */ }
        return item;
      }),
    );
    if (batch === enrichRef.current) {
      setPicks(enriched);
    }
  }, []);

  // --- Spin ---

  const spin = useCallback(async () => {
    setPhase('spinning');
    setPicks([]);
    setNoResults(false);
    setLoadError(false);

    await new Promise((r) => setTimeout(r, 800));

    const filters = buildFilters();

    // Accumulate all shown items so no repeats across spins.
    // When the pool is exhausted, reset to only exclude the last batch
    // (prevents back-to-back duplicates while recycling older items).

    if (filters.unowned) {
      const currentKey = discoverFilterKey(filters);
      let pool = unownedPool;
      if (poolFilterKeyRef.current !== currentKey) {
        try {
          pool = await fetchDiscoverPool(filters, newerThanYears);
          setUnownedPool(pool);
          poolFilterKeyRef.current = currentKey;
          shownKeys.current.clear();
          lastBatchKeys.current.clear();
        } catch {
          pool = [];
        }
      }

      const genreOpt = filters.genres.length > 0 ? filters.genres : undefined;
      const unownedOpts = {
        type: filters.type,
        era: filters.era,
        genres: genreOpt,
        newerThanYears,
      };
      let results = pickRandomUnowned(pool, PICK_COUNT, {
        ...unownedOpts,
        exclude: shownKeys.current,
      });
      if (results.length === 0) {
        // Pool exhausted — reset, only exclude the previous spin's items
        shownKeys.current = new Set(lastBatchKeys.current);
        results = pickRandomUnowned(pool, PICK_COUNT, {
          ...unownedOpts,
          exclude: shownKeys.current,
        });
      }
      for (const item of results) shownKeys.current.add(item.ratingKey);
      lastBatchKeys.current = new Set(results.map((i) => i.ratingKey));
      const ordered = orderWithCenterPremium(results);
      setPicks(ordered);
      setNoResults(results.length === 0);
      if (ordered.length > 0) enrichWithRt(ordered);
    } else {
      const pickOpts = {
        type: filters.type,
        unwatchedBoost: filters.unwatchedBoost,
        maxRuntime: filters.tonightMode ? tonightMaxRuntime : undefined,
        era: filters.era,
        genres: filters.genres.length > 0 ? filters.genres : undefined,
        newerThanYears,
        unwatchedBoostWeight,
      };
      // The library loads lazily (started at "In Your Library"); a cold server index
      // can take several seconds, so wait for it here instead of spinning on [].
      let lib = library;
      if (lib.length === 0 && loadLibrary) {
        try {
          lib = await loadLibrary();
        } catch {
          lib = [];
        }
      }
      if (lib.length === 0) {
        setPicks([]);
        setLoadError(true);
        setPhase('results');
        return;
      }
      let results = pickRandom(lib, PICK_COUNT, {
        ...pickOpts,
        exclude: shownKeys.current,
      });
      if (results.length === 0) {
        // Pool exhausted — reset, only exclude the previous spin's items
        shownKeys.current = new Set(lastBatchKeys.current);
        results = pickRandom(lib, PICK_COUNT, {
          ...pickOpts,
          exclude: shownKeys.current,
        });
      }
      for (const item of results) shownKeys.current.add(item.ratingKey);
      lastBatchKeys.current = new Set(results.map((i) => i.ratingKey));
      const ordered = orderWithCenterPremium(results);
      setPicks(ordered);
      setNoResults(results.length === 0);
      if (ordered.length > 0) enrichWithRt(ordered);
    }

    setPhase('results');
  }, [library, loadLibrary, answers, unownedPool, buildFilters, tonightMaxRuntime, newerThanYears, unwatchedBoostWeight, enrichWithRt]);

  // Keep spinRef current so setTimeout closures always call the latest spin
  spinRef.current = spin;

  // Dynamic spinner text based on media type selection
  const spinnerText = answers.type === 'movie'
    ? 'Finding You a Movie to Watch'
    : answers.type === 'tv'
      ? 'Finding You a Show to Watch'
      : 'Finding You Something to Watch';

  // --- Render ---

  return (
    <div className="relative z-10 flex h-full flex-col items-center overflow-y-auto">
      <style>{`
        @keyframes slideInRight {
          from { opacity: 0; transform: translateX(60px); }
          to { opacity: 1; transform: translateX(0); }
        }
        @keyframes slideInLeft {
          from { opacity: 0; transform: translateX(-60px); }
          to { opacity: 1; transform: translateX(0); }
        }
        @keyframes spinPulse {
          0% { transform: scale(1); }
          50% { transform: scale(1.08); }
          100% { transform: scale(1); }
        }
        @keyframes cardReveal {
          0% { opacity: 0; transform: scale(0.7) rotateY(90deg); }
          60% { opacity: 1; transform: scale(1.05) rotateY(-5deg); }
          100% { opacity: 1; transform: scale(1) rotateY(0); }
        }
        @keyframes mobileCardReveal {
          0% { opacity: 0; transform: scale(0.92) translateY(16px); }
          100% { opacity: 1; transform: scale(1) translateY(0); }
        }
        @keyframes spinnerRotate { to { transform: rotate(360deg); } }
      `}</style>

      {/* ================================================================= */}
      {/*  WALKTHROUGH                                                       */}
      {/* ================================================================= */}
      {phase === 'walkthrough' && (
        <div className="flex flex-1 flex-col items-center justify-center px-4 sm:px-6">
          <div
            key={`step-${step}-${animKey.current}`}
            className="flex flex-col items-center"
            style={{
              animation: `${direction === 'forward' ? 'slideInRight' : 'slideInLeft'} 0.35s ease-out both`,
            }}
          >
            {/* Progress row: back button + dots */}
            <div className="mb-6 flex items-center gap-3 sm:mb-10 sm:gap-4">
              {/* Back button — occupies space even when hidden for layout stability */}
              <button
                type="button"
                onClick={goBack}
                className={cn(
                  'rounded-full bg-white/10 p-2.5 text-white/60 backdrop-blur-sm',
                  'transition-all hover:bg-white/20 hover:text-white active:scale-90',
                  step === 0 && 'pointer-events-none opacity-0',
                )}
                aria-hidden={step === 0}
                tabIndex={step === 0 ? -1 : 0}
              >
                <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
                </svg>
              </button>

              {/* Progress dots */}
              {Array.from({ length: STEP_COUNT }).map((_, i) => (
                <div
                  key={i}
                  className={cn(
                    'h-2.5 rounded-full transition-all duration-300',
                    i === step
                      ? 'w-8 bg-amber-400'
                      : i < step
                        ? 'w-2.5 bg-white/40'
                        : 'w-2.5 bg-white/15',
                  )}
                />
              ))}
            </div>

            {/* ---- Step 0: Media Type ---- */}
            {step === 0 && (
              <>
                <h2 className="mb-2 text-center text-[24px] font-bold leading-tight text-white sm:mb-3 sm:text-[clamp(32px,4vw,56px)]">
                  What are you in the mood for?
                </h2>
                <p className="mb-6 text-center text-[14px] text-white/40 sm:mb-10 sm:text-[clamp(16px,1.6vw,22px)]">
                  Let&apos;s narrow it down
                </p>
                <div className="flex w-full max-w-[340px] flex-col gap-3 sm:max-w-none sm:flex-row sm:flex-wrap sm:justify-center sm:gap-5">
                  {([
                    { value: 'movie' as const, icon: '\u{1F3AC}', label: 'Movie', desc: 'Feature films' },
                    { value: 'tv' as const, icon: '\u{1F4FA}', label: 'TV Shows', desc: 'Series & shows' },
                    { value: 'both' as const, icon: '\u{1F3B2}', label: 'Surprise Me', desc: 'Either one' },
                  ]).map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => selectAndAdvance({ type: opt.value })}
                      className={cn(
                        'group flex items-center gap-4 rounded-2xl border px-5 py-4',
                        'sm:flex-col sm:items-center sm:justify-center sm:rounded-3xl sm:px-0 sm:py-0',
                        'w-full sm:w-[clamp(170px,20vw,260px)] sm:h-[clamp(170px,20vw,260px)]',
                        'backdrop-blur-sm transition-all duration-200 active:scale-95',
                        answers.type === opt.value
                          ? 'border-amber-400/50 bg-amber-500/20 shadow-lg shadow-amber-500/10'
                          : 'border-white/10 bg-white/5 hover:border-white/25 hover:bg-white/10',
                      )}
                    >
                      <span className="text-[36px] sm:text-[clamp(44px,5vw,68px)]">{opt.icon}</span>
                      <div className="flex flex-col items-start sm:items-center">
                        <span className="text-[18px] font-semibold text-white sm:mt-3 sm:text-[clamp(18px,1.8vw,24px)]">
                          {opt.label}
                        </span>
                        <span className="text-[13px] text-white/40 sm:mt-1 sm:text-[clamp(13px,1.2vw,16px)]">
                          {opt.desc}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              </>
            )}

            {/* ---- Step 1: Source ---- */}
            {step === 1 && (
              <>
                <h2 className="mb-2 text-center text-[24px] font-bold leading-tight text-white sm:mb-3 sm:text-[clamp(32px,4vw,56px)]">
                  Where should we look?
                </h2>
                <p className="mb-6 text-center text-[14px] text-white/40 sm:mb-10 sm:text-[clamp(16px,1.6vw,22px)]">
                  Your collection or the wider world
                </p>
                <div className="flex w-full max-w-[340px] flex-col gap-3 sm:max-w-none sm:flex-row sm:flex-wrap sm:justify-center sm:gap-5">
                  {([
                    { value: 'library' as const, icon: '\u{1F4DA}', label: 'In Your Library', desc: 'From your collection' },
                    { value: 'discover' as const, icon: '\u{2728}', label: 'Something New', desc: "Titles you don't own" },
                  ]).map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => selectAndAdvance({ source: opt.value })}
                      className={cn(
                        'group flex items-center gap-4 rounded-2xl border px-5 py-4',
                        'sm:flex-col sm:items-center sm:justify-center sm:rounded-3xl sm:px-0 sm:py-0',
                        'w-full sm:w-[clamp(180px,22vw,280px)] sm:h-[clamp(170px,20vw,260px)]',
                        'backdrop-blur-sm transition-all duration-200 active:scale-95',
                        answers.source === opt.value
                          ? 'border-amber-400/50 bg-amber-500/20 shadow-lg shadow-amber-500/10'
                          : 'border-white/10 bg-white/5 hover:border-white/25 hover:bg-white/10',
                      )}
                    >
                      <span className="text-[36px] sm:text-[clamp(44px,5vw,68px)]">{opt.icon}</span>
                      <div className="flex flex-col items-start sm:items-center">
                        <span className="text-[18px] font-semibold text-white sm:mt-3 sm:text-[clamp(18px,1.8vw,24px)]">
                          {opt.label}
                        </span>
                        <span className="text-[13px] text-white/40 sm:mt-1 sm:text-[clamp(13px,1.2vw,16px)]">
                          {opt.desc}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              </>
            )}

            {/* ---- Step 2: Genre ---- */}
            {step === 2 && (
              <>
                <h2 className="mb-2 text-center text-[24px] font-bold leading-tight text-white sm:mb-3 sm:text-[clamp(32px,4vw,56px)]">
                  What sounds good?
                </h2>
                <p className="mb-5 text-center text-[14px] text-white/40 sm:mb-8 sm:text-[clamp(16px,1.6vw,22px)]">
                  Pick as many as you like, or skip for anything
                </p>

                {/* Genre chips — large touch targets */}
                <div className="mb-6 flex max-w-[340px] flex-wrap items-center justify-center gap-2.5 sm:mb-10 sm:max-w-[900px] sm:gap-3.5">
                  {genreOptions.map((genre) => {
                    const active = answers.genres.includes(genre);
                    return (
                      <button
                        key={genre}
                        type="button"
                        onClick={() =>
                          setAnswers((prev) => ({
                            ...prev,
                            genres: active
                              ? prev.genres.filter((g) => g !== genre)
                              : [...prev.genres, genre],
                          }))
                        }
                        className={cn(
                          'rounded-full px-4 py-2.5 text-[14px] font-medium sm:px-7 sm:py-3.5 sm:text-[clamp(17px,1.7vw,22px)]',
                          'transition-all duration-200 active:scale-95',
                          active
                            ? 'bg-amber-500/30 text-amber-200 shadow-lg ring-1 ring-amber-500/40'
                            : 'bg-white/5 text-white/40 hover:bg-white/10 hover:text-white/60',
                        )}
                      >
                        {genre}
                      </button>
                    );
                  })}
                </div>

                {/* Action row */}
                <div className="flex items-center gap-3 sm:gap-5">
                  <button
                    type="button"
                    onClick={() => selectAndAdvance({ genres: [] })}
                    className={cn(
                      'rounded-full bg-white/10 px-6 py-3 text-[15px] font-medium sm:px-10 sm:py-4 sm:text-[clamp(18px,1.8vw,24px)]',
                      'text-white/60 backdrop-blur-sm transition-all',
                      'hover:bg-white/15 hover:text-white/80 active:scale-95',
                    )}
                  >
                    Anything Goes
                  </button>
                  {answers.genres.length > 0 && (
                    <button
                      type="button"
                      onClick={goForward}
                      className={cn(
                        'rounded-full bg-amber-500/20 px-6 py-3 text-[15px] font-medium sm:px-10 sm:py-4 sm:text-[clamp(18px,1.8vw,24px)]',
                        'text-amber-200 ring-1 ring-amber-500/30',
                        'transition-all hover:bg-amber-500/30 active:scale-95',
                      )}
                    >
                      Next ({answers.genres.length})
                    </button>
                  )}
                </div>
              </>
            )}

            {/* ---- Step 3: Era ---- */}
            {step === 3 && (
              <>
                <h2 className="mb-2 text-center text-[24px] font-bold leading-tight text-white sm:mb-3 sm:text-[clamp(32px,4vw,56px)]">
                  How recent?
                </h2>
                <p className="mb-6 text-center text-[14px] text-white/40 sm:mb-10 sm:text-[clamp(16px,1.6vw,22px)]">
                  Narrow it down or keep your options open
                </p>
                <div className="flex w-full max-w-[340px] flex-col gap-3 sm:max-w-none sm:flex-row sm:flex-wrap sm:justify-center sm:gap-5">
                  {([
                    { value: 'newer' as const, icon: '\u{1F525}', label: 'A Recent Release', desc: 'From the last few years' },
                    { value: 'older' as const, icon: '\u{1F4FD}\u{FE0F}', label: 'An Older Favorite', desc: 'A tried and true classic' },
                    { value: 'any' as const, icon: '\u{1F30D}', label: "Doesn\u{2019}t Matter", desc: 'Any decade works' },
                  ]).map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => selectAndAdvance({ era: opt.value })}
                      className={cn(
                        'group flex items-center gap-4 rounded-2xl border px-5 py-4',
                        'sm:flex-col sm:items-center sm:justify-center sm:rounded-3xl sm:px-0 sm:py-0',
                        'w-full sm:w-[clamp(170px,20vw,260px)] sm:h-[clamp(170px,20vw,260px)]',
                        'backdrop-blur-sm transition-all duration-200 active:scale-95',
                        answers.era === opt.value
                          ? 'border-amber-400/50 bg-amber-500/20 shadow-lg shadow-amber-500/10'
                          : 'border-white/10 bg-white/5 hover:border-white/25 hover:bg-white/10',
                      )}
                    >
                      <span className="text-[36px] sm:text-[clamp(44px,5vw,68px)]">{opt.icon}</span>
                      <div className="flex flex-col items-start sm:items-center">
                        <span className="text-[16px] font-semibold leading-snug text-white sm:mt-3 sm:text-center sm:text-[clamp(16px,1.6vw,21px)]">
                          {opt.label}
                        </span>
                        <span className="text-[13px] text-white/40 sm:mt-1 sm:text-[clamp(13px,1.2vw,16px)]">
                          {opt.desc}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* ================================================================= */}
      {/*  SPINNING                                                          */}
      {/* ================================================================= */}
      {phase === 'spinning' && (
        <div className="flex flex-1 flex-col items-center justify-center">
          <div
            className="mb-8 h-[clamp(120px,15vw,192px)] w-[clamp(120px,15vw,192px)] rounded-full border-4 border-white/10 border-t-amber-400"
            style={{ animation: 'spinnerRotate 0.5s linear infinite' }}
          />
          <p className="text-[clamp(22px,2.5vw,32px)] font-semibold text-white/50">
            {spinnerText}
          </p>
        </div>
      )}

      {/* ================================================================= */}
      {/*  RESULTS                                                           */}
      {/* ================================================================= */}
      {phase === 'results' && (
        isMobile
          ? <MobileResults
              picks={picks}
              noResults={noResults}
              loadError={loadError}
              onOpenModal={onOpenModal}
              onStartOver={startOver}
              onSpinAgain={spin}
            />
          : <DesktopResults
              picks={picks}
              noResults={noResults}
              loadError={loadError}
              onOpenModal={onOpenModal}
              onStartOver={startOver}
              onSpinAgain={spin}
            />
      )}
    </div>
  );
}
