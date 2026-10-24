/**
 * DescribeResultsCarousel — modal-based swipeable carousel for AI search results.
 *
 * Backdrop-forward design: full-bleed image with subtle top/bottom gradients,
 * centered clear logo, AI category top-left, AI writeup at the bottom.
 * Small "i" info button in the lower-left opens the full MovieDetailModal.
 */

import {
  useState,
  useEffect,
  useRef,
  useCallback,
} from 'react';
import { createPortal } from 'react-dom';
import { useSwipeCarousel } from '@/hooks/useSwipeCarousel';
import { useMovieDetail } from '@/hooks/useMovieDetail';
import { useTvDetail } from '@/hooks/useTvDetail';
import { UltraBlurOverlay } from '@/components/media/UltraBlurOverlay';
import type { UltraBlurColors } from '@/utils/modalParsers';
import type { VibeItem } from '@/types/vibe';

import type { Recommendation } from './DescribeMode';

interface Props {
  recommendations: Recommendation[];
  onClose: () => void;
  onOpenDetail: (item: VibeItem) => void;
  isMobile: boolean;
  onCategorySearch?: (category: string) => void;
}

type Phase = 'entering' | 'visible' | 'exiting' | 'gone';

const SLIDE_BG = '#0a0a0a';

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function DescribeResultsCarousel({
  recommendations,
  onClose,
  onOpenDetail,
  isMobile,
  onCategorySearch,
}: Props) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [phase, setPhase] = useState<Phase>('entering');
  const trackRef = useRef<HTMLDivElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  const total = recommendations.length;
  const singleResult = total <= 1;

  // Track which slides have been visited — mount progressively, never unmount
  const visitedRef = useRef(new Set([0]));
  const [, forceUpdate] = useState(0);

  useEffect(() => {
    let changed = false;
    if (!visitedRef.current.has(activeIndex)) {
      visitedRef.current.add(activeIndex);
      changed = true;
    }
    if (activeIndex + 1 < total && !visitedRef.current.has(activeIndex + 1)) {
      visitedRef.current.add(activeIndex + 1);
      changed = true;
    }
    if (changed) forceUpdate((n) => n + 1);
  }, [activeIndex, total]);

  // ---- Phase lifecycle ----
  useEffect(() => {
    const frame = requestAnimationFrame(() => setPhase('visible'));
    return () => cancelAnimationFrame(frame);
  }, []);

  const handleClose = useCallback(() => {
    setPhase('exiting');
    setTimeout(() => {
      setPhase('gone');
      onClose();
    }, 300);
  }, [onClose]);

  // Body scroll lock
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  // Keyboard navigation
  useEffect(() => {
    if (phase !== 'visible') return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose();
      if (e.key === 'ArrowRight' && activeIndex < total - 1)
        setActiveIndex((i) => i + 1);
      if (e.key === 'ArrowLeft' && activeIndex > 0)
        setActiveIndex((i) => i - 1);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [phase, activeIndex, total, handleClose]);

  // Swipe + mouse drag
  const { handlers } = useSwipeCarousel({
    containerRef: trackRef,
    totalSlides: total,
    activeIndex,
    onChangeIndex: setActiveIndex,
    onDismiss: handleClose,
    enabled: phase === 'visible',
  });

  if (phase === 'gone') return null;

  const overlayOpacity = phase === 'visible' ? 1 : 0;
  const cardScale = phase === 'entering' ? 'scale(0.96)' : phase === 'exiting' ? 'scale(0.96)' : 'scale(1)';
  const cardOpacity = phase === 'visible' ? 1 : 0;

  return createPortal(
    <div className="luma-fullscreen luma-bleed"
      data-carousel-overlay
      ref={overlayRef}
      onClick={(e) => { if (e.target === overlayRef.current) handleClose(); }}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 9999,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'transparent',
        opacity: overlayOpacity,
        transition: 'opacity 0.3s ease',
        padding: isMobile ? 0 : '20px',
      }}
    >
      {/* Modal card */}
      <div
        style={{
          position: 'relative',
          width: '100%',
          ...(isMobile ? { height: '100%' } : { maxWidth: '1500px', height: '90vh' }),
          borderRadius: isMobile ? 0 : '20px',
          overflow: 'hidden',
          backgroundColor: SLIDE_BG,
          boxShadow: isMobile ? 'none' : '0 32px 100px rgba(0,0,0,0.8)',
          transform: cardScale,
          opacity: cardOpacity,
          transition:
            phase === 'exiting'
              ? 'transform 0.3s ease, opacity 0.3s ease'
              : 'transform 0.5s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.5s cubic-bezier(0.16, 1, 0.3, 1)',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* Close X — top-left, no container */}
        <button
          type="button"
          onClick={handleClose}
          style={{
            position: 'absolute',
            top: isMobile ? '18px' : '28px',
            left: isMobile ? '18px' : '36px',
            zIndex: 20,
            background: 'none',
            border: 'none',
            padding: 0,
            color: '#fff',
            cursor: 'pointer',
            lineHeight: 1,
            opacity: 0.85,
            transition: 'opacity 0.2s ease',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.opacity = '1'; }}
          onMouseLeave={(e) => { e.currentTarget.style.opacity = '0.85'; }}
          aria-label="Close"
        >
          <svg width={isMobile ? '28' : '32'} height={isMobile ? '28' : '32'} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>

        {/* Swipeable track */}
        <div
          ref={trackRef}
          style={{
            display: 'flex',
            height: '100%',
            width: '100%',
            willChange: 'transform',
            cursor: singleResult ? 'default' : 'grab',
            userSelect: 'none',
            touchAction: 'none',
          }}
          {...handlers}
        >
          {recommendations.map((rec, idx) => (
            <div
              key={`${rec.owned ? 'o' : 'u'}-${rec.vibeItem.tmdbId ?? rec.vibeItem.ratingKey}`}
              style={{ flex: '0 0 100%', width: '100%', height: '100%' }}
            >
              {visitedRef.current.has(idx) ? (
                <CarouselSlide
                  rec={rec}
                  onOpenDetail={onOpenDetail}
                  isActive={idx === activeIndex}
                  isMobile={isMobile}
                  onCategorySearch={onCategorySearch}
                />
              ) : (
                <div style={{ width: '100%', height: '100%', backgroundColor: SLIDE_BG }} />
              )}
            </div>
          ))}
        </div>

        {/* Bottom indicators */}
        {!singleResult && (
          <div
            style={{
              position: 'absolute',
              bottom: 12,
              left: '50%',
              transform: 'translateX(-50%)',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 6,
              zIndex: 20,
            }}
          >
            <div style={{ display: 'flex', gap: 8 }}>
              {recommendations.map((_, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => setActiveIndex(i)}
                  style={{
                    width: i === activeIndex ? 10 : 7,
                    height: i === activeIndex ? 10 : 7,
                    borderRadius: '50%',
                    border: 'none',
                    padding: 0,
                    cursor: 'pointer',
                    backgroundColor: i === activeIndex ? 'rgba(255,255,255,0.9)' : 'rgba(255,255,255,0.25)',
                    transition: 'all 300ms ease',
                  }}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------------------
// CarouselSlide — dispatches to MovieSlide or TvSlide based on media type
// ---------------------------------------------------------------------------

interface SlideProps {
  rec: Recommendation;
  onOpenDetail: (item: VibeItem) => void;
  isActive: boolean;
  isMobile: boolean;
  onCategorySearch?: (category: string) => void;
}

function CarouselSlide(props: SlideProps) {
  if (props.rec.vibeItem.type === 'tv') return <TvCarouselSlide {...props} />;
  return <MovieCarouselSlide {...props} />;
}

// ---------------------------------------------------------------------------
// MovieCarouselSlide — uses useMovieDetail for movie items
// ---------------------------------------------------------------------------

function MovieCarouselSlide({ rec, onOpenDetail, isActive, isMobile, onCategorySearch }: SlideProps) {
  const vibeItem = rec.vibeItem;
  const ratingKey = vibeItem.ratingKey > 0 ? vibeItem.ratingKey : undefined;
  const tmdbId = vibeItem.tmdbId ?? undefined;
  const { detail, loading, error, ultraBlurColors } = useMovieDetail(ratingKey, tmdbId);

  return (
    <SlideLayout
      title={detail?.title ?? vibeItem.title}
      backdropUrl={detail?.backdropUrl ?? null}
      clearLogoUrl={detail?.clearLogoUrl ?? null}
      loading={loading}
      error={error}
      ultraBlurColors={ultraBlurColors}
      rec={rec}
      vibeItem={vibeItem}
      onOpenDetail={onOpenDetail}
      isActive={isActive}
      isMobile={isMobile}
      onCategorySearch={onCategorySearch}
    />
  );
}

// ---------------------------------------------------------------------------
// TvCarouselSlide — uses useTvDetail for TV show items
// ---------------------------------------------------------------------------

function TvCarouselSlide({ rec, onOpenDetail, isActive, isMobile, onCategorySearch }: SlideProps) {
  const vibeItem = rec.vibeItem;
  const ratingKey = vibeItem.ratingKey > 0 ? vibeItem.ratingKey : undefined;
  const tmdbId = vibeItem.tmdbId ?? undefined;
  const { show, loading, error, ultraBlurColors } = useTvDetail(ratingKey, tmdbId);

  return (
    <SlideLayout
      title={show?.title ?? vibeItem.title}
      backdropUrl={show?.backdropUrl ?? null}
      clearLogoUrl={show?.clearLogoUrl ?? null}
      loading={loading}
      error={error}
      ultraBlurColors={ultraBlurColors}
      rec={rec}
      vibeItem={vibeItem}
      onOpenDetail={onOpenDetail}
      isActive={isActive}
      isMobile={isMobile}
      onCategorySearch={onCategorySearch}
    />
  );
}

// ---------------------------------------------------------------------------
// SlideLayout — shared rendering for both movie and TV slides
// ---------------------------------------------------------------------------

function SlideLayout({
  title,
  backdropUrl,
  clearLogoUrl,
  loading,
  error,
  ultraBlurColors,
  rec,
  vibeItem,
  onOpenDetail,
  isActive,
  isMobile,
  onCategorySearch,
}: {
  title: string;
  backdropUrl: string | null;
  clearLogoUrl: string | null;
  loading: boolean;
  error: boolean;
  ultraBlurColors: UltraBlurColors | null;
  rec: Recommendation;
  vibeItem: VibeItem;
  onOpenDetail: (item: VibeItem) => void;
  isActive: boolean;
  isMobile: boolean;
  onCategorySearch?: (category: string) => void;
}) {
  const [backdropLoaded, setBackdropLoaded] = useState(false);
  const [logoFailed, setLogoFailed] = useState(false);

  const openDetail = useCallback(() => onOpenDetail(vibeItem), [onOpenDetail, vibeItem]);

  // ---- Loading state ----
  if (loading) {
    return (
      <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', backgroundColor: SLIDE_BG }}>
        <div
          style={{
            width: '48px',
            height: '48px',
            borderRadius: '50%',
            border: '2px solid rgba(255,255,255,0.15)',
            borderTopColor: 'rgba(255,255,255,0.7)',
            animation: 'webModalSpin 0.8s linear infinite',
          }}
        />
        <style>{`@keyframes webModalSpin { to { transform: rotate(360deg); } }`}</style>
      </div>
    );
  }

  // ---- Error state ----
  if (error) {
    return (
      <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, backgroundColor: SLIDE_BG }}>
        <p style={{ fontSize: 18, color: 'rgba(255,255,255,0.5)' }}>
          {vibeItem.title}
        </p>
        <p style={{ fontSize: 14, color: 'rgba(255,255,255,0.3)' }}>
          Could not load details
        </p>
      </div>
    );
  }

  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        position: 'relative',
        overflow: 'hidden',
        opacity: isActive ? 1 : 0.3,
        transition: 'opacity 300ms',
        backgroundColor: SLIDE_BG,
      }}
    >
      {/* UltraBlur blob overlay — matches MovieDetailModal */}
      {ultraBlurColors && <UltraBlurOverlay colors={ultraBlurColors} variant="web" />}

      {/* Backdrop with mask — fades to transparent so UltraBlur shows through */}
      <div
        data-modal-backdrop
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          zIndex: 0,
          overflow: 'hidden',
          WebkitMaskImage: 'linear-gradient(to bottom, black 45%, transparent 75%)',
          maskImage: 'linear-gradient(to bottom, black 45%, transparent 75%)',
        }}
      >
        {backdropUrl && (
          <img
            src={backdropUrl}
            alt=""
            style={{
              width: '100%',
              height: '100%',
              objectFit: 'cover',
              objectPosition: 'center 20%',
              opacity: backdropLoaded ? 1 : 0,
              transition: 'opacity 0.6s ease',
              filter: 'contrast(1.08) saturate(1.12)',
            }}
            onLoad={() => setBackdropLoaded(true)}
          />
        )}
      </div>

      {/* Top gradient — readability for category text + close button */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: isMobile ? '180px' : '240px',
          zIndex: 1,
          background: 'linear-gradient(to bottom, rgba(0,0,0,0.75) 0%, rgba(0,0,0,0.45) 40%, rgba(0,0,0,0.15) 70%, transparent 100%)',
          pointerEvents: 'none',
        }}
      />

      {/* AI Category — top center, clickable to search for more */}
      {rec.aiCategory && (
        <h2
          role={onCategorySearch ? 'button' : undefined}
          tabIndex={onCategorySearch ? 0 : undefined}
          onClick={onCategorySearch ? (e) => {
            e.stopPropagation();
            onCategorySearch(rec.aiCategory);
          } : undefined}
          onKeyDown={onCategorySearch ? (e) => {
            if (e.key === 'Enter') onCategorySearch(rec.aiCategory);
          } : undefined}
          style={{
            position: 'absolute',
            top: isMobile ? 22 : 34,
            left: 0,
            right: 0,
            zIndex: 5,
            margin: 0,
            fontSize: isMobile ? 20 : 28,
            fontWeight: 600,
            color: 'rgba(255,255,255,0.85)',
            textAlign: 'center',
            cursor: onCategorySearch ? 'pointer' : undefined,
            transition: 'opacity 0.2s ease',
          }}
          onMouseEnter={onCategorySearch ? (e) => { e.currentTarget.style.opacity = '1'; } : undefined}
          onMouseLeave={onCategorySearch ? (e) => { e.currentTarget.style.opacity = '0.85'; } : undefined}
        >
          {rec.aiCategory}
        </h2>
      )}

      {/* Centered clear logo / title — clickable to open detail modal */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          zIndex: 2,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          pointerEvents: 'none',
        }}
      >
        <div
          role="button"
          tabIndex={0}
          onClick={(e) => { e.stopPropagation(); openDetail(); }}
          onKeyDown={(e) => { if (e.key === 'Enter') openDetail(); }}
          style={{
            pointerEvents: 'auto',
            cursor: 'pointer',
            width: '100%',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            transition: 'transform 0.2s ease',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.transform = 'scale(1.03)'; }}
          onMouseLeave={(e) => { e.currentTarget.style.transform = 'scale(1)'; }}
        >
          {clearLogoUrl && !logoFailed ? (
            <img
              src={clearLogoUrl}
              alt={title}
              style={{
                width: '85%',
                maxWidth: isMobile ? '320px' : '800px',
                maxHeight: isMobile ? '160px' : '320px',
                objectFit: 'contain',
                display: 'block',
                filter: 'drop-shadow(0 4px 20px rgba(0,0,0,0.6))',
              }}
              onError={() => setLogoFailed(true)}
            />
          ) : (
            <h1
              style={{
                fontSize: isMobile ? '36px' : '52px',
                fontWeight: 700,
                color: '#fff',
                lineHeight: 1.2,
                margin: 0,
                textShadow: '0 2px 8px rgba(0,0,0,0.5)',
                textAlign: 'center',
                padding: '0 24px',
              }}
            >
              {title}
            </h1>
          )}
        </div>
      </div>

      {/* Bottom content — centered AI writeup */}
      {rec.aiReason && (
        <div
          style={{
            position: 'absolute',
            bottom: 0,
            left: 0,
            right: 0,
            zIndex: 10,
            padding: isMobile ? '30px 24px 80px' : '40px 60px 96px',
            display: 'flex',
            justifyContent: 'center',
            background: ultraBlurColors
              ? 'linear-gradient(to bottom, transparent, rgba(0,0,0,0.35) 40%, rgba(0,0,0,0.55))'
              : 'linear-gradient(to bottom, transparent, rgba(0,0,0,0.55) 35%, rgba(0,0,0,0.75))',
          }}
        >
          <p
            style={{
              fontSize: isMobile ? 20 : 30,
              lineHeight: 1.45,
              color: 'rgba(255,255,255,0.92)',
              fontStyle: 'italic',
              margin: 0,
              maxWidth: 1000,
              textAlign: 'center',
            }}
          >
            &ldquo;{rec.aiReason}&rdquo;
          </p>
        </div>
      )}
    </div>
  );
}
