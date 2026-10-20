import { MovieSearchActions } from '@/components/movies/MovieSearchActions';
import { isMinimal } from '@/minimal';
import { WatchlistAddSheet } from '@/components/movies/WatchlistAddSheet';
import { sheetBase } from '@/utils/posterColors';
import { CastPhoto } from '@/components/media/CastPhoto';
import { REVEALED_EVENT } from '@/services/modalPrefetch';
import { useTitleReveal, logoInput, titleTextStyle } from '@/hooks/useTitleReveal';
import { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { formatRuntime } from '@/utils/image';
import {
  PERSON_FALLBACK_SVG,
  getCastImageUrl,
  type CastMember,
} from '@/utils/modalParsers';
import { SourcesPanel } from './SourcesPanel';
import { TrailerModal } from '@/components/media/TrailerModal';
import { MobilePlaybackSheet } from './MobilePlaybackSheet';
import { MobileUpgradeSheet } from './MobileUpgradeSheet';
import { UltraBlurOverlay } from '@/components/media/UltraBlurOverlay';
import { SimilarPanel } from './SimilarPanel';
import { CollectionsSheet } from '@/components/collections/CollectionsSheet';
import { useEffectiveRatings } from './effectiveRatings';
import { RatingsBar, RATINGS_SLOT_SM } from '@/components/media/RatingsBar';
import { buildRatingItems } from '@/utils/ratingIcons';
import { BackdropHero } from '@/components/media/BackdropHero';
import { useBackdropEffects } from '@/hooks/useBackdropEffects';
import { useSwipeToDismiss } from '@/hooks/useSwipeToDismiss';
import { useModalHost } from '@/contexts/ModalHostContext';
import type { MovieModalViewProps } from './types';
import { removeFromWatchlist } from '@/services/watchlistApi';
import { useAuth } from '@/contexts/AuthContext';
import { FixMatchSheet, type FixMatchResult } from './FixMatchSheet';
import { FileInfoSheet } from './FileInfoSheet';
import { TopReviewsRow, ReviewSheet, useTopReviews, type TopReview } from '@/components/media/TopReviews';

// ---------------------------------------------------------------------------
// ActionButton — circular icon button with label underneath
// ---------------------------------------------------------------------------

function ActionButton({
  icon,
  label,
  sublabel,
  onClick,
  circle = '50px',
  fontSize = '11px',
}: {
  icon: React.ReactNode;
  label: string;
  sublabel?: string | null;
  onClick: () => void;
  /** Circle diameter (CSS length) — shrinks when the row holds 6 buttons. */
  circle?: string;
  /** Label font size (CSS length). */
  fontSize?: string;
}) {
  return (
    <div
      onClick={onClick}
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: '6px',
        cursor: 'pointer',
        minWidth: 0,
      }}
    >
      <div
        style={{
          width: circle,
          height: circle,
          borderRadius: '50%',
          background: 'rgba(255, 255, 255, 0.18)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'rgba(255, 255, 255, 0.9)',
        }}
      >
        {icon}
      </div>
      <span
        style={{
          fontSize,
          color: 'rgba(255, 255, 255, 0.9)',
          fontWeight: 500,
          letterSpacing: fontSize === '11px' ? '0.02em' : 0,
          textAlign: 'center',
          lineHeight: 1.15,
          whiteSpace: 'nowrap',
        }}
      >
        {label}
        {sublabel ? (
          <span style={{ display: 'block', fontSize: '10px', color: 'rgba(255,255,255,0.55)', fontWeight: 500 }}>
            {sublabel}
          </span>
        ) : null}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CompactLayout — full-screen with UltraBlur color gradients (phone)
// ---------------------------------------------------------------------------

export function CompactLayout(props: MovieModalViewProps) {
  const {
    detail,
    ratings,
    ratingsLoading,
    trailerKey,
    ultraBlurColors,
    loading,
    error,
    phase,
    showSources,
    showTrailer,
    showPlayback,
    showSimilar,
    showCollection,
    onClose,
    setShowSources,
    setShowTrailer,
    setShowPlayback,
    setShowSimilar,
    setShowCollection,
    onPersonSearch,
    inWatchlist,
    grabToast,
    onSourcesGrabbed,
    onUpgradeAdded,
    onWatchlistRemoved,
  } = props;
  const titleState = useTitleReveal(logoInput(detail), detail?.title, detail?.backdropUrl || detail?.posterUrl);
  // Phone: the sheet stays off-screen until it can show fully composed
  // (details, backdrop, logo and colors, or the 600 ms cap), then slides in.
  const composed = !!error || (!loading && !!detail && titleState !== 'hold');
  const sheetBg = sheetBase(ultraBlurColors) ?? '#15102a';
  useEffect(() => {
    if (composed) window.dispatchEvent(new Event(REVEALED_EVENT));
  }, [composed]);
  // Ratings arrive after the details: if the sheet shows before them, keep
  // their row reserved (and never collapse it) so nothing below moves.
  // Phone parity with web: RT from the reviews endpoint when the ratings lack it (mergeRatings).
  const shownRatings = useEffectiveRatings(ratings, detail?.title, detail?.year);
  const hasRatings = !!shownRatings && buildRatingItems(shownRatings).length > 0;
  const [reserveRatings, setReserveRatings] = useState(false);
  useEffect(() => { setReserveRatings(false); }, [detail?.title]);
  useEffect(() => {
    if (composed && (ratingsLoading || hasRatings)) setReserveRatings(true);
  }, [composed, ratingsLoading, hasRatings]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const topScrimRef = useRef<HTMLDivElement>(null);
  const topTintRef = useRef<HTMLDivElement>(null);
  const topBackdropRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const [plotExpanded, setPlotExpanded] = useState(false);

  // Upgrade bottom sheet; Sources uses lifted showSources so back/swipe
  // closes the sheet only (not the whole movie modal -> home).
  const [showUpgrade, setShowUpgrade] = useState(false);
  // File Info sheet (ⓘ above the meta line)
  const [showFileInfo, setShowFileInfo] = useState(false);
  const { authenticated, user } = useAuth();
  const [fixMatchKey, setFixMatchKey] = useState<number | null>(null);
  const isOwner = !!user?.is_admin;
  const onMatchedProp = props.onMatched;
  const handleFixMatched = useCallback((r: FixMatchResult) => {
    setFixMatchKey(null);
    setShowFileInfo(false);
    onMatchedProp?.(r);
  }, [onMatchedProp]);
  // Ratings & Reviews row (Rotten Tomatoes critics → audience) + full-review sheet
  const topReviews = useTopReviews(detail?.title ?? null, detail?.year ?? null);
  const [openReview, setOpenReview] = useState<TopReview | null>(null);
  const [showRemoveConfirm, setShowRemoveConfirm] = useState(false);
  const [removingWatchlist, setRemovingWatchlist] = useState(false);
  const [showNativeAdd, setShowNativeAdd] = useState(false);   // v202: the native watchlist (test install)
  const [removeWatchlistError, setRemoveWatchlistError] = useState<string | null>(null);

  const collectionId = detail?.collectionId ?? null;
  const modalHost = useModalHost();
  const { registerBackHandler } = modalHost;

  // System/gesture back while Collection/Sources is open -> close nested sheet only.
  useEffect(() => {
    if (fixMatchKey != null) {
      registerBackHandler(() => {
        setFixMatchKey(null);
        return true;
      });
      return () => registerBackHandler(null);
    }
    if (openReview) {
      registerBackHandler(() => {
        setOpenReview(null);
        return true;
      });
      return () => registerBackHandler(null);
    }
    if (showFileInfo) {
      registerBackHandler(() => {
        setShowFileInfo(false);
        return true;
      });
      return () => registerBackHandler(null);
    }
    if (showCollection) {
      registerBackHandler(() => {
        setShowCollection(false);
        return true;
      });
      return () => registerBackHandler(null);
    }
    if (!showSources) {
      registerBackHandler(null);
      return;
    }
    registerBackHandler(() => {
      setShowSources(false);
      return true;
    });
    return () => registerBackHandler(null);
  }, [showSources, showCollection, showFileInfo, fixMatchKey, openReview, setShowSources, setShowCollection, registerBackHandler]);

  // Swipe-to-dismiss: drag down from top to close modal
  const { handlers: swipeHandlers } = useSwipeToDismiss({
    scrollRef,
    onDismiss: onClose,
    enabled: phase === 'visible' && !showSources && !showPlayback && !showUpgrade && !showSimilar && !showCollection && !showFileInfo && !openReview,
  });

  // Plex-like backdrop effects: crop/zoom header + UltraBlur color bleed
  const backdropEffects = useBackdropEffects(detail?.backdropUrl, ultraBlurColors);

  // -------------------------------------------------------------------------
  // Body scroll lock + safe-area background
  // -------------------------------------------------------------------------
  useEffect(() => {
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prevOverflow;
    };
  }, []);

  // Paint <html> background to match modal once visible — delayed to avoid
  // flashing a black screen before the modal card animates in.
  useEffect(() => {
    if (phase !== 'visible') return;
    const bg = '#15102a';
    const prev = document.documentElement.style.background;
    document.documentElement.style.background = bg;
    return () => {
      document.documentElement.style.background = prev;
    };
  }, [phase]);

  // Parallax scroll handler (backdrop: translate up + fade out)
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    let rafId = 0;
    const handleScroll = () => {
      cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        const sc = topScrimRef.current;
        if (sc) sc.style.opacity = String(Math.min(1, Math.max(0, (el.scrollTop - 24) / 96)));
        // Keep the top tint's color layer aligned with the content's own UltraBlur,
        // so the strip under ✕ / ⓘ shows exactly the movie color that's behind it.
        const tint = topTintRef.current;
        const ca = contentRef.current;
        if (tint && ca) {
          tint.style.height = `${ca.offsetHeight}px`;
          tint.style.transform = `translate3d(0, ${ca.offsetTop - el.scrollTop}px, 0)`;
        }
        const scrollY = el.scrollTop;
        const bdTransform = `translate3d(0, ${-Math.min(scrollY * 0.4, 250)}px, 0)`;
        // Fade out backdrop over the first 300px of scroll
        const bdOpacity = String(Math.max(0, 1 - scrollY / 300));
        for (const bd of [backdropRef.current, topBackdropRef.current]) {
          if (!bd) continue;
          bd.style.transform = bdTransform;
          bd.style.opacity = bdOpacity;
        }
      });
    };
    el.addEventListener('scroll', handleScroll, { passive: true });
    return () => {
      el.removeEventListener('scroll', handleScroll);
      cancelAnimationFrame(rafId);
    };
  }, []);



  // -------------------------------------------------------------------------
  // Shelf toggle handlers
  // -------------------------------------------------------------------------
  const handleUpgradeToggle = useCallback(() => {
    setShowUpgrade(true);
  }, []);

  const handleSourcesToggle = useCallback(() => {
    setShowSources(!showSources);
  }, [setShowSources, showSources]);


  // -------------------------------------------------------------------------
  // Derived values
  // -------------------------------------------------------------------------
  const runtimeDisplay = detail ? formatRuntime(detail.durationMs) : '';
  const genresDisplay = detail?.genres.slice(0, 2) ?? [];
  const directorsForDisplay = detail?.directors ?? [];
  const rawCast = detail?.cast.slice(0, 20) ?? [];
  const isOwned = detail?.isOwned ?? false;
  const similarTmdbId = detail?.tmdbId ?? props.resolvedTmdbId ?? null;
  // ⓘ File Info — Plex items only, signed-in users only (shows NAS paths)
  const canShowFileInfo = authenticated && isOwned && !!detail?.ratingKey;
  // Action row sizing — count must mirror the conditional buttons below.
  const actionCount =
    (trailerKey ? 1 : 0) + (isOwned ? 1 : 0) + (isOwner ? 1 : 0) /* Sources: admins only, grabs are admin-only */ +
    (similarTmdbId ? 1 : 0) + (collectionId != null ? 1 : 0);
  const denseActions = actionCount >= 6;
  const actionCircle = denseActions ? 'min(46px, 11.8vw)' : actionCount === 5 ? 'min(50px, 12.8vw)' : '50px';
  const actionFont = denseActions ? 'min(11px, 2.72vw)' : '11px';
  const fileInfoCopies = detail?.libraryKeys
    ? Object.entries(detail.libraryKeys)
        .map(([library, rk]) => ({ library, ratingKey: Number(rk) }))
        .filter((c) => Number.isFinite(c.ratingKey) && c.ratingKey > 0)
    : [];

  // Merge directors (as first entries) + cast into single horizontal scroll
  const allCastForDisplay: CastMember[] = [
    ...directorsForDisplay.map((d) => ({
      name: d.name,
      character: 'Director',
      role: 'Director',
      thumb: d.thumb ?? undefined,
      profilePath: d.profilePath ?? undefined,
      tmdbPersonId: d.tmdbPersonId,
    })),
    ...rawCast,
  ];

  // Build meta info items (joined with • separators)
  const metaItems: string[] = [];
  if (detail?.year) metaItems.push(String(detail.year));
  if (runtimeDisplay) metaItems.push(runtimeDisplay);
  if (detail?.contentRating) metaItems.push(detail.contentRating);
  if (genresDisplay.length > 0) metaItems.push(genresDisplay.join(', '));

  // -------------------------------------------------------------------------
  // Animation styles
  // -------------------------------------------------------------------------

  // Slide up from bottom on enter, slide back down on exit
  const ENTER_DURATION = 600;
  const SHEET_FADE_MS = 200;
  const EXIT_DURATION = 400;
  const springBezier = 'cubic-bezier(0.22, 1, 0.36, 1)';
  // Use translate3d to force GPU compositing for smooth animations
  const cardTransform =
    phase === 'exiting'
      ? 'translate3d(0, 100%, 0)'
      : phase === 'entering' || !composed
        ? 'translate3d(0, 16px, 0)'
        : 'translate3d(0, 0, 0)';
  const cardTransition =
    phase === 'exiting'
      ? `transform ${EXIT_DURATION}ms cubic-bezier(0.4, 0, 1, 1)`
      : `transform ${ENTER_DURATION}ms ${springBezier}, opacity ${SHEET_FADE_MS}ms ease-out`;
  // The sheet only ever shows composed: it fades in as one piece (no slide
  // that would uncover an empty sheet first), and nothing in it fades alone.
  const cardOpacity = phase === 'exiting' || (phase !== 'entering' && composed) ? 1 : 0;

  if (phase === 'gone') return null;

  return createPortal(
    <div
      data-modal="MovieDetailModal"
      data-variant="mobile"
      data-composed={composed ? "true" : "false"}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        zIndex: 10000,
        backgroundColor: 'rgba(0,0,0,0.6)',
        opacity: phase === 'entering' || !composed ? 0 : 1,
        transition: 'opacity 250ms ease-out',
      }}
    >
      {/* Modal card — rounded top, extends to bottom of wrapper */}
      <div
        style={{
          position: 'absolute',
          top: 'max(env(safe-area-inset-top, 0px), 6px)',
          left: 0,
          right: 0,
          bottom: 0,
          borderTopLeftRadius: '14px',
          borderTopRightRadius: '14px',
          overflow: 'hidden',
          backgroundColor: sheetBg,
          display: 'flex',
          flexDirection: 'column',
          transform: cardTransform,
          opacity: cardOpacity,
          transition: cardTransition,
          willChange: 'transform',
          backfaceVisibility: 'hidden',
        }}
      >
        {/* Scrollable content */}
        <div
          ref={scrollRef}
          onClick={() => { if (showSources) setShowSources(false); }}
          onTouchStart={swipeHandlers.onTouchStart}
          onTouchMove={swipeHandlers.onTouchMove}
          onTouchEnd={swipeHandlers.onTouchEnd}
          onTouchCancel={swipeHandlers.onTouchCancel}
          data-scroll-container
          className="luma-scroll-y"
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            overflowX: 'hidden',
            WebkitOverflowScrolling: 'touch',
            overscrollBehavior: 'contain',
            touchAction: 'pan-y',
            position: 'relative',
            zIndex: 1,
          }}
        >
          {/* Loading — the sheet now slides up before the data lands, so this
              placeholder is what the user sees for the first few hundred ms. */}
          {loading && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                height: '100%',
              }}
            >
              <div
                style={{
                  width: '40px',
                  height: '40px',
                  borderRadius: '50%',
                  border: '3px solid rgba(255,255,255,0.15)',
                  borderTopColor: 'rgba(255,255,255,0.7)',
                  animation: 'webModalSpin 0.8s linear infinite',
                }}
              />
            </div>
          )}

          {/* Error */}
          {!loading && error && (
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                height: '100%',
                gap: '16px',
              }}
            >
              <p style={{ fontSize: '16px', color: 'rgba(255,255,255,0.6)' }}>
                Couldn’t load details
              </p>
              <button
                type="button"
                onClick={onClose}
                style={{
                  padding: '10px 28px',
                  borderRadius: '8px',
                  border: 'none',
                  background: 'var(--color-accent)',
                  color: '#fff',
                  fontSize: '15px',
                  cursor: 'pointer',
                }}
              >
                Close
              </button>
            </div>
          )}

          {/* Content */}
          {!loading && !error && detail && (
            <>
              {/* Backdrop — Plex-like cover-cropped header via BackdropHero */}
              <BackdropHero
                backdropUrl={detail.backdropUrl}
                posterUrl={detail.posterUrl}
                effects={backdropEffects}
                phase={phase}
                containerRef={backdropRef}
                hold={titleState === 'hold'} instant
              />

              {/* === CENTERED CONTENT AREA === */}
              <div ref={contentRef} data-reveal={titleState !== 'hold' ? 'shown' : 'hold'} style={{
                position: 'relative',
                zIndex: 3,
                textAlign: 'center',
                marginTop: '-100px',
                // One reveal for everything (backdrop, blur, title, text, buttons).
                opacity: titleState === 'hold' ? 0 : 1,
                transition: 'none', // the sheet itself fades in, composed
                willChange: 'opacity',
              }}>
                {/* UltraBlur — scrolls with content, slides up over backdrop */}
                {ultraBlurColors && (
                  <div
                    style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      right: 0,
                      bottom: 0,
                      zIndex: 0,
                      pointerEvents: 'none',
                      opacity: 0.7,
                      WebkitMaskImage: 'linear-gradient(to bottom, transparent 0%, black 80px)',
                      maskImage: 'linear-gradient(to bottom, transparent 0%, black 80px)',
                    }}
                  >
                    <UltraBlurOverlay hold={titleState === 'hold'} instant colors={ultraBlurColors} variant="mobile" animationDelay={300} />
                  </div>
                )}

                {/* Clearlogo / Title */}
                <div
                  style={{
                    margin: '0 auto 24px',
                    maxWidth: '100%',
                    width: '100%',
                    height: '140px',
                    display: 'flex',
                    alignItems: 'flex-end',
                    justifyContent: 'center',
                    overflow: 'visible',
                  }}
                >
                  {titleState === 'logo' && detail.clearLogoUrl ? (
                    <img
                      src={detail.clearLogoUrl}
                      alt={detail.title}
                      style={{
                        maxWidth: 'calc(100% - 32px)',
                        maxHeight: '200px',
                        objectFit: 'contain',
                        filter: 'drop-shadow(0 2px 8px rgba(0,0,0,0.5))',
                      }}
                      onError={(e) => {
                        const img = e.target as HTMLImageElement;
                        img.style.display = 'none';
                        const fallback = img.nextElementSibling as HTMLElement | null;
                        if (fallback) fallback.style.display = 'block';
                      }}
                    />
                  ) : null}
                  <h1
                    style={{
                      ...titleTextStyle(titleState),
                      fontSize: '28px',
                      fontWeight: 700,
                      color: '#fff',
                      lineHeight: 1.2,
                      margin: 0,
                      textShadow: '0 2px 8px rgba(0,0,0,0.5)',
                    }}
                  >
                    {detail.title}
                  </h1>
                </div>

                {/* === Stage 3: Details — individually staggered cascade === */}
                <div style={{ position: 'relative', zIndex: 1 }}>

                {/* === Shelf overlay zone — meta/ratings/action covered by shelf, buttons float above === */}
                <div style={{ position: 'relative' }}>
                  {/* Content hidden when shelf is open */}
                  <div style={{
                    ...({
                          opacity: showSources ? 0 : 1,
                          pointerEvents: (showSources ? 'none' : 'auto') as 'none' | 'auto',
                          transition: 'opacity 0.3s ease',
                        }),
                  }}>
                  {/* Meta info line: year • runtime • content_rating • genres */}
                  {metaItems.length > 0 && (
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: '10px',
                        marginTop: '8px',
                        flexWrap: 'nowrap',
                        fontSize: '14px',
                        color: 'rgba(255, 255, 255, 0.9)',
                      }}
                    >
                      {metaItems.map((item, i) => (
                        <span
                          key={i}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '10px',
                          }}
                        >
                          {i > 0 && (
                            <span style={{ color: 'rgba(255, 255, 255, 0.3)' }}>
                              •
                            </span>
                          )}
                          <span>{item}</span>
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Ratings bar — centered icons */}
                  <div data-ratings-slot style={{ minHeight: reserveRatings ? `${RATINGS_SLOT_SM}px` : 0 }}>
                    {shownRatings && <RatingsBar ratings={shownRatings} size="sm" />}
                  </div>

                  {/* Primary action area */}
                  <div data-modal-actions>
                  {isOwned ? (
                    /* Watch button — full-width white pill */
                    <button
                      type="button"
                      data-modal-keep=""
                      data-owned-watch=""
                      onClick={() => setShowPlayback(true)}
                      style={{
                        width: 'calc(100% - 32px)',
                        margin: '20px 16px 0',
                        padding: '14px 20px',
                        background: '#ffffff',
                        color: '#000000',
                        borderRadius: '50px',
                        border: 'none',
                        fontSize: '16px',
                        fontWeight: 600,
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: '10px',
                      }}
                    >
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                        <polygon points="6,3 20,12 6,21" />
                      </svg>
                      Watch
                    </button>
                  ) : inWatchlist ? (
                    /* In watchlist — tap to remove (confirm sheet) */
                    <div data-modal-keep=""
                      style={{
                        display: 'flex',
                        justifyContent: 'center',
                        margin: '20px 16px 0',
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => {
                          setRemoveWatchlistError(null);
                          setShowRemoveConfirm(true);
                        }}
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '8px',
                          padding: '12px 28px',
                          borderRadius: '50px',
                          background: 'rgba(74,222,128,0.12)',
                          border: '1px solid rgba(74,222,128,0.3)',
                          fontSize: '16px',
                          fontWeight: 600,
                          color: '#4ade80',
                          cursor: 'pointer',
                        }}
                      >
                        <span
                          style={{
                            width: '8px',
                            height: '8px',
                            borderRadius: '50%',
                            background: '#4ade80',
                          }}
                        />
                        In watchlist
                      </button>
                    </div>
                  ) : (
                    /* Add to watchlist: full-width #3b82f6 primary, white text, sentence case
                       (v167, Designer: the same button as TV detail; was the white pill) */
                    <button
                      type="button"
                      data-testid="movie-add-watchlist"
                      data-modal-keep=""
                      onClick={() => (isMinimal() ? setShowNativeAdd(true) : handleUpgradeToggle())}
                      style={{
                        width: 'calc(100% - 32px)',
                        margin: '20px 16px 0',
                        padding: '14px 20px',
                        background: '#3b82f6',
                        color: '#ffffff',
                        borderRadius: '12px',
                        border: 'none',
                        fontSize: '16px',
                        fontWeight: 600,
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: '10px',
                      }}
                    >
                      <svg
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <line x1="12" y1="5" x2="12" y2="19" />
                        <line x1="5" y1="12" x2="19" y2="12" />
                      </svg>
                      Add to watchlist
                    </button>
                  )}
                  {isMinimal() && inWatchlist && props.resolvedTmdbId && detail ? (
                    <MovieSearchActions tmdbId={props.resolvedTmdbId} title={detail.title} year={detail.year} />
                  ) : null}
                  </div>{/* end action stagger wrapper */}
                  </div>{/* end hidden-when-shelf-open wrapper */}
                </div>{/* end shelf overlay zone */}

                {/* Action buttons row — outside shelf zone, always visible.
                    Equal-width grid columns, so every button always fits the
                    screen (no sideways scroll, nothing chopped at the edge).
                    With 5–6 buttons (Collection present) the circles and labels
                    scale down with the viewport (vw) instead of overflowing. */}
                <div
                  data-action-row
                  data-modal-actions
                  onClick={(e) => e.stopPropagation()}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: `repeat(${actionCount}, minmax(0, 1fr))`,
                    columnGap: denseActions ? '2px' : '8px',
                    padding: denseActions ? '20px 10px' : '20px 16px',
                    maxWidth: `${actionCount * 76 + 32}px`,
                    margin: '0 auto',
                    boxSizing: 'border-box',
                    overflow: 'hidden',
                  }}
                >
                  {/* Trailer */}
                  {trailerKey && (
                    <ActionButton
                      circle={actionCircle}
                      fontSize={actionFont}
                      icon={
                        <svg
                          width="18"
                          height="18"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18" />
                          <line x1="7" y1="2" x2="7" y2="22" />
                          <line x1="17" y1="2" x2="17" y2="22" />
                          <line x1="2" y1="12" x2="22" y2="12" />
                          <line x1="2" y1="7" x2="7" y2="7" />
                          <line x1="2" y1="17" x2="7" y2="17" />
                          <line x1="17" y1="17" x2="22" y2="17" />
                          <line x1="17" y1="7" x2="22" y2="7" />
                        </svg>
                      }
                      label="Trailer"
                      onClick={() => setShowTrailer(true)}
                    />
                  )}

                  {/* Upgrade (owned only) */}
                  {isOwned && (
                    <ActionButton
                      circle={actionCircle}
                      fontSize={actionFont}
                      icon={
                        <svg
                          width="18"
                          height="18"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <line x1="12" y1="19" x2="12" y2="5" />
                          <polyline points="5 12 12 5 19 12" />
                        </svg>
                      }
                      label="Upgrade"
                      onClick={handleUpgradeToggle}
                    />
                  )}

                  {/* Sources — admins only (grabs are admin-only), like TV */}
                  {isOwner && (
                  <span data-modal-sources="" style={{ display: 'contents' }}>
                  <ActionButton
                    circle={actionCircle}
                    fontSize={actionFont}
                    icon={
                      <svg
                        width="18"
                        height="18"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
                        <polyline points="7 10 12 15 17 10" />
                        <line x1="12" y1="15" x2="12" y2="3" />
                      </svg>
                    }
                    label="Sources"
                    onClick={handleSourcesToggle}
                  />
                  </span>
                  )}

                  {/* Similar — movies only; needs a TMDb id to ask for
                      recommendations, so it hides on the rare Plex item we
                      couldn't resolve one for. */}
                  {similarTmdbId && (
                    <ActionButton
                      circle={actionCircle}
                      fontSize={actionFont}
                      icon={
                        <svg
                          width="18"
                          height="18"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <polygon points="12 2 2 7 12 12 22 7 12 2" />
                          <polyline points="2 17 12 22 22 17" />
                          <polyline points="2 12 12 17 22 12" />
                        </svg>
                      }
                      label="Similar"
                      onClick={() => setShowSimilar(true)}
                    />
                  )}

                  {/* Collection — TMDb franchise when belongs_to_collection is set */}
                  {collectionId != null && (
                    <ActionButton
                      circle={actionCircle}
                      fontSize={actionFont}
                      icon={
                        <svg
                          width="18"
                          height="18"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <rect x="3" y="3" width="7" height="7" rx="1" />
                          <rect x="14" y="3" width="7" height="7" rx="1" />
                          <rect x="3" y="14" width="7" height="7" rx="1" />
                          <rect x="14" y="14" width="7" height="7" rx="1" />
                        </svg>
                      }
                      label="Collection"
                      onClick={() => setShowCollection(true)}
                    />
                  )}
                </div>

                {/* Plot summary */}
                {detail.summary && (
                  <div
                    style={{
                      padding: '0 16px',
                      marginBottom: '24px',
                      textAlign: 'left',
                    }}
                  >
                    <p
                      onClick={() => setPlotExpanded((v) => !v)}
                      style={{
                        fontSize: '16px',
                        lineHeight: 1.6,
                        color: 'rgba(255, 255, 255, 0.9)',
                        margin: 0,
                        cursor: 'pointer',
                        ...(!plotExpanded
                          ? {
                              display: '-webkit-box',
                              WebkitLineClamp: 3,
                              WebkitBoxOrient: 'vertical' as const,
                              overflow: 'hidden',
                            }
                          : {}),
                      }}
                    >
                      {detail.summary}
                    </p>
                    {!plotExpanded && detail.summary.length > 150 && (
                      <button
                        type="button"
                        onClick={() => setPlotExpanded(true)}
                        style={{
                          background: 'none',
                          border: 'none',
                          color: '#2196F3',
                          fontSize: '14px',
                          fontWeight: 600,
                          cursor: 'pointer',
                          padding: '8px 0',
                          marginTop: '8px',
                        }}
                      >
                        More
                      </button>
                    )}
                    {plotExpanded && (
                      <button
                        type="button"
                        onClick={() => setPlotExpanded(false)}
                        style={{
                          background: 'none',
                          border: 'none',
                          color: '#2196F3',
                          fontSize: '14px',
                          fontWeight: 600,
                          cursor: 'pointer',
                          padding: '8px 0',
                          marginTop: '8px',
                        }}
                      >
                        Less
                      </button>
                    )}
                  </div>
                )}

                {/* Ratings & Reviews — Rotten Tomatoes critic/audience reviews (hidden when none) */}
                <TopReviewsRow reviews={topReviews} variant="compact" onOpen={setOpenReview} />

                {/* Cast & Crew — horizontal scroll with circular photos */}
                {/* Directors appear first, then cast */}
                {allCastForDisplay.length > 0 && (
                  <div style={{ marginBottom: '20px', textAlign: 'left' }}>
                    <h2
                      style={{
                        fontSize: '18px',
                        fontWeight: 600,
                        color: '#fff',
                        textShadow: '0 1px 4px rgba(0,0,0,0.5)',
                        margin: '0 0 10px',
                        padding: '0 16px',
                      }}
                    >
                      Cast &amp; crew
                    </h2>
                    <div
                      style={{
                        display: 'flex',
                        gap: '20px',
                        padding: '10px 16px',
                        overflowX: 'auto',
                        WebkitOverflowScrolling: 'touch',
                        scrollbarWidth: 'none',
                      }}
                    >
                      {allCastForDisplay.map((member, idx) => {
                        const imgUrl = getCastImageUrl(member);
                        return (
                          <div
                            key={`cast-${member.name}-${idx}`}
                            onClick={() => {
                              if (member.tmdbPersonId) {
                                modalHost.openPerson({ personId: member.tmdbPersonId, personName: member.name });
                              } else {
                                onPersonSearch?.(member.name, member.tmdbPersonId);
                              }
                            }}
                            style={{
                              flexShrink: 0,
                              width: '90px',
                              textAlign: 'center',
                              cursor: 'pointer',
                            }}
                          >
                            <CastPhoto
                              src={imgUrl}
                              alt={member.name}
                              ready={composed}
                              fallback={PERSON_FALLBACK_SVG}
                              style={{
                                width: '90px',
                                height: '90px',
                                borderRadius: '50%',
                                objectFit: 'cover',
                                background: 'rgba(255,255,255,0.1)',
                                marginBottom: '8px',
                              }}
                            />
                            <p
                              style={{
                                fontSize: '13px',
                                fontWeight: 500,
                                color: 'rgba(255,255,255,0.9)',
                                margin: '0 0 2px',
                                lineHeight: 1.3,
                                wordWrap: 'break-word',
                              }}
                            >
                              {member.name}
                            </p>
                            {(member.character || member.role) && (
                              <p
                                style={{
                                  fontSize: '12px',
                                  color: 'rgba(255,255,255,0.7)',
                                  margin: 0,
                                  lineHeight: 1.3,
                                }}
                              >
                                {member.character ?? member.role}
                              </p>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Libraries — show which Plex libraries contain this movie */}
                {isOwned && detail?.libraries && detail.libraries.length > 0 && (
                  <div style={{ marginBottom: '20px', textAlign: 'left', padding: '0 16px' }}>
                    <h2
                      style={{
                        fontSize: '18px',
                        fontWeight: 600,
                        color: '#fff',
                        textShadow: '0 1px 4px rgba(0,0,0,0.5)',
                        margin: '0 0 10px',
                      }}
                    >
                      Available In
                    </h2>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      {detail.libraries.map((lib) => (
                        <div
                          key={lib}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '12px',
                            padding: '12px 16px',
                            borderRadius: '12px',
                            background: 'rgba(255, 255, 255, 0.06)',
                            border: '1px solid rgba(255, 255, 255, 0.08)',
                          }}
                        >
                          <div
                            style={{
                              width: '36px',
                              height: '36px',
                              borderRadius: '10px',
                              background: 'rgba(255, 255, 255, 0.08)',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              flexShrink: 0,
                            }}
                          >
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="rgba(255, 255, 255, 0.5)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                            </svg>
                          </div>
                          <span style={{ fontSize: '15px', fontWeight: 500, color: 'rgba(255, 255, 255, 0.9)' }}>
                            {lib}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Bottom spacing — content padding keeps last items above home indicator */}
                <div style={{ height: 'calc(env(safe-area-inset-bottom, 0px) + 40px)' }} />

                </div>{/* end stage 3 details wrapper */}
              </div>
            </>
          )}
        </div>

        {/* Top strip — fades in as the content scrolls so text never sits visibly under ✕ / ⓘ.
            It's a text-free replica of the sheet behind it: base color, the same backdrop art
            (same parallax/fade) and the movie's UltraBlur aligned with the content layer — so
            the top always shows the art or the movie's color, never a dark band. */}
        <div
          ref={topScrimRef}
          aria-hidden="true"
          data-top-scrim
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            height: '92px',
            zIndex: 10,
            pointerEvents: 'none',
            overflow: 'hidden',
            backgroundColor: sheetBg,
            WebkitMaskImage: 'linear-gradient(to bottom, black 0px, black 34px, rgba(0,0,0,0.6) 58px, transparent 92px)',
            maskImage: 'linear-gradient(to bottom, black 0px, black 34px, rgba(0,0,0,0.6) 58px, transparent 92px)',
            opacity: 0,
            willChange: 'opacity',
          }}
        >
          {/* Same backdrop art, mirrored from the scroll handler, so the strip shows the
              art right up to the rounded top while it's still behind it. */}
          {!loading && !error && detail && (
            <BackdropHero
              backdropUrl={detail.backdropUrl}
              posterUrl={detail.posterUrl}
              effects={backdropEffects}
              phase={phase}
              containerRef={topBackdropRef}
              hold={titleState === 'hold'} instant
            />
          )}
          {ultraBlurColors && (
            <div
              ref={topTintRef}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                right: 0,
                height: '100vh',
                opacity: 0.7,
                WebkitMaskImage: 'linear-gradient(to bottom, transparent 0%, black 80px)',
                maskImage: 'linear-gradient(to bottom, transparent 0%, black 80px)',
                willChange: 'transform',
              }}
            >
              <UltraBlurOverlay hold={titleState === 'hold'} instant colors={ultraBlurColors} variant="mobile" />
            </div>
          )}
        </div>

        {/* Close button — bare white X, fixed top-left */}
        <button
          type="button"
          onClick={onClose}
          style={{
            position: 'absolute',
            top: '12px',
            left: '16px',
            zIndex: 20,
            width: '40px',
            height: '40px',
            borderRadius: '50%',
            background: 'rgba(0,0,0,0.3)',
            backdropFilter: 'blur(12px) saturate(1.4)',
            WebkitBackdropFilter: 'blur(12px) saturate(1.4)',
            border: 'none',
            padding: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#fff',
            cursor: 'pointer',
            WebkitTapHighlightColor: 'transparent',
          }}
          aria-label="Close"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>

        {/* ⓘ File Info — top-right, mirrors the close X (same size, offset,
            tap target and shadow). Signed-in + Plex-owned items only. */}
        {canShowFileInfo && (
          <button
            type="button"
            data-file-info-button
            data-modal-actions
            onClick={() => setShowFileInfo(true)}
            style={{
              position: 'absolute',
              top: '12px',
              right: '16px',
              zIndex: 20,
              width: '40px',
              height: '40px',
              borderRadius: '50%',
              background: 'rgba(0,0,0,0.3)',
              backdropFilter: 'blur(12px) saturate(1.4)',
              WebkitBackdropFilter: 'blur(12px) saturate(1.4)',
              border: 'none',
              padding: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#fff',
              cursor: 'pointer',
              WebkitTapHighlightColor: 'transparent',
            }}
            aria-label="File info"
          >
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
              <line x1="12" y1="10.5" x2="12" y2="18" />
              <circle cx="12" cy="6.4" r="1.35" fill="currentColor" stroke="none" />
            </svg>
          </button>
        )}
      </div>

      {/* Full review sheet (Ratings & Reviews row) */}
      {openReview && (
        <ReviewSheet review={openReview} variant="compact" onClose={() => setOpenReview(null)} />
      )}


      {/* Similar movies panel */}
      {showSimilar && detail && (
        <SimilarPanel
          tmdbId={similarTmdbId}
          ratingKey={detail.ratingKey ?? null}
          title={detail.title}
          onClose={() => setShowSimilar(false)}
        />
      )}

      {/* File Info sheet (ⓘ) */}
      {showFileInfo && detail?.ratingKey && (
        <FileInfoSheet
          title={detail.title}
          ratingKey={detail.ratingKey}
          copies={fileInfoCopies}
          variant="compact"
          onClose={() => setShowFileInfo(false)}
          onFixMatch={isOwner ? (rk) => setFixMatchKey(rk) : undefined}
        />
      )}

      {fixMatchKey != null && detail && (
        <FixMatchSheet
          ratingKey={fixMatchKey}
          title={detail.title}
          year={detail.year}
          variant="compact"
          onClose={() => setFixMatchKey(null)}
          onMatched={handleFixMatched}
        />
      )}

      {/* Collection ownership sheet — swipe returns to this movie modal */}
      {showCollection && collectionId != null && (
        <CollectionsSheet
          open={showCollection}
          initialCollectionId={collectionId}
          overlayZIndex={10050}
          onClose={() => setShowCollection(false)}
        />
      )}

      {/* Sources panel — unified (compact mode renders bottom sheet) */}
      {showSources && detail && (
        <SourcesPanel
          tmdbId={detail.tmdbId}
          title={detail.title}
          year={detail.year}
          seedQuery={props.searchQuery}
          onClose={() => setShowSources(false)}
          onGrabbed={() => {
            setShowSources(false);
            onSourcesGrabbed?.();
          }}
          onAddToWatchlist={() => {
            setShowUpgrade(true);
          }}
        />
      )}

      {/* Upgrade quality bottom sheet */}
      {showUpgrade && detail && (
        <MobileUpgradeSheet
          isOwned={isOwned}
          tmdbId={detail.tmdbId ?? 0}
          title={detail.title}
          year={detail.year}
          onClose={() => setShowUpgrade(false)}
          onAdded={() => onUpgradeAdded?.()}
        />
      )}

      {/* Playback sheet */}
      {showPlayback && detail?.ratingKey && (
        <MobilePlaybackSheet
          ratingKey={detail.ratingKey}
          title={detail.title}
          libraries={detail.libraries}
          libraryKeys={detail.libraryKeys}
          onClose={() => setShowPlayback(false)}
        />
      )}

      {/* Trailer modal */}
      {showTrailer && trailerKey && detail && (
        <TrailerModal
          title={detail.title}
          youtubeKey={trailerKey}
          onClose={() => setShowTrailer(false)}
        />
      )}



      {showNativeAdd && detail && props.resolvedTmdbId && (
        <WatchlistAddSheet tmdbId={props.resolvedTmdbId} title={detail.title} year={detail.year}
          onClose={() => setShowNativeAdd(false)} onAdded={() => { setShowNativeAdd(false); props.onUpgradeAdded?.(); }} />
      )}
      {/* Remove from watchlist confirm sheet */}
      {showRemoveConfirm && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Remove from watchlist"
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 10004,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'flex-end',
          }}
        >
          <button
            type="button"
            aria-label="Close"
            disabled={removingWatchlist}
            onClick={() => setShowRemoveConfirm(false)}
            style={{
              position: 'absolute',
              inset: 0,
              border: 'none',
              background: 'rgba(0,0,0,0.45)',
              cursor: removingWatchlist ? 'default' : 'pointer',
            }}
          />
          <div
            style={{
              position: 'relative',
              background: 'rgba(22, 18, 28, 0.98)',
              borderTopLeftRadius: 18,
              borderTopRightRadius: 18,
              border: '1px solid rgba(255,255,255,0.08)',
              borderBottom: 'none',
              padding: '10px 16px calc(16px + env(safe-area-inset-bottom, 0px))',
              boxShadow: '0 -12px 40px rgba(0,0,0,0.45)',
            }}
          >
            <div
              style={{
                width: 36,
                height: 4,
                borderRadius: 2,
                background: 'rgba(255,255,255,0.18)',
                margin: '0 auto 14px',
              }}
            />
            <p style={{ margin: '0 0 4px', fontSize: 13, color: 'rgba(255,255,255,0.45)' }}>
              Watchlist
            </p>
            <p
              style={{
                margin: '0 0 8px',
                fontSize: 16,
                fontWeight: 600,
                color: 'rgba(255,255,255,0.92)',
                lineHeight: 1.3,
              }}
            >
              Remove {detail?.title ?? 'this movie'}?
            </p>
            <p style={{ margin: '0 0 16px', fontSize: 14, color: 'rgba(255,255,255,0.45)' }}>
              Unmonitors in Radarr — the movie record is kept.
            </p>
            {removeWatchlistError && (
              <p style={{ margin: '0 0 12px', fontSize: 14, color: '#f87171' }}>{removeWatchlistError}</p>
            )}
            <button
              type="button"
              disabled={removingWatchlist}
              onClick={async () => {
                const id = detail?.tmdbId ?? props.resolvedTmdbId;
                if (!id) return;
                setRemovingWatchlist(true);
                setRemoveWatchlistError(null);
                const result = await removeFromWatchlist(id);
                setRemovingWatchlist(false);
                if (result.ok) {
                  setShowRemoveConfirm(false);
                  onWatchlistRemoved?.();
                } else {
                  setRemoveWatchlistError(result.error || 'Failed to remove');
                }
              }}
              style={{
                width: '100%',
                border: 'none',
                borderRadius: 12,
                padding: '14px 16px',
                marginBottom: 8,
                background: 'hsla(0, 70%, 42%, 0.95)',
                color: 'white',
                fontSize: 15,
                fontWeight: 600,
                cursor: removingWatchlist ? 'default' : 'pointer',
                opacity: removingWatchlist ? 0.6 : 1,
              }}
            >
              {removingWatchlist ? 'Removing…' : 'Remove from watchlist'}
            </button>
            <button
              type="button"
              disabled={removingWatchlist}
              onClick={() => setShowRemoveConfirm(false)}
              style={{
                width: '100%',
                border: '1px solid rgba(255,255,255,0.12)',
                borderRadius: 12,
                padding: '14px 16px',
                background: 'rgba(255,255,255,0.06)',
                color: 'rgba(255,255,255,0.82)',
                fontSize: 15,
                fontWeight: 500,
                cursor: removingWatchlist ? 'default' : 'pointer',
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Grab toast */}
      {grabToast && (
        <div
          style={{
            position: 'fixed',
            bottom: '40px',
            left: '50%',
            transform: 'translateX(-50%)',
            padding: '12px 24px',
            borderRadius: '12px',
            background: 'rgba(74,222,128,0.15)',
            border: '1px solid rgba(74,222,128,0.3)',
            color: '#4ade80',
            fontSize: '14px',
            fontWeight: 600,
            zIndex: 10003,
            whiteSpace: 'nowrap',
            animation: 'mobileToastEnter 0.4s cubic-bezier(0.22, 1, 0.36, 1)',
          }}
        >
          Download started!
        </div>
      )}


      {/* Keyframes */}
      <style>{`
        @keyframes mobileShelfSlideUp {
          from { transform: translateY(100%); }
          to { transform: translateY(0); }
        }
        @keyframes webModalSpin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>,
    document.body,
  );
}
