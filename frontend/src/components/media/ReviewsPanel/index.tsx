import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { cn } from '@/utils/cn';
import { useUiDensity } from '@/hooks/useUiDensity';
import { useReviews } from '@/hooks/useReviews';
import { RATING_ICONS, RT_FRESH_THRESHOLD } from '@/utils/ratingIcons';
import type {
  ReviewTab,
  SortMode,
  CriticReview,
  AudienceReview,
} from '@/types/reviews';
import { SortRow } from './SortRow';
import { ReviewCard } from './ReviewCard';

/** Stable empty arrays — avoids busting useMemo deps when data is null */
const EMPTY_CRITICS: CriticReview[] = [];
const EMPTY_AUDIENCE: AudienceReview[] = [];

/** Stable key for a review card — uses quote prefix to disambiguate duplicate names */
function reviewKey(review: CriticReview | AudienceReview, tab: ReviewTab): string {
  const q = (review.quote ?? '').slice(0, 40);
  if (tab === 'critics') {
    const cr = review as CriticReview;
    return `c-${cr.critic}-${cr.publication ?? ''}-${q}`;
  }
  const ar = review as AudienceReview;
  return `a-${ar.user}-${q}`;
}

const DISMISS_THRESHOLD = 120;

// ---------------------------------------------------------------------------
// Props contract (MUST NOT CHANGE)
// ---------------------------------------------------------------------------

interface ReviewsPanelProps {
  title: string;
  year: number | null;
  mediaType?: 'movie' | 'tv';
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Sort logic (pure functions)
// ---------------------------------------------------------------------------

function sortCritics(reviews: CriticReview[], mode: SortMode): CriticReview[] {
  const arr = [...reviews];
  switch (mode) {
    case 'top':
      return arr.sort((a, b) => {
        if (a.is_top_critic !== b.is_top_critic) return a.is_top_critic ? -1 : 1;
        if (a.fresh !== b.fresh) return a.fresh ? -1 : 1;
        return 0;
      });
    case 'newest':
      return arr.sort((a, b) => {
        if (!a.date && !b.date) return 0;
        if (!a.date) return 1;
        if (!b.date) return -1;
        return b.date.localeCompare(a.date);
      });
    case 'positive':
      return arr.sort((a, b) => {
        if (a.fresh !== b.fresh) return a.fresh ? -1 : 1;
        if (a.is_top_critic !== b.is_top_critic) return a.is_top_critic ? -1 : 1;
        return 0;
      });
    case 'critical':
      return arr.sort((a, b) => {
        if (a.fresh !== b.fresh) return a.fresh ? 1 : -1;
        if (a.is_top_critic !== b.is_top_critic) return a.is_top_critic ? -1 : 1;
        return 0;
      });
    default:
      return arr;
  }
}

function sortAudience(
  reviews: AudienceReview[],
  mode: SortMode
): AudienceReview[] {
  const arr = [...reviews];
  switch (mode) {
    case 'top':
      return arr.sort((a, b) => {
        if (a.is_super_reviewer !== b.is_super_reviewer)
          return a.is_super_reviewer ? -1 : 1;
        const ra = a.rating ?? -1;
        const rb = b.rating ?? -1;
        return rb - ra;
      });
    case 'newest':
      return arr.sort((a, b) => {
        if (!a.date && !b.date) return 0;
        if (!a.date) return 1;
        if (!b.date) return -1;
        return b.date.localeCompare(a.date);
      });
    case 'positive':
      return arr.sort((a, b) => {
        const ra = a.rating ?? -1;
        const rb = b.rating ?? -1;
        return rb - ra;
      });
    case 'critical':
      return arr.sort((a, b) => {
        const ra = a.rating ?? Number.MAX_SAFE_INTEGER;
        const rb = b.rating ?? Number.MAX_SAFE_INTEGER;
        return ra - rb;
      });
    default:
      return arr;
  }
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ReviewsPanel({ title, year, mediaType = 'movie', onClose }: ReviewsPanelProps) {
  const d = useUiDensity();
  const isDesktopWeb = d.mode === 'expanded' && !d.isTablet && !d.isTvMode;
  const { data, loading, error } = useReviews(title, year, mediaType);

  const [activeTab, setActiveTab] = useState<ReviewTab>('critics');
  const [sortMode, setSortMode] = useState<SortMode>('top');
  const [expandedCards, setExpandedCards] = useState<Set<string>>(
    () => new Set()
  );

  // Reset sort to 'top' when switching tabs; clear expanded cards
  const handleTabChange = useCallback((tab: ReviewTab) => {
    setActiveTab(tab);
    setSortMode('top');
    setExpandedCards(new Set());
  }, []);

  // Toggle individual card expansion — keyed by content identity, not position
  const handleToggleExpand = useCallback((cardKey: string) => {
    setExpandedCards((prev) => {
      const next = new Set(prev);
      if (next.has(cardKey)) {
        next.delete(cardKey);
      } else {
        next.add(cardKey);
      }
      return next;
    });
  }, []);

  // -------------------------------------------------------------------------
  // Mobile bottom-sheet state
  // -------------------------------------------------------------------------
  const isMobile = d.mode === 'compact';
  const sheetRef = useRef<HTMLDivElement>(null);
  const reviewsScrollRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });
  const [dragOffset, setDragOffset] = useState(0);
  const [dismissing, setDismissing] = useState(false);
  const [entered, setEntered] = useState(false);

  useEffect(() => {
    if (isMobile) {
      requestAnimationFrame(() => setEntered(true));
    }
  }, [isMobile]);

  const handleDismiss = useCallback(() => {
    setDismissing(true);
    setTimeout(onClose, 250);
  }, [onClose]);

  const onSheetTouchStart = useCallback((e: React.TouchEvent) => {
    const target = e.target as HTMLElement;
    const isHandle = target.closest('[data-sheet-handle]');
    const scrollEl = reviewsScrollRef.current;
    const isAtTop = !scrollEl || scrollEl.scrollTop <= 0;
    if ((isHandle || isAtTop) && e.touches.length > 0) {
      dragRef.current = { active: true, startY: e.touches[0]!.clientY, currentY: e.touches[0]!.clientY };
    }
  }, []);

  const onSheetTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragRef.current.active || e.touches.length === 0) return;
    const y = e.touches[0]!.clientY;
    const dy = y - dragRef.current.startY;
    if (dy > 0) {
      dragRef.current.currentY = y;
      setDragOffset(dy);
    }
  }, []);

  const onSheetTouchEnd = useCallback(() => {
    if (!dragRef.current.active) return;
    const dy = dragRef.current.currentY - dragRef.current.startY;
    dragRef.current.active = false;
    if (dy > DISMISS_THRESHOLD) {
      handleDismiss();
    } else {
      setDragOffset(0);
    }
  }, [handleDismiss]);

  // ESC key handler — stopPropagation to prevent parent modal from closing
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation();
        if (isMobile) {
          handleDismiss();
        } else {
          onClose();
        }
      }
    }
    window.addEventListener('keydown', handleKey, true);
    return () => window.removeEventListener('keydown', handleKey, true);
  }, [onClose, isMobile, handleDismiss]);

  // Derived data — use stable empty arrays to avoid busting useMemo deps
  const critics = data?.critic_reviews ?? EMPTY_CRITICS;
  const audience = data?.audience_reviews ?? EMPTY_AUDIENCE;
  const criticCount = critics.length;
  const audienceCount = audience.length;
  const hasAny = criticCount > 0 || audienceCount > 0;
  const rtUrl = data?.reviews_url ?? data?.url ?? null;

  // Auto-select tab if only one type has reviews
  useEffect(() => {
    if (!data) return;
    if (criticCount === 0 && audienceCount > 0) {
      setActiveTab('audience');
    } else if (criticCount > 0 && audienceCount === 0) {
      setActiveTab('critics');
    }
  }, [data, criticCount, audienceCount]);

  // Sort reviews
  const sortedReviews = useMemo(() => {
    if (activeTab === 'critics') {
      return sortCritics(critics, sortMode);
    }
    return sortAudience(audience, sortMode);
  }, [activeTab, sortMode, critics, audience]);

  // Check if any reviews in current tab have date data
  const hasDateData = useMemo(() => {
    const reviews = activeTab === 'critics' ? critics : audience;
    return reviews.some((r) => r.date != null);
  }, [activeTab, critics, audience]);

  // -------------------------------------------------------------------------
  // Mobile tab pills (shared between bottom sheet content)
  // -------------------------------------------------------------------------
  const mobileTabPills = useMemo(() => {
    const cScore = data?.critic_score ?? null;
    const aScore = data?.audience_score ?? null;
    const cIcon = cScore != null && cScore < RT_FRESH_THRESHOLD ? RATING_ICONS.rtCriticRotten : RATING_ICONS.rtCriticFresh;
    const aIcon = aScore != null && aScore < RT_FRESH_THRESHOLD ? RATING_ICONS.rtAudienceBad : RATING_ICONS.rtAudienceGood;
    const activeBg = 'rgb(44, 44, 46)';
    const activeBorder = '1px solid rgba(255,255,255,0.25)';
    const inactiveBg = 'rgb(28, 28, 30)';
    const inactiveBorder = '1px solid rgb(38, 38, 40)';
    return { cScore, aScore, cIcon, aIcon, activeBg, activeBorder, inactiveBg, inactiveBorder };
  }, [data]);

  const hasContent = !loading && !error && hasAny;

  // =========================================================================
  // MOBILE: Bottom sheet
  // =========================================================================
  if (isMobile) {
    const sheetTranslate = dismissing ? '100%' : entered ? `${dragOffset}px` : '100%';

    return (
      <div
        data-reviews-panel
        role="dialog"
        aria-modal="true"
        aria-label="Reviews"
        style={{
          position: 'fixed',
          inset: 0,
          zIndex: 200,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'flex-end',
          background: entered && !dismissing ? 'rgba(0,0,0,0.4)' : 'transparent',
          transition: 'background 0.25s ease',
        }}
        onClick={(e) => {
          if (e.target === e.currentTarget) handleDismiss();
        }}
      >
        <div
          ref={sheetRef}
          onTouchStart={onSheetTouchStart}
          onTouchMove={onSheetTouchMove}
          onTouchEnd={onSheetTouchEnd}
          style={{
            height: hasContent ? 'calc(100% - 60px)' : 'auto',
            maxHeight: 'calc(100% - 60px)',
            display: 'flex',
            flexDirection: 'column',
            background: 'var(--color-kiosk-black)',
            borderRadius: '20px 20px 0 0',
            overflow: 'hidden',
            transform: `translateY(${sheetTranslate})`,
            transition: dragOffset > 0 ? 'none' : 'transform 0.3s cubic-bezier(0.32, 0.72, 0, 1)',
            willChange: 'transform',
          }}
        >
          {/* Grab handle */}
          <div
            data-sheet-handle
            style={{
              flexShrink: 0,
              display: 'flex',
              justifyContent: 'center',
              padding: '12px 0 8px',
              cursor: 'grab',
            }}
          >
            <div style={{
              width: '40px',
              height: '5px',
              borderRadius: '3px',
              background: 'rgba(255,255,255,0.3)',
            }} />
          </div>

          {/* Loading */}
          {loading && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '24px', padding: '60px 0 80px' }}>
              <div style={{
                width: '36px', height: '36px',
                border: '3px solid rgba(255,255,255,0.15)', borderTopColor: 'rgba(255,255,255,0.7)',
                borderRadius: '50%', animation: 'reviewsSpin 0.8s linear infinite',
              }} />
              <span style={{ fontSize: '14px', color: 'rgba(255,255,255,0.5)' }}>Loading reviews...</span>
              <style>{`@keyframes reviewsSpin { to { transform: rotate(360deg) } }`}</style>
            </div>
          )}

          {/* Error */}
          {!loading && error && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '8px', padding: '60px 0 80px' }}>
              <span style={{ fontSize: '15px', fontWeight: 500, color: 'rgba(255,255,255,0.5)' }}>Failed to load reviews</span>
              <span style={{ fontSize: '13px', color: 'rgba(255,255,255,0.3)' }}>Check your connection and try again</span>
            </div>
          )}

          {/* Empty */}
          {!loading && !error && !hasAny && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '8px', padding: '60px 0 80px' }}>
              <span style={{ fontSize: '15px', fontWeight: 500, color: 'rgba(255,255,255,0.5)' }}>No reviews available</span>
              <span style={{ fontSize: '13px', color: 'rgba(255,255,255,0.3)' }}>Reviews may appear once they are published</span>
            </div>
          )}

          {/* Content */}
          {hasContent && (
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
              {/* RT icon toggle pills + sort */}
              <div className="shrink-0 px-4 pb-4">
                <div className="mx-auto max-w-3xl flex flex-col items-center gap-3">
                  <div style={{ display: 'flex', gap: '10px' }} role="tablist" aria-label="Review type">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={activeTab === 'critics'}
                      onClick={() => handleTabChange('critics')}
                      style={{
                        display: 'flex', alignItems: 'center', gap: '8px',
                        padding: '10px 20px', borderRadius: '9999px',
                        background: activeTab === 'critics' ? mobileTabPills.activeBg : mobileTabPills.inactiveBg,
                        border: activeTab === 'critics' ? mobileTabPills.activeBorder : mobileTabPills.inactiveBorder,
                        cursor: 'pointer', transition: 'all 0.2s ease',
                      }}
                    >
                      <img src={mobileTabPills.cIcon} alt="" style={{ width: '28px', height: '28px', objectFit: 'contain' }} draggable={false} />
                      {mobileTabPills.cScore != null && (
                        <span style={{ fontSize: '20px', fontWeight: 700, color: '#fff', lineHeight: 1 }}>
                          {mobileTabPills.cScore}<span style={{ fontSize: '14px', fontWeight: 400, color: 'rgba(255,255,255,0.5)' }}>%</span>
                        </span>
                      )}
                      <span style={{ fontSize: '16px', fontWeight: 600, color: activeTab === 'critics' ? '#fff' : 'rgba(255,255,255,0.5)' }}>
                        Critics
                      </span>
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={activeTab === 'audience'}
                      onClick={() => handleTabChange('audience')}
                      style={{
                        display: 'flex', alignItems: 'center', gap: '8px',
                        padding: '10px 20px', borderRadius: '9999px',
                        background: activeTab === 'audience' ? mobileTabPills.activeBg : mobileTabPills.inactiveBg,
                        border: activeTab === 'audience' ? mobileTabPills.activeBorder : mobileTabPills.inactiveBorder,
                        cursor: 'pointer', transition: 'all 0.2s ease',
                      }}
                    >
                      <img src={mobileTabPills.aIcon} alt="" style={{ width: '28px', height: '28px', objectFit: 'contain' }} draggable={false} />
                      {mobileTabPills.aScore != null && (
                        <span style={{ fontSize: '20px', fontWeight: 700, color: '#fff', lineHeight: 1 }}>
                          {mobileTabPills.aScore}<span style={{ fontSize: '14px', fontWeight: 400, color: 'rgba(255,255,255,0.5)' }}>%</span>
                        </span>
                      )}
                      <span style={{ fontSize: '16px', fontWeight: 600, color: activeTab === 'audience' ? '#fff' : 'rgba(255,255,255,0.5)' }}>
                        Audience
                      </span>
                    </button>
                  </div>
                  <SortRow
                    sortMode={sortMode}
                    onSortChange={setSortMode}
                    activeTab={activeTab}
                    hasDateData={hasDateData}
                  />
                </div>
              </div>

              {/* Scrollable review cards */}
              <div
                ref={reviewsScrollRef}
                data-reviews-scrollable
                data-scroll-container
                className="luma-scroll-y flex-1 overscroll-contain px-4 pb-10 safe-bottom"
                style={{ touchAction: 'pan-y', WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain' }}
              >
                <div className="mx-auto max-w-3xl">
                  <div className="flex flex-col gap-3">
                    {sortedReviews.map((review, i) => {
                      const ck = reviewKey(review, activeTab);
                      return (
                        <ReviewCard
                          key={ck}
                          review={review}
                          activeTab={activeTab}
                          index={i}
                          cardKey={ck}
                          expanded={expandedCards.has(ck)}
                          onToggleExpand={handleToggleExpand}
                        />
                      );
                    })}
                    {rtUrl && <RTLink url={rtUrl} />}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  // =========================================================================
  // TABLET / WEB: Full-screen overlay (unchanged)
  // =========================================================================
  return (
    <div
      className="fixed inset-0 z-[200] flex flex-col bg-black/95"
      data-reviews-panel
      role="dialog"
      aria-modal="true"
      aria-label="Reviews"
      style={{ touchAction: 'none' }}
      onTouchMove={(e) => {
        const target = e.target as HTMLElement;
        if (!target.closest('[data-reviews-scrollable]')) {
          e.preventDefault();
        }
      }}
    >
      {/* Tablet/Web: match the modal close button — 72px circle, top-left 20px */}
      <button
        type="button"
        onClick={onClose}
        aria-label="Close reviews"
        style={{
          position: 'absolute',
          top: '20px',
          left: '20px',
          zIndex: 10,
          width: '72px',
          height: '72px',
          borderRadius: '50%',
          border: '1px solid rgba(255, 255, 255, 0.15)',
          background: d.isTablet ? 'rgba(20, 20, 20, 0.85)' : 'rgba(20, 20, 20, 0.7)',
          backdropFilter: d.isTablet ? 'none' : 'blur(20px) saturate(1.8)',
          WebkitBackdropFilter: d.isTablet ? 'none' : 'blur(20px) saturate(1.8)',
          color: 'rgba(255, 255, 255, 0.9)',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          transition: 'background 0.2s ease',
          boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
        }}
      >
        <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="15 18 9 12 15 6" />
        </svg>
      </button>

      {/* Loading state */}
      {loading && (
        <div className="flex flex-1 flex-col items-center justify-center gap-4 text-white/50">
          <div className="h-9 w-9 animate-spin rounded-full border-[3px] border-white/15 border-t-white/70" />
          <p className="text-sm">Loading reviews...</p>
        </div>
      )}

      {/* Error state */}
      {!loading && error && (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 text-center text-white/50">
          <p className="text-base font-medium">Failed to load reviews</p>
          <p className="text-sm text-white/40">
            Check your connection and try again
          </p>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && !hasAny && (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 text-center text-white/50">
          <p className="text-base font-medium">No reviews available</p>
          <p className="text-sm text-white/40">
            Reviews may appear once they are published
          </p>
        </div>
      )}

      {/* Content: reviews loaded with data */}
      {hasContent && (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {/* Tablet/Web: icon+score pills (matches mobile pattern, sized up) */}
          <div className="shrink-0 px-6 pb-4 pt-24 lg:px-8">
            <div className="mx-auto max-w-3xl lg:max-w-5xl flex flex-col items-center gap-4">
              {(() => {
                const cScore = data?.critic_score ?? null;
                const aScore = data?.audience_score ?? null;
                const cIcon = cScore != null && cScore < RT_FRESH_THRESHOLD ? RATING_ICONS.rtCriticRotten : RATING_ICONS.rtCriticFresh;
                const aIcon = aScore != null && aScore < RT_FRESH_THRESHOLD ? RATING_ICONS.rtAudienceBad : RATING_ICONS.rtAudienceGood;
                const activeBg = 'rgba(255,255,255,0.15)';
                const activeBorder = '1px solid rgba(255,255,255,0.25)';
                const inactiveBg = 'rgba(255,255,255,0.06)';
                const inactiveBorder = '1px solid transparent';
                return (
                  <div style={{ display: 'flex', gap: '14px' }} role="tablist" aria-label="Review type">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={activeTab === 'critics'}
                      onClick={() => handleTabChange('critics')}
                      style={{
                        display: 'flex', alignItems: 'center', gap: '10px',
                        padding: '14px 28px', borderRadius: '9999px',
                        background: activeTab === 'critics' ? activeBg : inactiveBg,
                        border: activeTab === 'critics' ? activeBorder : inactiveBorder,
                        cursor: 'pointer', transition: 'all 0.2s ease',
                      }}
                    >
                      <img src={cIcon} alt="" style={{ width: '36px', height: '36px', objectFit: 'contain' }} draggable={false} />
                      {cScore != null && (
                        <span style={{ fontSize: '26px', fontWeight: 700, color: '#fff', lineHeight: 1 }}>
                          {cScore}<span style={{ fontSize: '16px', fontWeight: 400, color: 'rgba(255,255,255,0.5)' }}>%</span>
                        </span>
                      )}
                      <span style={{ fontSize: '20px', fontWeight: 600, color: activeTab === 'critics' ? '#fff' : 'rgba(255,255,255,0.5)' }}>
                        Critics
                      </span>
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={activeTab === 'audience'}
                      onClick={() => handleTabChange('audience')}
                      style={{
                        display: 'flex', alignItems: 'center', gap: '10px',
                        padding: '14px 28px', borderRadius: '9999px',
                        background: activeTab === 'audience' ? activeBg : inactiveBg,
                        border: activeTab === 'audience' ? activeBorder : inactiveBorder,
                        cursor: 'pointer', transition: 'all 0.2s ease',
                      }}
                    >
                      <img src={aIcon} alt="" style={{ width: '36px', height: '36px', objectFit: 'contain' }} draggable={false} />
                      {aScore != null && (
                        <span style={{ fontSize: '26px', fontWeight: 700, color: '#fff', lineHeight: 1 }}>
                          {aScore}<span style={{ fontSize: '16px', fontWeight: 400, color: 'rgba(255,255,255,0.5)' }}>%</span>
                        </span>
                      )}
                      <span style={{ fontSize: '20px', fontWeight: 600, color: activeTab === 'audience' ? '#fff' : 'rgba(255,255,255,0.5)' }}>
                        Audience
                      </span>
                    </button>
                  </div>
                );
              })()}
              {/* Sort pills centered below */}
              <SortRow
                sortMode={sortMode}
                onSortChange={setSortMode}
                activeTab={activeTab}
                hasDateData={hasDateData}
                large
              />
            </div>
          </div>

          {/* Scrollable content area */}
          <div
            data-scroll-container
            className="luma-scroll-y flex-1 overscroll-contain px-4 pb-10 safe-bottom md:px-6 lg:px-8"
            data-reviews-scrollable
            style={{ touchAction: 'pan-y', WebkitOverflowScrolling: 'touch' }}
          >
            <div className="mx-auto max-w-3xl lg:max-w-5xl">
              <div className={cn(
                'flex flex-col gap-3',
                isDesktopWeb && 'grid grid-cols-2 gap-4'
              )}>
                {sortedReviews.map((review, i) => {
                  const ck = reviewKey(review, activeTab);
                  return (
                    <ReviewCard
                      key={ck}
                      review={review}
                      activeTab={activeTab}
                      index={i}
                      cardKey={ck}
                      expanded={expandedCards.has(ck)}
                      onToggleExpand={handleToggleExpand}
                    />
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Internal RT link component
// ---------------------------------------------------------------------------

function RTLink({ url }: { url: string }) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        'flex min-h-[44px] items-center justify-center gap-2 rounded-2xl bg-[#1c1c1e] border border-[#2c2c2e]',
        'px-5 py-3 text-sm font-semibold text-white/80 transition-all',
        'hover:bg-[#2c2c2e] hover:text-white',
        'active:scale-95 active:bg-[#3a3a3c]',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/25'
      )}
    >
      Read on Rotten Tomatoes
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="shrink-0"
      >
        <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6" />
        <polyline points="15 3 21 3 21 9" />
        <line x1="10" y1="14" x2="21" y2="3" />
      </svg>
    </a>
  );
}
