/**
 * Describe It — AI-powered natural language search mode.
 *
 * 3 phases: Input → Searching → Results
 * Searches both Plex library (owned) and TMDb (unowned).
 */

import { useState, useRef, useCallback, useEffect, type MutableRefObject } from 'react';
import { performNaturalSearch, type UnownedItem } from '@/services/naturalSearchApi';
import { DescribeResultsCarousel } from './DescribeResultsCarousel';
import { MobileDescribeResults } from './MobileDescribeResults';
import { cn } from '@/utils/cn';
import type { VibeItem } from '@/types/vibe';

interface Props {
  library: VibeItem[];
  onOpenModal: (item: VibeItem) => void;
  isMobile: boolean;
  /** Ref that VibeEnginePage reads on popstate — returns true if back was handled (results→input) */
  backHandlerRef?: MutableRefObject<(() => boolean) | null>;
  /** Pre-filled query from voice — auto-triggers search */
  autoQuery?: string | null;
  /** Called after autoQuery is consumed */
  onAutoQueryConsumed?: () => void;
}

type Phase = 'input' | 'searching' | 'results';

/** Combined recommendation: VibeItem + AI reasoning + ownership */
export interface Recommendation {
  vibeItem: VibeItem;
  aiCategory: string;
  aiReason: string;
  owned: boolean;
  aiRank: number;
}

/** Map category text to a distinct color palette */
export function categoryColor(cat: string): { bg: string; text: string; border: string } {
  const lower = cat.toLowerCase();
  if (lower.includes('comedy') || lower.includes('funny') || lower.includes('laugh'))
    return { bg: 'bg-amber-500/15', text: 'text-amber-300', border: 'border-amber-500/25' };
  if (lower.includes('cozy') || lower.includes('comfort') || lower.includes('warm') || lower.includes('feel-good') || lower.includes('feel good'))
    return { bg: 'bg-orange-500/15', text: 'text-orange-300', border: 'border-orange-500/25' };
  if (lower.includes('classic') || lower.includes('timeless') || lower.includes('iconic'))
    return { bg: 'bg-yellow-500/15', text: 'text-yellow-300', border: 'border-yellow-500/25' };
  if (lower.includes('dark') || lower.includes('gritty') || lower.includes('noir') || lower.includes('intense'))
    return { bg: 'bg-slate-500/15', text: 'text-slate-300', border: 'border-slate-500/25' };
  if (lower.includes('thrill') || lower.includes('suspense') || lower.includes('edge') || lower.includes('tension'))
    return { bg: 'bg-red-500/15', text: 'text-red-300', border: 'border-red-500/25' };
  if (lower.includes('mind') || lower.includes('thought') || lower.includes('cerebral') || lower.includes('twist'))
    return { bg: 'bg-violet-500/15', text: 'text-violet-300', border: 'border-violet-500/25' };
  if (lower.includes('adventure') || lower.includes('epic') || lower.includes('action'))
    return { bg: 'bg-sky-500/15', text: 'text-sky-300', border: 'border-sky-500/25' };
  if (lower.includes('chill') || lower.includes('relax') || lower.includes('easy') || lower.includes('lazy') || lower.includes('light'))
    return { bg: 'bg-teal-500/15', text: 'text-teal-300', border: 'border-teal-500/25' };
  if (lower.includes('hidden') || lower.includes('gem') || lower.includes('underrated') || lower.includes('sleeper'))
    return { bg: 'bg-emerald-500/15', text: 'text-emerald-300', border: 'border-emerald-500/25' };
  if (lower.includes('drama') || lower.includes('emotional') || lower.includes('moving') || lower.includes('heart'))
    return { bg: 'bg-pink-500/15', text: 'text-pink-300', border: 'border-pink-500/25' };
  if (lower.includes('horror') || lower.includes('scary') || lower.includes('creep'))
    return { bg: 'bg-purple-500/15', text: 'text-purple-300', border: 'border-purple-500/25' };
  if (lower.includes('quirky') || lower.includes('indie') || lower.includes('offbeat') || lower.includes('weird'))
    return { bg: 'bg-fuchsia-500/15', text: 'text-fuchsia-300', border: 'border-fuchsia-500/25' };
  // Default
  return { bg: 'bg-rose-500/15', text: 'text-rose-300', border: 'border-rose-500/25' };
}

const EXAMPLE_PROMPTS = [
  'A dark 90s thriller like Se7en',
  'Cozy comfort movie for a rainy day',
  'Mind-bending sci-fi that makes you think',
  'Feel-good comedy with great ensemble cast',
  'Atmospheric horror with slow build tension',
  'Epic adventure with stunning visuals',
  'Quirky indie film with dry humor',
  'Gripping true crime documentary',
];

/** Convert an unowned TMDb result to a VibeItem for modal opening */
function unownedToVibeItem(item: UnownedItem, index: number): VibeItem {
  return {
    ratingKey: -(index + 1),
    title: item.title,
    year: item.year,
    type: item.type,
    runtimeMinutes: item.runtime_minutes,
    genres: item.genres,
    summary: item.overview,
    contentRating: item.content_rating,
    rating: item.rating,
    audienceRating: null,
    posterUrl: item.poster_url,
    backdropUrl: item.backdrop_url,
    tmdbId: item.tmdb_id,
    imdbId: null,
    viewCount: 0,
    lastViewedAt: 0,
    addedAt: 0,
    libraryName: '',
    voteCount: item.vote_count,
    rtCriticScore: null,
    rtAudienceScore: null,
  };
}

/** Convert a raw owned result (snake_case) to VibeItem */
function ownedToVibeItem(raw: Record<string, unknown>): VibeItem {
  return {
    ratingKey: (raw.rating_key as number) ?? 0,
    title: (raw.title as string) ?? '',
    year: (raw.year as number) ?? null,
    type: (raw.type as 'movie' | 'tv') ?? 'movie',
    runtimeMinutes: (raw.runtime_minutes as number) ?? 0,
    genres: (raw.genres as string[]) ?? [],
    summary: (raw.summary as string) ?? '',
    contentRating: (raw.content_rating as string) ?? '',
    rating: (raw.rating as number) ?? null,
    audienceRating: (raw.audience_rating as number) ?? null,
    posterUrl: (raw.poster_url as string) ?? null,
    backdropUrl: (raw.backdrop_url as string) ?? null,
    tmdbId: (raw.tmdb_id as number) ?? null,
    imdbId: (raw.imdb_id as string) ?? null,
    viewCount: (raw.view_count as number) ?? 0,
    lastViewedAt: (raw.last_viewed_at as number) ?? 0,
    addedAt: (raw.added_at as number) ?? 0,
    libraryName: (raw.library_name as string) ?? '',
    rtCriticScore: (raw.rt_critic_score as number) ?? null,
    rtAudienceScore: (raw.rt_audience_score as number) ?? null,
  };
}

export function DescribeMode({ library: _library, onOpenModal, isMobile, backHandlerRef, autoQuery, onAutoQueryConsumed }: Props) {
  const [phase, setPhase] = useState<Phase>('input');
  const [query, setQuery] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const phaseRef = useRef(phase);
  phaseRef.current = phase;

  // Focus textarea on mount
  useEffect(() => {
    if (phase === 'input') {
      setTimeout(() => textareaRef.current?.focus(), 300);
    }
  }, [phase]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // Register back handler so VibeEnginePage's popstate can route
  // results→input or searching→input (cancel in-flight search)
  useEffect(() => {
    if (!backHandlerRef) return;
    backHandlerRef.current = () => {
      const p = phaseRef.current;
      if (p === 'results' || p === 'searching') {
        abortRef.current?.abort();
        setPhase('input');
        setRecommendations([]);
        setError(null);
        return true;
      }
      return false;
    };
    return () => { backHandlerRef.current = null; };
  }, [backHandlerRef]);

  const handleSearch = useCallback(async () => {
    const trimmed = query.trim();
    if (!trimmed) return;

    // Push sentinel while describe input is still visible — iOS captures this
    // as the snapshot for swipe-back, so swiping from results shows the input page.
    if (isMobile) {
      window.history.pushState({ describeSearch: true }, '');
    }

    setPhase('searching');
    setError(null);

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const data = await performNaturalSearch(trimmed, undefined, controller.signal);
      if (controller.signal.aborted) return;

      // Build unified recommendation list sorted by AI rank (best match first)
      const recs: Recommendation[] = [];

      for (const raw of data.owned || []) {
        const r = raw as unknown as Record<string, unknown>;
        recs.push({
          vibeItem: ownedToVibeItem(r),
          aiCategory: (r.ai_category as string) || '',
          aiReason: (r.ai_reason as string) || '',
          owned: true,
          aiRank: (r.ai_rank as number) ?? 999,
        });
      }

      (data.unowned || []).forEach((item, i) => {
        recs.push({
          vibeItem: unownedToVibeItem(item, i),
          aiCategory: item.ai_category || '',
          aiReason: item.ai_reason || '',
          owned: false,
          aiRank: item.ai_rank ?? 999,
        });
      });

      // Sort by AI relevance rank — best match first
      recs.sort((a, b) => a.aiRank - b.aiRank);
      setRecommendations(recs);
      setPhase('results');
    } catch (err: unknown) {
      if (controller.signal.aborted) return;
      const msg = err instanceof Error ? err.message : 'Search failed';
      setError(msg);
      setPhase('input');
    }
  }, [query, isMobile]);

  // Auto-search when launched from voice with a pre-filled query
  const autoConsumedRef = useRef(false);
  useEffect(() => {
    if (autoQuery && !autoConsumedRef.current) {
      autoConsumedRef.current = true;
      setQuery(autoQuery);
      onAutoQueryConsumed?.();
    }
  }, [autoQuery, onAutoQueryConsumed]);

  // Trigger search once query is set from autoQuery
  useEffect(() => {
    if (autoConsumedRef.current && query && phase === 'input') {
      autoConsumedRef.current = false;
      handleSearch();
    }
  }, [query]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSearchAgain = useCallback(() => {
    setPhase('input');
    setRecommendations([]);
    setError(null);
  }, []);

  /** Triggered when user taps a category badge — searches for more like that vibe */
  const handleCategorySearch = useCallback((category: string) => {
    // Push sentinel so swipe-back from new results returns to input
    if (isMobile) {
      window.history.pushState({ describeSearch: true }, '');
    }

    setQuery(`More movies and shows with a "${category}" vibe`);
    setRecommendations([]);
    setError(null);
    setPhase('searching');

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    performNaturalSearch(`More movies and shows with a "${category}" vibe`, undefined, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        const recs: Recommendation[] = [];
        for (const raw of data.owned || []) {
          const r = raw as unknown as Record<string, unknown>;
          recs.push({
            vibeItem: ownedToVibeItem(r),
            aiCategory: (r.ai_category as string) || '',
            aiReason: (r.ai_reason as string) || '',
            owned: true,
            aiRank: (r.ai_rank as number) ?? 999,
          });
        }
        (data.unowned || []).forEach((item, i) => {
          recs.push({
            vibeItem: unownedToVibeItem(item, i),
            aiCategory: item.ai_category || '',
            aiReason: item.ai_reason || '',
            owned: false,
            aiRank: item.ai_rank ?? 999,
          });
        });
        recs.sort((a, b) => a.aiRank - b.aiRank);
        setRecommendations(recs);
        setPhase('results');
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const msg = err instanceof Error ? err.message : 'Search failed';
        setError(msg);
        setPhase('input');
      });
  }, [isMobile]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSearch();
    }
  }, [handleSearch]);

  // Searching phase: cycling status messages
  const STATUS_MESSAGES = [
    'Scanning thousands of titles…',
    'Analyzing your description…',
    'Finding the best matches…',
    'Almost there…',
  ];
  const [statusIndex, setStatusIndex] = useState(0);
  useEffect(() => {
    if (phase !== 'searching') { setStatusIndex(0); return; }
    const id = setInterval(() => setStatusIndex((i) => i + 1), 3500);
    return () => clearInterval(id);
  }, [phase]);

  // Searching phase: elapsed dots
  const [dotCount, setDotCount] = useState(0);
  useEffect(() => {
    if (phase !== 'searching') { setDotCount(0); return; }
    const id = setInterval(() => setDotCount((c) => Math.min(c + 1, 10)), 5000);
    return () => clearInterval(id);
  }, [phase]);

  return (
    <div className="relative z-10 flex h-full flex-col overflow-y-auto bg-kiosk-black">
      <style>{`
        @keyframes descFadeIn {
          from { opacity: 0; transform: translateY(20px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes descPulse {
          0%, 100% { opacity: 0.5; }
          50% { opacity: 1; }
        }
        @keyframes descCardReveal {
          from { opacity: 0; transform: translateY(16px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes spinnerRotate { to { transform: rotate(360deg); } }
        @keyframes orbitSpin { to { transform: rotate(360deg); } }
        @keyframes orbitSpinReverse { to { transform: rotate(-360deg); } }
        @keyframes orbGlow {
          0%, 100% { transform: scale(0.9); opacity: 0.5; }
          50% { transform: scale(1.1); opacity: 1; }
        }
        @keyframes statusFadeIn {
          from { opacity: 0; transform: translateY(8px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes dotAppear {
          from { transform: scale(0); opacity: 0; }
          to { transform: scale(1); opacity: 1; }
        }
      `}</style>

      {/* -- INPUT PHASE -- */}
      {phase === 'input' && (
        <div
          className="flex flex-1 flex-col items-center justify-center px-5 sm:px-8"
          style={{ animation: 'descFadeIn 0.5s ease-out both' }}
        >
          <h2 className="mb-2 text-center text-[24px] font-bold text-white sm:text-[clamp(36px,4vw,56px)]">
            Describe what you want
          </h2>
          <p className="mb-6 text-center text-[14px] text-white/40 sm:mb-8 sm:text-[clamp(16px,1.6vw,20px)]">
            Tell us in your own words — Luma will find the perfect match
          </p>

          {/* Textarea */}
          <div className="w-full max-w-[560px]">
            <textarea
              ref={textareaRef}
              value={query}
              onChange={(e) => setQuery(e.target.value.slice(0, 500))}
              onKeyDown={handleKeyDown}
              placeholder="e.g. A dark 90s thriller like Se7en with a twist ending..."
              rows={3}
              className={cn(
                'w-full resize-none rounded-2xl border bg-white/5 px-5 py-4',
                'text-[16px] text-white placeholder:text-white/25',
                'outline-none backdrop-blur-md transition-all duration-200',
                'focus:border-white/25 focus:bg-white/8 focus:ring-1 focus:ring-white/15',
                error ? 'border-red-500/50' : 'border-white/10',
              )}
            />
            <div className="mt-1.5 flex items-center justify-between px-1">
              <span className="text-[12px] text-white/25">{query.length}/500</span>
              {error && (
                <span className="text-[12px] text-red-400">{error}</span>
              )}
            </div>
          </div>

          {/* Example prompt chips */}
          <div className="mt-5 flex max-w-[560px] flex-wrap justify-center gap-2 sm:mt-6">
            {EXAMPLE_PROMPTS.map((prompt) => (
              <button
                key={prompt}
                type="button"
                onClick={() => setQuery(prompt)}
                className={cn(
                  'rounded-full border border-white/8 bg-white/5 px-3.5 py-1.5',
                  'text-[12px] text-white/40 transition-all duration-200 sm:text-[13px]',
                  'hover:border-white/20 hover:bg-white/10 hover:text-white/60',
                  'active:scale-95',
                )}
              >
                {prompt}
              </button>
            ))}
          </div>

          {/* Submit button */}
          <button
            type="button"
            onClick={handleSearch}
            disabled={!query.trim()}
            className={cn(
              'mt-8 rounded-full px-10 py-4 text-[17px] font-bold text-white sm:text-[20px]',
              'transition-all duration-300 active:scale-95',
              query.trim()
                ? 'bg-white/15 border border-white/20 hover:bg-white/22 shadow-[0_0_30px_rgba(255,255,255,0.06)]'
                : 'bg-white/6 text-white/30 cursor-not-allowed border border-white/8',
            )}
          >
            Find Something
          </button>
        </div>
      )}

      {/* -- SEARCHING PHASE -- */}
      {phase === 'searching' && (
        <div
          className="flex flex-1 flex-col items-center justify-center gap-6"
          style={{ animation: 'descFadeIn 0.5s ease-out both' }}
        >
          {/* Luma title */}
          <h2
            className="text-[clamp(28px,4vw,44px)] font-bold tracking-wide text-white/80"
            style={{ animation: 'descFadeIn 0.6s ease-out both' }}
          >
            Luma
          </h2>

          {/* Orbital rings + center orb */}
          <div className="relative flex items-center justify-center"
            style={{ width: 'clamp(120px,18vw,180px)', height: 'clamp(120px,18vw,180px)' }}
          >
            {/* Outer ring */}
            <div
              className="absolute inset-0 rounded-full border-[2px] border-dashed border-white/10"
              style={{ animation: 'orbitSpin 20s linear infinite' }}
            />
            {/* Middle ring */}
            <div
              className="absolute rounded-full border-[1.5px] border-dotted border-white/20"
              style={{
                inset: '15%',
                animation: 'orbitSpinReverse 12s linear infinite',
              }}
            />
            {/* Inner ring */}
            <div
              className="absolute rounded-full border-[1px] border-dashed border-white/8"
              style={{
                inset: '30%',
                animation: 'orbitSpin 8s linear infinite',
              }}
            />
            {/* Center orb */}
            <div
              className="relative z-10 rounded-full"
              style={{
                width: '28%',
                height: '28%',
                background: 'radial-gradient(circle, rgba(255,255,255,0.6) 0%, rgba(255,255,255,0.15) 50%, transparent 70%)',
                boxShadow: '0 0 40px 10px rgba(255,255,255,0.1)',
                animation: 'orbGlow 3s ease-in-out infinite',
              }}
            />
          </div>

          {/* Cycling status text */}
          <p
            key={statusIndex}
            className="text-[clamp(16px,2.2vw,24px)] font-medium text-white/60"
            style={{ animation: 'statusFadeIn 0.4s ease-out both' }}
          >
            {STATUS_MESSAGES[statusIndex % STATUS_MESSAGES.length]}
          </p>

          {/* User's query */}
          <p className="max-w-[400px] truncate px-4 text-center text-[clamp(13px,1.5vw,16px)] italic text-white/25">
            &ldquo;{query.length > 80 ? query.slice(0, 80) + '…' : query}&rdquo;
          </p>

          {/* Elapsed dots */}
          {dotCount > 0 && (
            <div className="flex items-center gap-1.5">
              {Array.from({ length: dotCount }, (_, i) => (
                <div
                  key={i}
                  className="h-[6px] w-[6px] rounded-full bg-white/40"
                  style={{ animation: `dotAppear 0.3s ease-out ${i * 0.05}s both` }}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* -- RESULTS PHASE -- */}
      {phase === 'results' && recommendations.length === 0 && (
        <div
          className="flex flex-1 flex-col items-center justify-center"
          style={{ animation: 'descFadeIn 0.4s ease-out both' }}
        >
          <p className="text-[24px] font-medium text-white/50 sm:text-[32px]">No matches found</p>
          <p className="mt-2 text-[14px] text-white/30 sm:text-[16px]">Try describing something different</p>
          <button
            type="button"
            onClick={handleSearchAgain}
            className="mt-6 rounded-full bg-white/10 px-8 py-3 text-[16px] font-medium text-white/70 transition-all hover:bg-white/15 active:scale-95"
          >
            Try Again
          </button>
        </div>
      )}

      {/* Results: mobile card list or desktop/tablet carousel */}
      {phase === 'results' && recommendations.length > 0 && (
        isMobile ? (
          <MobileDescribeResults
            recommendations={recommendations}
            onClose={handleSearchAgain}
            onOpenDetail={onOpenModal}
            onCategorySearch={handleCategorySearch}
          />
        ) : (
          <DescribeResultsCarousel
            recommendations={recommendations}
            onClose={handleSearchAgain}
            onOpenDetail={onOpenModal}
            isMobile={false}
            onCategorySearch={handleCategorySearch}
          />
        )
      )}
    </div>
  );
}
