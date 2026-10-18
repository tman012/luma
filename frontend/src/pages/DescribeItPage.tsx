/**
 * DescribeItPage — AI-powered natural language search as a sheet.
 *
 * Opens as a bottom sheet (same as WatchlistSheet/LumaAISheet) on mobile.
 * 3 phases: Input → Searching → Results
 */

import { useState, useRef, useCallback, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router';
import { useModalHost } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { useUiDensity } from '@/hooks/useUiDensity';
import { performNaturalSearch, type UnownedItem } from '@/services/naturalSearchApi';
import { categoryColor } from '@/pages/vibe/DescribeMode';
import type { Recommendation } from '@/pages/vibe/DescribeMode';
import { cn } from '@/utils/cn';
import type { VibeItem } from '@/types/vibe';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function unownedToVibeItem(item: UnownedItem, index: number): VibeItem {
  return {
    ratingKey: -(index + 1), title: item.title, year: item.year, type: item.type,
    runtimeMinutes: item.runtime_minutes, genres: item.genres, summary: item.overview,
    contentRating: item.content_rating, rating: item.rating, audienceRating: null,
    posterUrl: item.poster_url, backdropUrl: item.backdrop_url, tmdbId: item.tmdb_id,
    imdbId: null, viewCount: 0, lastViewedAt: 0, addedAt: 0, libraryName: '',
    voteCount: item.vote_count, rtCriticScore: null, rtAudienceScore: null,
  };
}

function ownedToVibeItem(raw: Record<string, unknown>): VibeItem {
  return {
    ratingKey: (raw.rating_key as number) ?? 0, title: (raw.title as string) ?? '',
    year: (raw.year as number) ?? null, type: (raw.type as 'movie' | 'tv') ?? 'movie',
    runtimeMinutes: (raw.runtime_minutes as number) ?? 0, genres: (raw.genres as string[]) ?? [],
    summary: (raw.summary as string) ?? '', contentRating: (raw.content_rating as string) ?? '',
    rating: (raw.rating as number) ?? null, audienceRating: (raw.audience_rating as number) ?? null,
    posterUrl: (raw.poster_url as string) ?? null, backdropUrl: (raw.backdrop_url as string) ?? null,
    tmdbId: (raw.tmdb_id as number) ?? null, imdbId: (raw.imdb_id as string) ?? null,
    viewCount: (raw.view_count as number) ?? 0, lastViewedAt: (raw.last_viewed_at as number) ?? 0,
    addedAt: (raw.added_at as number) ?? 0, libraryName: (raw.library_name as string) ?? '',
    rtCriticScore: (raw.rt_critic_score as number) ?? null, rtAudienceScore: (raw.rt_audience_score as number) ?? null,
  };
}

function buildRecs(data: { owned?: unknown[]; unowned?: UnownedItem[] }): Recommendation[] {
  const recs: Recommendation[] = [];
  for (const raw of data.owned || []) {
    const r = raw as unknown as Record<string, unknown>;
    recs.push({ vibeItem: ownedToVibeItem(r), aiCategory: (r.ai_category as string) || '', aiReason: (r.ai_reason as string) || '', owned: true, aiRank: (r.ai_rank as number) ?? 999 });
  }
  (data.unowned || []).forEach((item, i) => {
    recs.push({ vibeItem: unownedToVibeItem(item, i), aiCategory: item.ai_category || '', aiReason: item.ai_reason || '', owned: false, aiRank: item.ai_rank ?? 999 });
  });
  recs.sort((a, b) => a.aiRank - b.aiRank);
  return recs;
}

const EXAMPLE_PROMPTS = [
  'A dark 90s thriller like Se7en',
  'Cozy comfort movie for a rainy day',
  'Mind-bending sci-fi that makes you think',
  'Feel-good comedy with great ensemble cast',
  'Epic adventure with stunning visuals',
];

const STATUS_MESSAGES = [
  'Scanning thousands of titles\u2026',
  'Analyzing your description\u2026',
  'Finding the best matches\u2026',
  'Almost there\u2026',
];

const DISMISS_THRESHOLD = 120;
const ENTER_DURATION = 600;
const EXIT_DURATION = 400;
const SPRING = 'cubic-bezier(0.16, 1, 0.3, 1)';

type SearchPhase = 'input' | 'searching' | 'results';

// ---------------------------------------------------------------------------
// Main component — auto-opens as sheet on mount, navigates back on dismiss
// ---------------------------------------------------------------------------

export function DescribeItPage() {
  const navigate = useNavigate();
  const { openMovie, openTv, close } = useModalHost();
  const d = useUiDensity();
  const isMobile = d.mode === 'compact';

  // --- Sheet animation ---
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });
  const [dragOffset, setDragOffset] = useState(0);
  const [sheetPhase, setSheetPhase] = useState<'entering' | 'visible' | 'exiting' | 'gone'>('entering');

  useEffect(() => {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => setSheetPhase('visible'));
    });
  }, []);

  useEffect(() => {
    const prevBg = document.documentElement.style.background;
    const prevOverflow = document.body.style.overflow;
    document.documentElement.style.background = '#000';
    document.body.style.overflow = 'hidden';
    return () => {
      document.documentElement.style.background = prevBg;
      document.body.style.overflow = prevOverflow;
    };
  }, []);

  const handleDismiss = useCallback(() => {
    setDragOffset(0);
    requestAnimationFrame(() => {
      setSheetPhase('exiting');
      setTimeout(() => navigate('/', { replace: true }), EXIT_DURATION);
    });
  }, [navigate]);

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

  // --- Search state ---
  const [phase, setPhase] = useState<SearchPhase>('input');
  const [query, setQuery] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (phase === 'input') setTimeout(() => textareaRef.current?.focus(), 400);
  }, [phase]);

  useEffect(() => { return () => { abortRef.current?.abort(); }; }, []);

  const [statusIndex, setStatusIndex] = useState(0);
  useEffect(() => {
    if (phase !== 'searching') { setStatusIndex(0); return; }
    const id = setInterval(() => setStatusIndex((i) => i + 1), 3500);
    return () => clearInterval(id);
  }, [phase]);

  const [dotCount, setDotCount] = useState(0);
  useEffect(() => {
    if (phase !== 'searching') { setDotCount(0); return; }
    const id = setInterval(() => setDotCount((c) => Math.min(c + 1, 10)), 5000);
    return () => clearInterval(id);
  }, [phase]);

  const handleSearch = useCallback(async () => {
    const trimmed = query.trim();
    if (!trimmed) return;
    setPhase('searching');
    setError(null);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const data = await performNaturalSearch(trimmed, undefined, controller.signal);
      if (controller.signal.aborted) return;
      setRecommendations(buildRecs(data));
      setPhase('results');
    } catch (err: unknown) {
      if (controller.signal.aborted) return;
      setError(err instanceof Error ? err.message : 'Search failed');
      setPhase('input');
    }
  }, [query]);

  const handleSearchAgain = useCallback(() => {
    setPhase('input');
    setRecommendations([]);
    setError(null);
  }, []);

  const handleCategorySearch = useCallback((category: string) => {
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
        setRecommendations(buildRecs(data));
        setPhase('results');
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : 'Search failed');
        setPhase('input');
      });
  }, []);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSearch(); }
  }, [handleSearch]);

  const makePersonSearch = useCallback((name: string, personId?: number) => {
    close();
    const params = new URLSearchParams({ q: name, type: 'people' });
    if (personId) params.set('personId', String(personId));
    navigate(`/?${params.toString()}`);
  }, [close, navigate]);

  const handleOpenModal = useCallback((item: VibeItem) => {
    openModalForItem(
      { openMovie, openTv },
      { mediaType: item.type, tmdbId: item.tmdbId, ratingKey: item.ratingKey > 0 ? item.ratingKey : undefined },
      'vibe_engine',
      { onPersonSearch: makePersonSearch },
    );
  }, [openMovie, openTv, makePersonSearch]);

  // --- Sheet transform ---
  const sheetTransform =
    dragOffset > 0 ? `translate3d(0, ${dragOffset}px, 0)`
      : sheetPhase === 'visible' ? 'translate3d(0, 0, 0)'
        : 'translate3d(0, 100%, 0)';

  const sheetTransition =
    dragOffset > 0 ? 'none'
      : sheetPhase === 'exiting' ? `transform ${EXIT_DURATION}ms cubic-bezier(0.4, 0, 1, 1)`
        : `transform ${ENTER_DURATION}ms ${SPRING}`;

  // --- Content ---
  const describeContent = (
    <>
      <style>{`
        @keyframes descFadeIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes orbitSpin { to { transform: rotate(360deg); } }
        @keyframes orbitSpinReverse { to { transform: rotate(-360deg); } }
        @keyframes orbGlow { 0%, 100% { transform: scale(0.9); opacity: 0.5; } 50% { transform: scale(1.1); opacity: 1; } }
        @keyframes statusFadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes dotAppear { from { transform: scale(0); opacity: 0; } to { transform: scale(1); opacity: 1; } }
        @keyframes mobilePageEnter { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes mobileCardEnter { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: translateY(0); } }
      `}</style>

      {/* INPUT */}
      {phase === 'input' && (
        <div className="flex flex-col items-center px-1 pt-4" style={{ animation: 'descFadeIn 0.5s ease-out both' }}>
          <h2 className="mb-2 text-center text-[22px] font-bold text-white sm:text-[clamp(36px,4vw,56px)]">
            Describe what you want
          </h2>
          <p className="mb-6 text-center text-[14px] text-white/40 sm:mb-8 sm:text-[clamp(16px,1.6vw,20px)]">
            Tell us in your own words — Luma will find the perfect match
          </p>
          <div className="w-full max-w-[560px]">
            <textarea ref={textareaRef} value={query} onChange={(e) => setQuery(e.target.value.slice(0, 500))} onKeyDown={handleKeyDown}
              placeholder="e.g. A dark 90s thriller like Se7en with a twist ending..." rows={3}
              className={cn('w-full resize-none rounded-2xl border bg-white/5 px-5 py-4', 'text-[16px] text-white placeholder:text-white/25', 'outline-none backdrop-blur-md transition-all duration-200', 'focus:border-white/25 focus:bg-white/8 focus:ring-1 focus:ring-white/15', error ? 'border-red-500/50' : 'border-white/10')}
            />
            <div className="mt-1.5 flex items-center justify-between px-1">
              <span className="text-[12px] text-white/25">{query.length}/500</span>
              {error && <span className="text-[12px] text-red-400">{error}</span>}
            </div>
          </div>
          <div className="mt-5 flex max-w-[560px] flex-wrap justify-center gap-2 sm:mt-6">
            {EXAMPLE_PROMPTS.map((prompt) => (
              <button key={prompt} type="button" onClick={() => setQuery(prompt)}
                className={cn('rounded-full border border-white/8 bg-white/5 px-3.5 py-1.5', 'text-[12px] text-white/40 transition-all duration-200 sm:text-[13px]', 'hover:border-white/20 hover:bg-white/10 hover:text-white/60', 'active:scale-95')}>
                {prompt}
              </button>
            ))}
          </div>
          <button type="button" onClick={handleSearch} disabled={!query.trim()}
            className={cn('mt-8 rounded-full px-10 py-4 text-[17px] font-bold text-white sm:text-[20px]', 'transition-all duration-300 active:scale-95', query.trim() ? 'bg-white/15 border border-white/20 hover:bg-white/22 shadow-[0_0_30px_rgba(255,255,255,0.06)]' : 'bg-white/6 text-white/30 cursor-not-allowed border border-white/8')}>
            Find Something
          </button>
        </div>
      )}

      {/* SEARCHING */}
      {phase === 'searching' && (
        <div className="flex flex-col items-center justify-center gap-6" style={{ animation: 'descFadeIn 0.5s ease-out both', minHeight: isMobile ? 400 : 500 }}>
          <h2 className="text-[clamp(28px,4vw,44px)] font-bold tracking-wide text-white/80" style={{ animation: 'descFadeIn 0.6s ease-out both' }}>Luma</h2>
          <div className="relative flex items-center justify-center" style={{ width: 'clamp(120px,18vw,180px)', height: 'clamp(120px,18vw,180px)' }}>
            <div className="absolute inset-0 rounded-full border-[2px] border-dashed border-white/10" style={{ animation: 'orbitSpin 20s linear infinite' }} />
            <div className="absolute rounded-full border-[1.5px] border-dotted border-white/20" style={{ inset: '15%', animation: 'orbitSpinReverse 12s linear infinite' }} />
            <div className="absolute rounded-full border-[1px] border-dashed border-white/8" style={{ inset: '30%', animation: 'orbitSpin 8s linear infinite' }} />
            <div className="relative z-10 rounded-full" style={{ width: '28%', height: '28%', background: 'radial-gradient(circle, rgba(255,255,255,0.6) 0%, rgba(255,255,255,0.15) 50%, transparent 70%)', boxShadow: '0 0 40px 10px rgba(255,255,255,0.1)', animation: 'orbGlow 3s ease-in-out infinite' }} />
          </div>
          <p key={statusIndex} className="text-[clamp(16px,2.2vw,24px)] font-medium text-white/60" style={{ animation: 'statusFadeIn 0.4s ease-out both' }}>{STATUS_MESSAGES[statusIndex % STATUS_MESSAGES.length]}</p>
          <p className="max-w-[400px] truncate px-4 text-center text-[clamp(13px,1.5vw,16px)] italic text-white/25">&ldquo;{query.length > 80 ? query.slice(0, 80) + '\u2026' : query}&rdquo;</p>
          {dotCount > 0 && (
            <div className="flex items-center gap-1.5">
              {Array.from({ length: dotCount }, (_, i) => (<div key={i} className="h-[6px] w-[6px] rounded-full bg-white/40" style={{ animation: `dotAppear 0.3s ease-out ${i * 0.05}s both` }} />))}
            </div>
          )}
        </div>
      )}

      {/* RESULTS */}
      {phase === 'results' && (
        <>
          {/* Summary + search again */}
          <div className="flex items-center justify-between px-1 pt-2 pb-3">
            <span className="text-[13px] text-white/30">
              {recommendations.length} result{recommendations.length !== 1 ? 's' : ''}
            </span>
            <button type="button" onClick={handleSearchAgain}
              className="text-[13px] text-white/50 active:text-white/70">
              New search
            </button>
          </div>

          {recommendations.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-white/30">
              <p className="text-[14px]">No matches found</p>
              <button type="button" onClick={handleSearchAgain} className="mt-4 rounded-full bg-white/10 px-6 py-2.5 text-[14px] font-medium text-white/70 active:scale-95">Try Again</button>
            </div>
          ) : (
            <div className="divide-y divide-white/5">
              {recommendations.map((rec, idx) => {
                const colors = categoryColor(rec.aiCategory);
                return (
                  <button
                    key={`${rec.vibeItem.tmdbId ?? rec.vibeItem.ratingKey}-${idx}`}
                    type="button"
                    onClick={() => handleOpenModal(rec.vibeItem)}
                    className="flex w-full items-center gap-3 px-1 py-3 text-left active:bg-white/5"
                  >
                    <div className="h-[120px] w-[80px] shrink-0 overflow-hidden rounded-lg bg-white/5">
                      {rec.vibeItem.posterUrl ? (
                        <img src={rec.vibeItem.posterUrl} alt="" loading={idx < 6 ? 'eager' : 'lazy'} draggable={false} className="h-full w-full object-cover" />
                      ) : (
                        <div className="flex h-full w-full items-center justify-center text-white/20 text-[11px]">No Poster</div>
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[14px] font-medium text-white/90">
                        {rec.vibeItem.title}
                        {rec.vibeItem.year && <span className="ml-2 font-normal text-white/40">({rec.vibeItem.year})</span>}
                      </p>
                      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                        <span className={cn('rounded px-1.5 py-0.5 text-[11px] font-semibold',
                          rec.vibeItem.type === 'tv' ? 'bg-purple-500/20 text-purple-400' : 'bg-blue-500/20 text-blue-400')}>
                          {rec.vibeItem.type === 'tv' ? 'TV' : 'Movie'}
                        </span>
                        {rec.aiCategory && (
                          <span
                            role="button"
                            onClick={(e) => { e.stopPropagation(); handleCategorySearch(rec.aiCategory); }}
                            className={cn('rounded-full border px-2 py-0.5 text-[11px] font-medium active:scale-95 transition-transform cursor-pointer', colors.bg, colors.text, colors.border)}>
                            {rec.aiCategory}
                          </span>
                        )}
                        {rec.owned && (
                          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/12 px-2 py-0.5 text-[11px] font-medium text-emerald-400">
                            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                            In Library
                          </span>
                        )}
                      </div>
                      {rec.aiReason && (
                        <p className="mt-1 text-[13px] leading-[1.4] text-white/30 line-clamp-2">{rec.aiReason}</p>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </>
      )}
    </>
  );

  // ---------------------------------------------------------------------------
  // Mobile: sheet layout
  // ---------------------------------------------------------------------------

  if (isMobile) {
    return createPortal(
      <div
        style={{
          position: 'fixed', inset: 0, zIndex: 10001,
          display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
          backgroundColor: sheetPhase === 'visible' ? 'rgba(0,0,0,0.4)' : 'rgba(0,0,0,0)',
          transition: sheetPhase === 'exiting' ? `background-color ${EXIT_DURATION}ms ease` : `background-color ${ENTER_DURATION}ms ease`,
        }}
        onClick={(e) => { if (e.target === e.currentTarget && phase === 'input') handleDismiss(); }}
      >
        <div
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
          <div data-sheet-handle style={{ flexShrink: 0, display: 'flex', justifyContent: 'center', padding: '12px 0 8px', cursor: 'grab' }}>
            <div style={{ width: 40, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)' }} />
          </div>

          {/* Scrollable content */}
          <div data-scroll-container className="luma-scroll-y flex-1 px-4" style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}>
            {describeContent}
            <div className="shrink-0" style={{ height: 'env(safe-area-inset-bottom, 12px)' }} />
          </div>
        </div>
      </div>,
      document.body,
    );
  }

  // ---------------------------------------------------------------------------
  // Desktop / Tablet layout
  // ---------------------------------------------------------------------------

  return (
    <div data-scroll-container className="luma-scroll-y h-dvh bg-kiosk-black">
      <div className="mx-auto max-w-[1600px] pb-16 px-4 pt-16 sm:px-6 sm:pb-20 sm:pt-20 md:px-8">
        <div className="mb-6">
          <button type="button" onClick={() => phase === 'results' ? handleSearchAgain() : navigate('/')}
            className="mb-4 flex items-center gap-2 text-[14px] text-white/50 hover:text-white/70 transition-colors">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
            {phase === 'results' ? 'New search' : 'Back'}
          </button>
        </div>
        {describeContent}
      </div>
    </div>
  );
}
