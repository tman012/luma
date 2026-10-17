import { useTitleReveal, logoInput, titleTextStyle, revealStyle } from '@/hooks/useTitleReveal';
import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { formatRuntime } from '@/utils/image';
import {
  CARD_BG,
  PERSON_FALLBACK_SVG,
  getCastImageUrl,
  deduplicateCastAndCrew,
  type Ratings,
  type CastMember,
} from '@/utils/modalParsers';
import { TrailerModal } from '@/components/media/TrailerModal';
import { UltraBlurOverlay } from '@/components/media/UltraBlurOverlay';
import { RatingsBar } from '@/components/media/RatingsBar';
import { PlaybackModal } from '@/components/media/PlaybackModal';
import { FullPlotModal } from './FullPlotModal';
import {
  type ProcessedRelease,
  buildQualityGroups,
  getBadgeColor,
} from './sourcesUtils';
import { useMovieSourcesData } from './useMovieSourcesData';
import { SegmentRow, sortTrackerTopsFirst, needsLibraryGrab, IndexerQueryOverride } from '@/components/media/sourcesShared';
import { grabToLibrary } from '@/services/allSourcesApi';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { useTabletPerf } from '@/hooks/useTabletPerf';
import { useFocusable } from '@/hooks/useFocusable';
import { useReviews } from '@/hooks/useReviews';
import { SimilarPanel } from './SimilarPanel';
import { CollectionsSheet } from '@/components/collections/CollectionsSheet';
import { TOKENS } from './expandedLayoutTokens';
import { useModalHost } from '@/contexts/ModalHostContext';
import type { MovieModalViewProps } from './types';
import { removeFromWatchlist } from '@/services/watchlistApi';
import { useAuth } from '@/contexts/AuthContext';
import { FixMatchSheet, type FixMatchResult } from './FixMatchSheet';
import { FileInfoSheet, FileInfoIcon } from './FileInfoSheet';
import { TopReviewsRow, ReviewSheet, useTopReviews, type TopReview } from '@/components/media/TopReviews';
import { apiFetch } from '../../../apiClient';
import { isMinimal } from '@/minimal';

// ---------------------------------------------------------------------------
// CastShelf — slide-up shelf with preloaded circular cast photos
// ---------------------------------------------------------------------------

function CastShelf({
  cast,
  isTablet,
  onPersonSearch,
  onClose,
  reviews = [],
  onOpenReview,
}: {
  cast: CastMember[];
  isTablet: boolean;
  onPersonSearch?: (name: string, tmdbPersonId?: number) => void;
  onClose: () => void;
  /** Ratings & Reviews row shown above the cast (hidden when empty). */
  reviews?: TopReview[];
  onOpenReview?: (r: TopReview) => void;
}) {
  const hasReviews = reviews.length > 0 && !!onOpenReview;
  const sidePad = isTablet ? 24 : 48;
  const [imagesReady, setImagesReady] = useState(false);
  const imgSize = isTablet ? 130 : 150;

  useEffect(() => {
    const urls = cast
      .map((m) => getCastImageUrl(m))
      .filter((u): u is string => !!u);
    if (urls.length === 0) { setImagesReady(true); return; }

    let loaded = 0;
    const target = Math.min(urls.length, 8); // only wait for first visible batch
    const check = () => { if (++loaded >= target) setImagesReady(true); };

    urls.slice(0, target).forEach((url) => {
      const img = new Image();
      img.onload = check;
      img.onerror = check;
      img.src = url;
    });

    // Safety timeout — show shelf even if images are slow
    const timer = setTimeout(() => setImagesReady(true), 1500);
    return () => clearTimeout(timer);
  }, [cast]);

  return (
    <>
      <div
        onClick={onClose}
        style={{ position: 'absolute', inset: 0, zIndex: 7 }}
      />
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          right: 0,
          zIndex: 8,
          // Soft scrim behind the taller shelf when reviews are shown so the
          // review text stays legible over the (shifted) backdrop.
          background: hasReviews
            ? 'linear-gradient(to top, rgba(0,0,0,0.78) 0%, rgba(0,0,0,0.55) 70%, rgba(0,0,0,0) 100%)'
            : 'transparent',
          padding: isTablet ? '40px 0 32px' : '48px 0 48px',
          animation: imagesReady ? 'castShelfBounce 0.55s cubic-bezier(0.34, 1.56, 0.64, 1)' : 'none',
          transform: imagesReady ? 'translateY(0)' : 'translateY(100%)',
        }}
      >
        {hasReviews && (
          <TopReviewsRow reviews={reviews} variant="expanded" onOpen={onOpenReview!} sidePadding={sidePad} />
        )}
        {hasReviews && cast.length > 0 && (
          <h2 style={{ fontSize: 22, fontWeight: 600, color: '#fff', margin: '0 0 14px', padding: `0 ${sidePad}px`, textShadow: '0 1px 4px rgba(0,0,0,0.5)' }}>
            Cast &amp; crew
          </h2>
        )}
        <div style={{ position: 'relative' }}>
          <div
            style={{
              display: 'flex',
              gap: isTablet ? '24px' : '32px',
              padding: isTablet ? '0 24px' : '0 48px',
              overflowX: 'auto',
              WebkitOverflowScrolling: 'touch',
              scrollbarWidth: 'none',
              paddingBottom: '8px',
            }}
          >
            {cast.map((member, idx) => {
              const imgUrl = getCastImageUrl(member);
              return (
                <div
                  key={`cast-${member.name}-${idx}`}
                  onClick={() => onPersonSearch?.(member.name, member.tmdbPersonId)}
                  style={{
                    flexShrink: 0,
                    width: `${imgSize}px`,
                    textAlign: 'center',
                    cursor: onPersonSearch ? 'pointer' : 'default',
                  }}
                >
                  <img
                    src={imgUrl ?? PERSON_FALLBACK_SVG}
                    alt={member.name}
                    style={{
                      width: `${imgSize}px`,
                      height: `${imgSize}px`,
                      borderRadius: '50%',
                      objectFit: 'cover',
                      background: 'rgba(255,255,255,0.1)',
                      marginBottom: '12px',
                    }}
                    onError={(e) => {
                      (e.currentTarget as HTMLImageElement).src = PERSON_FALLBACK_SVG;
                    }}
                  />
                  <p style={{
                    fontSize: '14px',
                    fontWeight: 500,
                    color: 'rgba(255,255,255,0.9)',
                    margin: '0 0 3px',
                    lineHeight: 1.3,
                    wordWrap: 'break-word',
                  }}>
                    {member.name}
                  </p>
                  {(member.character || member.role) && (
                    <p style={{
                      fontSize: '13px',
                      color: 'rgba(255,255,255,0.6)',
                      margin: 0,
                      lineHeight: 1.3,
                    }}>
                      {member.character ?? member.role}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// PillButton — minimal inline pill with icon + label (Google TV style)
// ---------------------------------------------------------------------------

function PillButton({
  icon,
  label,
  onClick,
  primary,
  padding = '12px 24px',
  fontSize = '15px',
  focusKey,
  zoneId,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  primary?: boolean;
  padding?: string;
  fontSize?: string;
  focusKey: string;
  zoneId: string;
}) {
  const { ref, focusProps } = useFocusable({ focusKey, zoneId, onEnter: onClick });
  const [hovered, setHovered] = useState(false);

  return (
    <button
      ref={ref}
      {...focusProps}
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '8px',
        padding,
        borderRadius: '28px',
        background: primary
          ? (hovered ? 'rgba(255,255,255,0.88)' : '#fff')
          : (hovered ? 'rgba(255,255,255,0.18)' : 'rgba(255,255,255,0.08)'),
        border: primary ? 'none' : '1px solid rgba(255,255,255,0.15)',
        color: primary ? '#111' : '#fff',
        fontSize,
        fontWeight: 600,
        cursor: 'pointer',
        transition: 'background 0.15s ease',
        backdropFilter: primary ? 'none' : 'blur(12px)',
        WebkitBackdropFilter: primary ? 'none' : 'blur(12px)',
      }}
    >
      {icon}
      {label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// FileInfoButton — circled-i in the top-right, just left of the close X
// (same chip style/size as the X; DPAD-focusable in TV mode)
// ---------------------------------------------------------------------------

function FileInfoButton({ onClick, top, right, svgSize }: { onClick: () => void; top: string; right: string; svgSize: string }) {
  const { ref, focusProps } = useFocusable({ focusKey: 'mm-info-fileinfo', zoneId: 'movie-detail-modal', onEnter: onClick });
  return (
    <button
      ref={ref}
      {...focusProps}
      type="button"
      data-file-info-button
      aria-label="File info"
      onClick={onClick}
      style={{
        position: 'absolute',
        top,
        right,
        zIndex: 10,
        background: 'rgba(0,0,0,0.3)',
        border: 'none',
        // ~1.3× the X's glyph in the same 44px chip; thin stroke
        padding: '4px',
        borderRadius: '50%',
        minWidth: '44px',
        minHeight: '44px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: '#fff',
        cursor: 'pointer',
        lineHeight: 1,
        opacity: 0.85,
        transition: 'opacity 0.2s ease, background 0.2s ease',
        filter: 'drop-shadow(0 1px 3px rgba(0,0,0,0.6))',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.opacity = '1'; e.currentTarget.style.background = 'rgba(0,0,0,0.45)'; }}
      onMouseLeave={(e) => { e.currentTarget.style.opacity = '0.85'; e.currentTarget.style.background = 'rgba(0,0,0,0.3)'; }}
    >
      <FileInfoIcon size={Math.round(Number(svgSize) * 1.3)} strokeWidth={1.4} light />
    </button>
  );
}

// ---------------------------------------------------------------------------
// ExpandedLayout
// ---------------------------------------------------------------------------

export function ExpandedLayout(props: MovieModalViewProps) {
  const {
    detail,
    ratings,
    trailerKey,
    ultraBlurColors,
    loading,
    error,
    phase,
    showTrailer,
    showFullPlot,
    onClose,
    showSimilar,
    showCollection,
    setShowTrailer,
    setShowFullPlot,
    setShowSimilar,
    setShowCollection,
    inWatchlist,
    onUpgradeAdded,
    onWatchlistRemoved,
    onSourcesGrabbed,
  } = props;
  const titleState = useTitleReveal(logoInput(detail), detail?.title, detail?.backdropUrl || detail?.posterUrl);

  const overlayRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const logoRef = useRef<HTMLDivElement>(null);

  // Backdrop effects hook no longer needed — full-bleed backdrop with scrim handles readability
  const [showAlreadyInWatchlist, setShowAlreadyInWatchlist] = useState(false);
  const [removingWatchlist, setRemovingWatchlist] = useState(false);
  const [removeWatchlistError, setRemoveWatchlistError] = useState<string | null>(null);
  const [showUpgradeShelf, setShowUpgradeShelf] = useState(false);
  const upgradeShelfRef = useRef<HTMLDivElement>(null);
  const [upgradeAdding, setUpgradeAdding] = useState<string | null>(null);
  const [upgradeError, setUpgradeError] = useState<string | null>(null);
  const [upgradeErrorMsg, setUpgradeErrorMsg] = useState<string | null>(null);
  const [showSourcesShelf, setShowSourcesShelf] = useState(false);
  const collectionId = detail?.collectionId ?? null;
  // File Info sheet (ⓘ above the meta line)
  const [showFileInfo, setShowFileInfo] = useState(false);
  // Ratings & Reviews (Rotten Tomatoes critics → audience) — row lives in the Cast shelf
  const topReviews = useTopReviews(detail?.title ?? null, detail?.year ?? null);
  const [openReview, setOpenReview] = useState<TopReview | null>(null);
  const { authenticated, user } = useAuth();
  const [fixMatchKey, setFixMatchKey] = useState<number | null>(null);
  const isOwner = !!user?.is_admin;
  const onMatchedProp = props.onMatched;
  const handleFixMatched = useCallback((r: FixMatchResult) => {
    setFixMatchKey(null);
    setShowFileInfo(false);
    onMatchedProp?.(r);
  }, [onMatchedProp]);

  const [sourcesIndexerQuery, setSourcesIndexerQuery] = useState<string | null>(null);
  const sourcesShelfRef = useRef<HTMLDivElement>(null);
  const [showCastShelf, setShowCastShelf] = useState(false);
  const modalHost = useModalHost();
  const [srcQuality, setSrcQuality] = useState<string | null>(null);
  const [srcSourceType, setSrcSourceType] = useState<string | null>(null);
  const [srcIndexerFilter, setSrcIndexerFilter] = useState<string | null>(null);
  const [srcConfirmTarget, setSrcConfirmTarget] = useState<ProcessedRelease | null>(null);
  const [srcConfirmLoading, setSrcConfirmLoading] = useState(false);
  const [srcGrabError, setSrcGrabError] = useState<string | null>(null);
  const { isTablet } = useLayoutMode();
  const t = isTablet ? TOKENS.tablet : TOKENS.desktop;
  const perf = useTabletPerf();

  // Reviews data — deferred until Info shelf is opened (RT scraping is slow)
  const [reviewsRequested, setReviewsRequested] = useState(false);
  const reviewsQuery = useReviews(reviewsRequested ? (detail?.title ?? '') : '', detail?.year ?? null);
  const reviewData = reviewsQuery.data;

  // Effective ratings: Plex ratings augmented with RT scores from reviews
  const effectiveRatings = useMemo<Ratings | null>(() => {
    const rtCritic = ratings?.rotten_tomatoes_critic?.score != null
      ? ratings.rotten_tomatoes_critic
      : reviewData?.critic_score != null
        ? { score: reviewData.critic_score }
        : null;
    const rtAudience = ratings?.rotten_tomatoes_audience?.score != null
      ? ratings.rotten_tomatoes_audience
      : reviewData?.audience_score != null
        ? { score: reviewData.audience_score }
        : null;
    const imdb = ratings?.imdb ?? null;
    const metacritic = ratings?.metacritic ?? null;

    const tmdb = ratings?.tmdb ?? null;

    if (!imdb?.score && !rtCritic?.score && !rtAudience?.score && !metacritic?.score && !tmdb?.score) {
      return null;
    }
    return { imdb, rotten_tomatoes_critic: rtCritic, rotten_tomatoes_audience: rtAudience, metacritic, tmdb };
  }, [ratings, reviewData]);


  // ---------------------------------------------------------------------------
  // Shelf handlers
  // ---------------------------------------------------------------------------
  // Trigger reviews fetch on mount when detail loads
  useEffect(() => {
    if (detail) setReviewsRequested(true);
  }, [detail]);

  const handleCastOpen = useCallback(() => {
    setShowSourcesShelf(false);
    setShowUpgradeShelf(false);
    setShowCastShelf(prev => !prev);
  }, []);

  const handleUpgradeOpen = useCallback(() => {
    setShowSourcesShelf(false);
    setShowCastShelf(false);
    setShowUpgradeShelf(prev => !prev);
    setUpgradeAdding(null);
    setUpgradeError(null);
    setUpgradeErrorMsg(null);
  }, []);

  const handleSourcesOpen = useCallback(() => {
    setShowUpgradeShelf(false);
    setShowCastShelf(false);
    setShowSourcesShelf(prev => {
      if (prev) return false;
      setSrcQuality(null);
      setSrcSourceType(null);
      setSrcIndexerFilter(null);
      setSrcConfirmTarget(null);
      setSrcConfirmLoading(false);
      setSrcGrabError(null);
      setSourcesIndexerQuery(null);
      return true;
    });
  }, []);

  // System back while a full review is open -> close just the review
  const registerBackHandler = modalHost.registerBackHandler;
  useEffect(() => {
    if (!openReview) return;
    registerBackHandler(() => { setOpenReview(null); return true; });
    return () => registerBackHandler(null);
  }, [openReview, registerBackHandler]);

  // Close shelf on Escape — capture phase so it fires before modal's Escape handler
  // (yields to the review sheet's own Escape handler while a review is open)
  const shelfOpen = (showUpgradeShelf || showSourcesShelf || showCastShelf) && !openReview;
  useEffect(() => {
    if (!shelfOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopImmediatePropagation();
        if (showSourcesShelf) {
          if (srcConfirmTarget) { setSrcConfirmTarget(null); return; }
          if (srcSourceType) { setSrcSourceType(null); setSrcIndexerFilter(null); return; }
          if (srcQuality) { setSrcQuality(null); return; }
        }
        setShowUpgradeShelf(false);
        setShowSourcesShelf(false);
        setShowCastShelf(false);
      }
    };
    window.addEventListener('keydown', handler, true);
    return () => window.removeEventListener('keydown', handler, true);
  }, [shelfOpen, showSourcesShelf, srcConfirmTarget, srcSourceType, srcQuality]);


  // Sources search — the shared hook, gated on the shelf being open so the
  // search does not run for every movie the user merely opens.
  const {
    loading: sourcesLoading,
    error: sourcesError,
    timedOut: sourcesTimedOut,
    slow: sourcesSlow,
    releases: sourcesReleases,
    movieId: sourcesMovieId,
  } = useMovieSourcesData({
    tmdbId: detail?.tmdbId,
    title: detail?.title ?? '',
    year: detail?.year ?? null,
    indexerQuery: sourcesIndexerQuery,
    enabled: showSourcesShelf && !!detail,
  });

  // Handle sources confirm download
  const handleSrcDownload = useCallback(async () => {
    if (!srcConfirmTarget) return;
    setSrcConfirmLoading(true);
    setSrcGrabError(null);
    try {
      // Raw indexer row or a rejected Arr row → grab-to-library (bypasses Arr).
      if (needsLibraryGrab(srcConfirmTarget)) {
        const result = await grabToLibrary({
          downloadUrl: srcConfirmTarget.downloadUrl ?? '',
          torrentName: srcConfirmTarget.title,
          tmdbId: detail?.tmdbId,
          mediaType: 'movie',
          title: detail?.title ?? srcConfirmTarget.title,
          year: detail?.year ?? null,
        });
        if (result.success) {
          setSrcConfirmLoading(false);
          setSrcConfirmTarget(null);
          setShowSourcesShelf(false);
          window.dispatchEvent(new Event('download-grabbed'));
          onSourcesGrabbed?.();
          return;
        }
        setSrcGrabError(result.error || 'Failed to add download');
        setSrcConfirmLoading(false);
        return;
      }

      const resp = await apiFetch('/api/radarr/movie/grab', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          guid: srcConfirmTarget.guid,
          indexer_id: srcConfirmTarget.indexerId,
          movie_id: sourcesMovieId,
        }),
      });
      if (resp.ok) {
        setSrcConfirmLoading(false);
        setSrcConfirmTarget(null);
        setShowSourcesShelf(false);
        window.dispatchEvent(new Event('download-grabbed'));
        onSourcesGrabbed?.();
        return;
      }
      const data = await resp.json().catch(() => ({}));
      setSrcGrabError(data.error || `Failed to grab release (${resp.status})`);
    } catch {
      setSrcGrabError('Network error — could not reach server');
    }
    setSrcConfirmLoading(false);
  }, [srcConfirmTarget, sourcesMovieId, onSourcesGrabbed, detail]);

  // Parallax removed — fit-to-viewport layout, no scrolling needed

  // ---------------------------------------------------------------------------
  // Lock body scroll
  // ---------------------------------------------------------------------------
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  // ---------------------------------------------------------------------------
  // Overlay click closes
  // ---------------------------------------------------------------------------
  const handleOverlayClick = (e: React.MouseEvent) => {
    if (e.target === overlayRef.current) onClose();
  };

  // ---------------------------------------------------------------------------
  // Derived
  // ---------------------------------------------------------------------------
  const isOwned = detail?.isOwned ?? false;
  const similarTmdbId = detail?.tmdbId ?? props.resolvedTmdbId ?? null;
  // ⓘ File Info — Plex items only, signed-in users only (shows NAS paths)
  const canShowFileInfo = authenticated && isOwned && !!detail?.ratingKey;
  const fileInfoCopies = detail?.libraryKeys
    ? Object.entries(detail.libraryKeys)
        .map(([library, rk]) => ({ library, ratingKey: Number(rk) }))
        .filter((c) => Number.isFinite(c.ratingKey) && c.ratingKey > 0)
    : [];
  const genresDisplay = detail?.genres.slice(0, 3) ?? [];
  const runtimeDisplay = detail ? formatRuntime(detail.durationMs) : '';

  // Build meta info items (joined with • separators)
  const metaItems: string[] = [];
  if (detail?.year) metaItems.push(String(detail.year));
  if (runtimeDisplay) metaItems.push(runtimeDisplay);
  if (detail?.contentRating) metaItems.push(detail.contentRating);
  if (genresDisplay.length > 0) metaItems.push(genresDisplay.join(', '));

  // Sources computed values
  const srcQualityGroups = buildQualityGroups(sourcesReleases);
  const srcSelectedGroup = srcQuality ? srcQualityGroups.find(g => g.quality === srcQuality) : null;
  const srcSelectedReleases = srcSelectedGroup && srcSourceType
    ? srcSelectedGroup.sourceTypes.find(st => st.type === srcSourceType)?.items ?? []
    : [];
  const srcFilteredReleases = sortTrackerTopsFirst(
    srcIndexerFilter
      ? srcSelectedReleases.filter(r => r.indexer === srcIndexerFilter)
      : srcSelectedReleases,
  );
  const srcUniqueIndexers = [...new Set(srcSelectedReleases.map(r => r.indexer))];

  // ---------------------------------------------------------------------------
  // Animation — tablet: opacity-only (skip scale/transform for GPU savings)
  // ---------------------------------------------------------------------------
  const overlayOpacity = phase === 'visible' ? 1 : 0;
  const cardTransform = perf.reduceMotion
    ? 'none'
    : phase === 'entering'
      ? 'scale(0.95) translateY(20px)'
      : phase === 'visible'
        ? 'scale(1) translateY(0)'
        : 'scale(0.97) translateY(10px)';
  const cardOpacity = phase === 'visible' ? 1 : 0;

  if (phase === 'gone') return null;

  return (
    <>
    {createPortal(
    <div
      data-modal="MovieDetailModal"
      data-variant="web-v2"
      ref={overlayRef}
      onClick={handleOverlayClick}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 100,
        display: 'flex',
        alignItems: t.overlay.alignItems,
        justifyContent: 'center',
        background: t.overlay.background,
        opacity: overlayOpacity,
        transition: 'opacity 0.3s ease',
        padding: t.overlay.padding,
      }}
    >
      {/* Card */}
      <div
        style={{
          position: 'relative',
          width: '100%',
          maxWidth: t.card.maxWidth,
          height: t.card.height,
          borderRadius: t.card.borderRadius,
          overflow: 'hidden',
          backgroundColor: ultraBlurColors ? t.card.bgUltraBlur : CARD_BG,
          boxShadow: t.card.boxShadow,
          transform: cardTransform,
          opacity: cardOpacity,
          transition: perf.reduceMotion
            ? 'opacity 0.2s ease'
            : phase === 'exiting'
              ? 'transform 0.3s ease, opacity 0.3s ease'
              : 'transform 0.5s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.5s cubic-bezier(0.16, 1, 0.3, 1)',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* ⓘ File Info — sits just left of the close X (44px chip + 12px gap) */}
        {canShowFileInfo && !isMinimal() && (
          <FileInfoButton
            top={t.close.top}
            right={`calc(${t.close.right} + 56px)`}
            svgSize={t.close.svgSize}
            onClick={() => setShowFileInfo(true)}
          />
        )}

        {/* Close X — top-left, no container */}
        <button
          type="button"
          onClick={onClose}
          style={{
            position: 'absolute',
            top: t.close.top,
            right: t.close.right,
            zIndex: 10,
            background: 'rgba(0,0,0,0.3)',
            border: 'none',
            padding: '6px',
            borderRadius: '50%',
            minWidth: '44px',
            minHeight: '44px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#fff',
            cursor: 'pointer',
            lineHeight: 1,
            opacity: 0.85,
            transition: 'opacity 0.2s ease, background 0.2s ease',
            filter: 'drop-shadow(0 1px 3px rgba(0,0,0,0.6))',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.opacity = '1'; e.currentTarget.style.background = 'rgba(0,0,0,0.45)'; }}
          onMouseLeave={(e) => { e.currentTarget.style.opacity = '0.85'; e.currentTarget.style.background = 'rgba(0,0,0,0.3)'; }}
          aria-label="Close"
        >
          <svg
            width={t.close.svgSize}
            height={t.close.svgSize}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>

        {/* Backdrop — full-bleed background behind everything */}
        {!loading && !error && detail && (
          <div
            ref={backdropRef}
            style={{
              position: 'absolute',
              inset: 0,
              zIndex: 0,
              overflow: 'hidden',
              borderRadius: t.card.borderRadius,
              opacity: phase === 'visible' ? 1 : 0,
              transition: 'opacity 0.4s ease-out',
            }}
          >
            {detail.backdropUrl ? (
              <img
                key={detail.backdropUrl}
                src={detail.backdropUrl}
                alt=""
                style={{
                  ...revealStyle(titleState),
                  width: '100%',
                  height: '100%',
                  objectFit: 'cover',
                  objectPosition: 'center top',
                  ...(t.backdrop.imgFilter ? { filter: t.backdrop.imgFilter } : {}),
                }}
              />
            ) : detail.posterUrl ? (
              <img
                key={detail.posterUrl}
                src={detail.posterUrl}
                alt=""
                style={{ width: '100%', height: '100%', objectFit: 'cover', opacity: titleState === 'hold' ? 0 : 0.6, transition: 'opacity 0.45s ease-out' }}
              />
            ) : null}
            {/* Vignette */}
            {detail.backdropUrl && (
              <div style={{ position: 'absolute', inset: 0, background: 'radial-gradient(ellipse at 50% 30%, transparent 40%, rgba(0,0,0,0.2) 100%)', pointerEvents: 'none' }} />
            )}
          </div>
        )}

        {/* UltraBlur color layer — absolute, blends into backdrop lower portion */}
        {ultraBlurColors && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              zIndex: 1,
              pointerEvents: 'none',
              WebkitMaskImage: t.ultraBlurMask,
              maskImage: t.ultraBlurMask,
            }}
          >
            <UltraBlurOverlay hold={titleState === 'hold'} colors={ultraBlurColors} variant={t.ultraBlurVariant} animationDelay={300} />
          </div>
        )}

        {/* Readability scrim — gradient for text over backdrop */}
        <div
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 2,
            pointerEvents: 'none',
            background: t.scrim,
            borderRadius: t.card.borderRadius,
          }}
        />

        {/* Content — flex column, justify-end so content sits at bottom */}
        <div
          ref={scrollRef}
          data-scroll-container
          data-reveal={loading || titleState !== 'hold' ? 'shown' : 'hold'}
          className="luma-scroll-y"
          style={{
            // One reveal for all content, with the backdrop, blur and title.
            ...revealStyle(loading ? 'text' : titleState),
            position: 'relative',
            zIndex: 3,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'flex-end',
            height: '100%',
            overflowY: 'auto',
            overflowX: 'hidden',
            WebkitOverflowScrolling: 'touch',
            overscrollBehavior: 'contain',
            touchAction: 'pan-y',
          }}
        >

        {/* Loading state */}
        {loading && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flex: 1,
            }}
          >
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
          </div>
        )}

        {/* Error state */}
        {!loading && error && (
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '20px',
              flex: 1,
            }}
          >
            <p style={{ fontSize: '22px', color: 'rgba(255,255,255,0.7)' }}>
              Couldn’t load details
            </p>
            <button
              type="button"
              onClick={onClose}
              style={{
                padding: '12px 29px',
                borderRadius: '10px',
                border: 'none',
                background: 'var(--color-accent)',
                color: 'rgba(255,255,255,0.95)',
                fontSize: '18px',
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
            {/* === Clearlogo — always visible, even over cast shelf === */}
            <div
              ref={logoRef}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                right: 0,
                bottom: '35%',
                zIndex: 9,
                pointerEvents: 'none',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                padding: t.contentPadding,
                opacity: phase === 'visible' ? 1 : 0,
                transition: 'none', // the container's single reveal fades it
                willChange: 'opacity',
              }}
            >
              {/* Push logo up when cast shelf is open */}
              <div style={{
                maxWidth: t.logo.maxWidth,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'transform 0.5s cubic-bezier(0.22, 1, 0.36, 1)',
                transform: showCastShelf ? 'translateY(-15vh)' : 'translateY(0)',
              }}>
                {titleState === 'logo' && detail.clearLogoUrl ? (
                  <img
                    src={detail.clearLogoUrl}
                    alt={detail.title}
                    style={{
                      maxWidth: '100%',
                      maxHeight: t.logo.maxHeight,
                      objectFit: 'contain',
                      filter: t.logo.filter,
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
                    fontSize: t.titleFontSize,
                    fontWeight: 700,
                    color: '#fff',
                    lineHeight: 1.15,
                    margin: 0,
                    textShadow: '0 2px 12px rgba(0,0,0,0.6)',
                    letterSpacing: '-0.02em',
                  }}
                >
                  {detail.title}
                </h1>
              </div>
            </div>

            {/* === CONTENT — meta+plot+pills at bottom === */}
            <div style={{
              opacity: (showSourcesShelf || showCastShelf) ? 0 : (phase === 'visible' ? 1 : 0),
              transition: (showSourcesShelf || showCastShelf) ? 'none' : 'opacity 1s ease-in-out 0.8s',
              willChange: 'opacity',
              display: 'flex',
              flexDirection: 'column',
              height: '100%',
              padding: t.contentPadding,
              textAlign: 'center',
            }}>

              {/* Top spacer — pushes bottom group down */}
              <div style={{ flex: '1 1 0' }} />

              {/* ── Bottom group: meta → ratings → plot → pills ── */}
              <div style={{ flexShrink: 0 }}>

                {/* Info block — shifted up from pills */}
                <div style={{ marginBottom: '48px' }}>
                  {/* Meta line (year, runtime, etc.) */}
                  {metaItems.length > 0 && (
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: '16px',
                        flexWrap: 'wrap',
                        fontSize: '26px',
                        fontWeight: 500,
                        color: 'rgba(255,255,255,0.85)',
                        marginBottom: effectiveRatings ? '12px' : '20px',
                        textShadow: '0 1px 4px rgba(0,0,0,0.5)',
                      }}
                    >
                      {metaItems.map((item, i) => (
                        <span key={i} style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
                          {i > 0 && <span style={{ color: 'rgba(255,255,255,0.25)' }}>&middot;</span>}
                          <span>{item}</span>
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Ratings */}
                  {effectiveRatings && (
                    <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '18px' }}>
                      <RatingsBar ratings={effectiveRatings} size="lg" />
                    </div>
                  )}

                  {/* Plot — full text, wide */}
                  {detail.summary && (
                    <p style={{
                      fontSize: '24px',
                      lineHeight: 1.6,
                      color: 'rgba(255,255,255,0.88)',
                      margin: '0 auto 0',
                      maxWidth: '1100px',
                      textShadow: '0 1px 4px rgba(0,0,0,0.5)',
                    }}>
                      {detail.summary}
                    </p>
                  )}
                </div>

                {/* Action pills */}
                <div data-modal-actions
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: t.pill.gap,
                    flexWrap: 'wrap',
                  }}
                >
                  {isOwned ? (
                    <PillButton
                      focusKey="mm-info-watch"
                      zoneId="movie-detail-modal"
                      primary
                      padding={t.pill.padding}
                      fontSize={t.pill.fontSize}
                      icon={
                        <svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="currentColor">
                          <polygon points="6,3 20,12 6,21" />
                        </svg>
                      }
                      label="Play"
                      onClick={() => props.setShowPlayback(true)}
                    />
                  ) : (
                    <PillButton
                      focusKey="mm-info-watchlist"
                      zoneId="movie-detail-modal"
                      primary
                      padding={t.pill.padding}
                      fontSize={t.pill.fontSize}
                      icon={
                        inWatchlist ? (
                          <svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                            <polyline points="20 6 9 17 4 12" />
                          </svg>
                        ) : (
                          <svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                            <line x1="12" y1="5" x2="12" y2="19" />
                            <line x1="5" y1="12" x2="19" y2="12" />
                          </svg>
                        )
                      }
                      label={inWatchlist ? 'In Watchlist' : 'Watchlist'}
                      onClick={() => inWatchlist ? setShowAlreadyInWatchlist(true) : handleUpgradeOpen()}
                    />
                  )}
                  {trailerKey && (
                    <PillButton
                      focusKey="mm-info-trailer"
                      zoneId="movie-detail-modal"
                      padding={t.pill.padding}
                      fontSize={t.pill.fontSize}
                      icon={
                        <svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
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
                  {isOwned && (
                    <PillButton
                      focusKey="mm-info-upgrade"
                      zoneId="movie-detail-modal"
                      padding={t.pill.padding}
                      fontSize={t.pill.fontSize}
                      icon={
                        <svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <line x1="12" y1="19" x2="12" y2="5" />
                          <polyline points="5 12 12 5 19 12" />
                        </svg>
                      }
                      label="Upgrade"
                      onClick={handleUpgradeOpen}
                    />
                  )}
                  <span data-modal-sources="" style={{ display: 'contents' }}>
                  <PillButton
                    focusKey="mm-info-sources"
                    zoneId="movie-detail-modal"
                    padding={t.pill.padding}
                    fontSize={t.pill.fontSize}
                    icon={
                      <svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
                        <polyline points="7 10 12 15 17 10" />
                        <line x1="12" y1="15" x2="12" y2="3" />
                      </svg>
                    }
                    label="Sources"
                    onClick={handleSourcesOpen}
                  />
                  </span>
                  {similarTmdbId && (
                    <PillButton
                      focusKey="mm-info-similar"
                      zoneId="movie-detail-modal"
                      padding={t.pill.padding}
                      fontSize={t.pill.fontSize}
                      icon={
                        <svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <polygon points="12 2 2 7 12 12 22 7 12 2" />
                          <polyline points="2 17 12 22 22 17" />
                          <polyline points="2 12 12 17 22 12" />
                        </svg>
                      }
                      label="Similar"
                      onClick={() => setShowSimilar(true)}
                    />
                  )}
                  {collectionId != null && (
                    <PillButton
                      focusKey="mm-info-collection"
                      zoneId="movie-detail-modal"
                      padding={t.pill.padding}
                      fontSize={t.pill.fontSize}
                      icon={
                        <svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
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
                  {(detail.cast?.length > 0 || detail.directors?.length > 0 || topReviews.length > 0) && (
                    <PillButton
                      focusKey="mm-info-cast"
                      zoneId="movie-detail-modal"
                      padding={t.pill.padding}
                      fontSize={t.pill.fontSize}
                      icon={
                        <svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                          <circle cx="9" cy="7" r="4" />
                          <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                          <path d="M16 3.13a4 4 0 0 1 0 7.75" />
                        </svg>
                      }
                      label={topReviews.length > 0 ? 'Cast & Reviews' : 'Cast'}
                      onClick={handleCastOpen}
                    />
                  )}
                </div>

              </div>{/* end bottom group */}

            </div>{/* end content area */}

          </>
        )}
        </div>{/* end content container */}




        {/* Upgrade shelf — same visual style as info/cast shelves */}
        {showUpgradeShelf && detail && (
          <>
            {/* Click-catcher to dismiss */}
            <div
              onClick={() => setShowUpgradeShelf(false)}
              style={{
                position: 'absolute',
                inset: 0,
                zIndex: 7,
              }}
            />
            {/* Shelf */}
            <div
              ref={upgradeShelfRef}
              onClick={(e) => e.stopPropagation()}
              style={{
                position: 'absolute',
                bottom: 0,
                left: 0,
                right: 0,
                zIndex: 8,
                background: 'rgba(0, 0, 0, 0.02)',
                backdropFilter: 'blur(12px)',
                WebkitBackdropFilter: 'blur(12px)',
                borderRadius: '20px 20px 0 0',
                padding: t.upgrade.padding,
                animation: 'infoShelfSlideUp 0.45s ease-out',
                WebkitMaskImage: t.upgrade.mask,
                maskImage: t.upgrade.mask,
              }}
            >
              {/* Text scrim — light gradient for readability */}
              <div
                style={{
                  position: 'absolute',
                  inset: 0,
                  borderRadius: '20px 20px 0 0',
                  background: 'linear-gradient(to bottom, rgba(0,0,0,0.1) 0%, rgba(0,0,0,0.25) 50%, rgba(0,0,0,0.4) 100%)',
                  pointerEvents: 'none',
                }}
              />
              {/* Quality options — horizontal row */}
              <div style={{ position: 'relative' }}>
                <h2 style={{ fontSize: t.upgrade.headingFontSize, fontWeight: 600, color: '#fff', margin: t.upgrade.headingMargin, textShadow: '0 2px 8px rgba(0,0,0,0.7)', textAlign: 'center' }}>
                  {isOwned ? 'Upgrade Quality' : 'Select Quality'}
                </h2>
                <div
                  style={{
                    display: 'flex',
                    gap: t.upgrade.optionsGap,
                    justifyContent: 'center',
                  }}
                >
                  {[
                    { name: 'Standard', profile: '', desc: 'Streaming optimized' },
                    { name: '1080p Remux', profile: '1080p Remux', desc: 'Full quality 1080p' },
                    { name: '2160p Remux', profile: '2160p Remux', desc: 'Full quality 4K' },
                  ].map((opt) => {
                    const isAdding = upgradeAdding === opt.name;
                    const isError = upgradeError === opt.name;
                    return (
                      <button
                        key={opt.name}
                        type="button"
                        disabled={!!upgradeAdding}
                        onClick={async () => {
                          if (upgradeAdding) return;
                          setUpgradeAdding(opt.name);
                          setUpgradeError(null);
                          setUpgradeErrorMsg(null);
                          try {
                            const resp = await apiFetch('/api/watchlist', {
                              method: 'POST',
                              headers: { 'Content-Type': 'application/json' },
                              body: JSON.stringify({
                                tmdb_id: detail.tmdbId,
                                media_type: 'movie',
                                title: detail.title,
                                year: detail.year,
                                quality_profile: opt.profile,
                              }),
                            });
                            const result = (await resp.json()) as Record<string, unknown>;
                            if (resp.ok && result.success) {
                              setShowUpgradeShelf(false);
                              onUpgradeAdded?.();
                            } else {
                              setUpgradeAdding(null);
                              setUpgradeError(opt.name);
                              setUpgradeErrorMsg((result.error as string) ?? 'Failed — try again');
                              setTimeout(() => { setUpgradeError(null); setUpgradeErrorMsg(null); }, 4000);
                            }
                          } catch {
                            setUpgradeAdding(null);
                            setUpgradeError(opt.name);
                            setUpgradeErrorMsg('Network error');
                            setTimeout(() => { setUpgradeError(null); setUpgradeErrorMsg(null); }, 4000);
                          }
                        }}
                        style={{
                          flex: 1,
                          maxWidth: t.upgrade.optMaxWidth,
                          background: isError
                            ? 'rgba(244, 67, 54, 0.2)'
                            : 'rgba(30, 30, 30, 0.95)',
                          border: isError
                            ? '2px solid rgba(244, 67, 54, 0.6)'
                            : '1px solid rgba(255, 255, 255, 0.1)',
                          borderRadius: '16px',
                          padding: t.upgrade.optPadding,
                          cursor: upgradeAdding ? 'default' : 'pointer',
                          transition: 'all 0.2s',
                          textAlign: 'center',
                        }}
                      >
                        <div
                          style={{
                            fontSize: t.upgrade.optNameFontSize,
                            fontWeight: 600,
                            color: isError ? '#ef4444' : '#fff',
                            textShadow: '0 2px 6px rgba(0,0,0,0.6)',
                            marginBottom: isAdding || isError ? '0' : '6px',
                          }}
                        >
                          {isAdding ? 'Adding...' : isError ? 'Failed' : opt.name}
                        </div>
                        {isError && upgradeErrorMsg && (
                          <div style={{ fontSize: '13px', color: '#f87171', lineHeight: 1.4, marginTop: '4px' }}>
                            {upgradeErrorMsg}
                          </div>
                        )}
                        {!isAdding && !isError && (
                          <div style={{ fontSize: t.upgrade.optDescFontSize, color: 'rgba(255,255,255,0.6)', lineHeight: 1.4 }}>
                            {opt.desc}
                          </div>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          </>
        )}

        {/* Cast & Crew shelf */}
        {showCastShelf && detail && (
          <CastShelf
            cast={deduplicateCastAndCrew(
              detail.directors ?? [],
              (detail.cast ?? []).slice(0, 30),
              'Director',
            )}
            isTablet={isTablet}
            onPersonSearch={(name, personId) => {
              if (personId) {
                modalHost.openPerson({ personId, personName: name });
                setShowCastShelf(false);
              } else {
                props.onPersonSearch?.(name, personId);
              }
            }}
            onClose={() => setShowCastShelf(false)}
            reviews={topReviews}
            onOpenReview={setOpenReview}
          />
        )}

        {/* Sources overlay — full-screen centered */}
        {showSourcesShelf && detail && (
          <>
            <div
              onClick={() => setShowSourcesShelf(false)}
              style={{ position: 'absolute', inset: 0, zIndex: 7, background: 'rgba(0,0,0,0.85)' }}
            />
            <div
              ref={sourcesShelfRef}
              onClick={(e) => e.stopPropagation()}
              style={{
                position: 'absolute',
                inset: 0,
                zIndex: 8,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                pointerEvents: 'none',
              }}
            >
              <div style={{ pointerEvents: 'auto', width: '100%', maxWidth: '900px', padding: '0 32px' }}>
                <IndexerQueryOverride
                  mediaTitle={detail.title ?? ''}
                  defaultQuery={props.searchQuery}
                  appliedQuery={sourcesIndexerQuery}
                  onApply={setSourcesIndexerQuery}
                  onClear={() => setSourcesIndexerQuery(null)}
                  disabled={sourcesLoading}
                />
                {/* Loading */}
                {sourcesLoading && (
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '24px' }}>
                    <div style={{ width: '72px', height: '72px', border: '4px solid rgba(255,255,255,0.15)', borderTopColor: 'rgba(255,255,255,0.7)', borderRadius: '50%', animation: 'webModalSpin 0.8s linear infinite' }} />
                    <span style={{ fontSize: '36px', color: 'rgba(255,255,255,0.6)', fontWeight: 500 }}>Searching for sources...</span>
                    {sourcesSlow && (
                      <span style={{ fontSize: '22px', color: 'rgba(255,255,255,0.4)', textAlign: 'center', maxWidth: '620px' }}>
                        Taking longer than usual — indexers are slow to answer while the library is busy.
                      </span>
                    )}
                  </div>
                )}
                {/* Error */}
                {!sourcesLoading && sourcesError && (
                  <div style={{ textAlign: 'center', display: 'flex', flexDirection: 'column', gap: '12px' }}>
                    <span style={{ fontSize: '28px', color: 'rgba(255,255,255,0.6)' }}>
                      {sourcesTimedOut ? 'Search timed out' : 'Failed to load sources'}
                    </span>
                    <span style={{ fontSize: '20px', color: 'rgba(255,255,255,0.35)' }}>
                      {sourcesTimedOut
                        ? 'Radarr and the indexers did not answer in time. Close and reopen to retry.'
                        : 'Radarr and the indexers both failed to answer'}
                    </span>
                  </div>
                )}
                {/* No results */}
                {!sourcesLoading && !sourcesError && sourcesReleases.length === 0 && (
                  <div style={{ textAlign: 'center' }}>
                    <span style={{ fontSize: '28px', color: 'rgba(255,255,255,0.6)' }}>No sources found</span>
                  </div>
                )}

                {/* Results — multi-level drill-down */}
                {!sourcesLoading && !sourcesError && sourcesReleases.length > 0 && (() => {
                  // Level 4: Confirm download
                  if (srcConfirmTarget) {
                    const badge = getBadgeColor(srcConfirmTarget.indexer);
                    const ageDays = srcConfirmTarget.age != null ? Math.round(srcConfirmTarget.age) : null;
                    return (
                      <div style={{ textAlign: 'center' }}>
                        <span style={{
                          display: 'inline-block', padding: '8px 20px', borderRadius: '12px', fontSize: '20px', fontWeight: 700,
                          background: badge.bg, color: badge.fg, border: `1px solid ${badge.border}`, marginBottom: '16px',
                        }}>{srcConfirmTarget.indexer}</span>
                        <p style={{ fontSize: '22px', color: 'rgba(255,255,255,0.9)', lineHeight: 1.5, wordBreak: 'break-word', margin: '0 0 20px' }}>
                          {srcConfirmTarget.title}
                        </p>
                        <div style={{ display: 'flex', gap: '24px', fontSize: '20px', justifyContent: 'center', flexWrap: 'wrap', marginBottom: srcGrabError ? '16px' : '32px' }}>
                          <span style={{ color: '#fff', fontWeight: 600 }}>
                            {srcConfirmTarget.sizeGb ? `${srcConfirmTarget.sizeGb} GB` : '\u2014'}
                          </span>
                          {ageDays != null && <span style={{ color: 'rgba(255,255,255,0.6)' }}>{ageDays}d ago</span>}
                          <span style={{ color: '#4ade80', fontWeight: 600 }}>
                            {srcConfirmTarget.seeders} seeders
                          </span>
                        </div>
                        {srcGrabError && (
                          <div style={{
                            background: 'rgba(239, 68, 68, 0.15)',
                            border: '1px solid rgba(239, 68, 68, 0.3)',
                            borderRadius: '12px',
                            padding: '12px 16px',
                            marginBottom: '16px',
                            fontSize: '15px',
                            color: '#fca5a5',
                            lineHeight: 1.5,
                            textAlign: 'center',
                          }}>
                            {srcGrabError}
                          </div>
                        )}
                        <div style={{ display: 'flex', gap: '20px', justifyContent: 'center' }}>
                          <button type="button" onClick={() => setSrcConfirmTarget(null)} disabled={srcConfirmLoading}
                            style={{ padding: '18px 40px', borderRadius: '50px', border: '1px solid rgba(255,255,255,0.2)', background: 'rgba(255,255,255,0.08)', color: '#fff', fontSize: '22px', fontWeight: 600, cursor: srcConfirmLoading ? 'default' : 'pointer' }}>
                            Cancel
                          </button>
                          <button type="button" onClick={handleSrcDownload} disabled={srcConfirmLoading}
                            style={{ padding: '18px 44px', borderRadius: '50px', border: '1px solid rgba(255,255,255,0.3)', background: 'rgba(255,255,255,0.15)', color: '#fff', fontSize: '22px', fontWeight: 600, cursor: srcConfirmLoading ? 'default' : 'pointer', opacity: srcConfirmLoading ? 0.7 : 1 }}>
                            {srcConfirmLoading ? 'Sending...' : 'Download'}
                          </button>
                        </div>
                      </div>
                    );
                  }

                  // Level 3: Release list
                  if (srcSourceType && srcSelectedGroup) {
                    return (
                      <>
                        {srcUniqueIndexers.length > 1 && (
                          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '16px', justifyContent: 'center' }}>
                            <button type="button" onClick={() => setSrcIndexerFilter(null)}
                              style={{ padding: '6px 16px', borderRadius: '20px', border: 'none', cursor: 'pointer', fontSize: '16px', fontWeight: 600, background: srcIndexerFilter === null ? 'rgba(255,255,255,0.25)' : 'rgba(255,255,255,0.1)', color: '#fff', opacity: srcIndexerFilter === null ? 1 : 0.6 }}>
                              All
                            </button>
                            {srcUniqueIndexers.map(indexer => {
                              const iBadge = getBadgeColor(indexer);
                              const active = srcIndexerFilter === indexer;
                              return (
                                <button key={indexer} type="button" onClick={() => setSrcIndexerFilter(indexer)}
                                  style={{ padding: '6px 16px', borderRadius: '20px', cursor: 'pointer', fontSize: '16px', fontWeight: 600, background: iBadge.fg, color: '#000', border: active ? '2px solid #fff' : '1px solid transparent' }}>
                                  {indexer}
                                </button>
                              );
                            })}
                          </div>
                        )}
                        <div data-scroll-container className="luma-scroll-y" style={{ maxHeight: '55vh', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '8px', WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}>
                          {srcFilteredReleases.map((release, i) => {
                            const rBadge = getBadgeColor(release.indexer);
                            const ageDays = release.age != null ? Math.round(release.age) : null;
                            return (
                              <div key={release.guid || i} role="button" onClick={() => { setSrcConfirmTarget(release); setSrcGrabError(null); }}
                                style={{ display: 'flex', flexDirection: 'column', gap: '8px', padding: '18px 24px', background: 'rgba(30, 30, 30, 0.95)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '14px', cursor: 'pointer', transition: 'background 0.2s ease' }}>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                  <span style={{ display: 'inline-block', padding: '4px 12px', borderRadius: '8px', fontSize: '13px', fontWeight: 700, alignSelf: 'flex-start', background: rBadge.bg, color: rBadge.fg, border: `1px solid ${rBadge.border}` }}>
                                    {release.indexer}
                                  </span>
                                  <span style={{ fontSize: '16px', fontWeight: 500, color: '#fff', wordBreak: 'break-word', lineHeight: 1.4 }}>
                                    {release.title}
                                  </span>
                                </div>
                                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', fontSize: '16px' }}>
                                  <span style={{ color: '#fff', fontWeight: 600 }}>
                                    {release.sizeGb ? `${release.sizeGb} GB` : '\u2014'}
                                  </span>
                                  <span style={{ color: 'rgba(255,255,255,0.6)', textAlign: 'center' }}>
                                    {ageDays != null ? `${ageDays}d ago` : '\u2014'}
                                  </span>
                                  <span style={{ color: '#4ade80', textAlign: 'right', fontWeight: 600 }}>
                                    {release.seeders} seeders
                                  </span>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </>
                    );
                  }

                  // Level 2: Source type selection — normalized rows w/ pills
                  if (srcQuality && srcSelectedGroup) {
                    return (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', width: '100%', maxWidth: '560px', margin: '0 auto' }}>
                        {srcSelectedGroup.sourceTypes.map(st => (
                          <SegmentRow
                            key={st.type}
                            label={st.type}
                            count={st.items.length}
                            releases={st.items}
                            colorFor={getBadgeColor}
                            onClick={() => setSrcSourceType(st.type)}
                          />
                        ))}
                      </div>
                    );
                  }

                  // Level 1: Quality selection (default) — normalized rows, count only
                  return (
                    <>
                      <h2 style={{ fontSize: '30px', fontWeight: 600, color: '#fff', margin: '0 0 20px', textAlign: 'center' }}>Sources</h2>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', width: '100%', maxWidth: '560px', margin: '0 auto' }}>
                        {srcQualityGroups.map(group => (
                          <SegmentRow
                            key={group.quality}
                            label={group.quality}
                            count={group.totalCount}
                            releases={[]}
                            colorFor={getBadgeColor}
                            showPills={false}
                            onClick={() => setSrcQuality(group.quality)}
                          />
                        ))}
                      </div>
                    </>
                  );
                })()}
              </div>
            </div>
          </>
        )}

        {/* Remove from Watchlist confirm */}
        {showAlreadyInWatchlist && (
          <div
            onClick={() => { if (!removingWatchlist) setShowAlreadyInWatchlist(false); }}
            style={{
              position: 'fixed',
              inset: 0,
              zIndex: 200,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'rgba(0,0,0,0.6)',
            }}
          >
            <div
              onClick={(e) => e.stopPropagation()}
              role="dialog"
              aria-modal="true"
              aria-label="Remove from Watchlist"
              style={{
                background: 'var(--color-kiosk-black)',
                borderRadius: '16px',
                padding: '32px 40px',
                textAlign: 'center',
                border: '1px solid rgba(255,255,255,0.1)',
                maxWidth: '360px',
                width: '90%',
              }}
            >
              <div style={{
                width: '48px',
                height: '48px',
                borderRadius: '50%',
                background: 'rgba(248,113,113,0.15)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                margin: '0 auto 16px',
              }}>
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#f87171" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </div>
              <p style={{ fontSize: '20px', fontWeight: 600, color: '#fff', margin: '0 0 8px' }}>
                Remove from Watchlist?
              </p>
              <p style={{ fontSize: '15px', color: 'rgba(255,255,255,0.5)', margin: '0 0 20px' }}>
                {detail?.title ?? 'This movie'} will be unmonitored in Radarr. It will not be deleted.
              </p>
              {removeWatchlistError && (
                <p style={{ fontSize: '14px', color: '#f87171', margin: '0 0 12px' }}>{removeWatchlistError}</p>
              )}
              <div style={{ display: 'flex', gap: '10px', justifyContent: 'center' }}>
                <button
                  type="button"
                  disabled={removingWatchlist}
                  onClick={() => setShowAlreadyInWatchlist(false)}
                  style={{
                    padding: '10px 24px',
                    borderRadius: '10px',
                    border: '1px solid rgba(255,255,255,0.12)',
                    background: 'rgba(255,255,255,0.06)',
                    color: '#fff',
                    fontSize: '16px',
                    fontWeight: 500,
                    cursor: removingWatchlist ? 'default' : 'pointer',
                    opacity: removingWatchlist ? 0.5 : 1,
                  }}
                >
                  Cancel
                </button>
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
                      setShowAlreadyInWatchlist(false);
                      onWatchlistRemoved?.();
                    } else {
                      setRemoveWatchlistError(result.error || 'Failed to remove');
                    }
                  }}
                  style={{
                    padding: '10px 24px',
                    borderRadius: '10px',
                    border: 'none',
                    background: 'hsla(0, 70%, 42%, 0.95)',
                    color: '#fff',
                    fontSize: '16px',
                    fontWeight: 600,
                    cursor: removingWatchlist ? 'default' : 'pointer',
                    opacity: removingWatchlist ? 0.6 : 1,
                  }}
                >
                  {removingWatchlist ? 'Removing…' : 'Remove'}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Playback modal */}
        {props.showPlayback && detail?.ratingKey && (
          <PlaybackModal
            ratingKey={detail.ratingKey}
            title={detail.title}
            clearLogoUrl={detail.clearLogoUrl}
            mediaType="movie"
            libraries={detail.libraries}
            libraryKeys={detail.libraryKeys}
            onClose={() => props.setShowPlayback(false)}
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

        {showCollection && collectionId != null && (
          <CollectionsSheet
            open={showCollection}
            initialCollectionId={collectionId}
            overlayZIndex={10050}
            onClose={() => setShowCollection(false)}
          />
        )}

        {openReview && (
          <ReviewSheet review={openReview} variant="expanded" onClose={() => setOpenReview(null)} />
        )}

        {showFileInfo && detail?.ratingKey && (
          <FileInfoSheet
            title={detail.title}
            ratingKey={detail.ratingKey}
            copies={fileInfoCopies}
            variant="expanded"
            onClose={() => setShowFileInfo(false)}
            onFixMatch={isOwner ? (rk) => setFixMatchKey(rk) : undefined}
          />
        )}

        {fixMatchKey != null && detail && (
          <FixMatchSheet
            ratingKey={fixMatchKey}
            title={detail.title}
            year={detail.year}
            variant="expanded"
            onClose={() => setFixMatchKey(null)}
            onMatched={handleFixMatched}
          />
        )}

        {showSimilar && detail && (
          <SimilarPanel
            tmdbId={similarTmdbId}
            ratingKey={detail.ratingKey ?? null}
            title={detail.title}
            onClose={() => setShowSimilar(false)}
          />
        )}



        {/* Grab success toast */}
        {props.grabToast && (
          <div
            style={{
              position: 'absolute',
              bottom: '32px',
              left: '50%',
              transform: 'translateX(-50%)',
              background: 'rgba(34,197,94,0.95)',
              color: '#fff',
              padding: '12px 28px',
              borderRadius: '12px',
              fontSize: '16px',
              fontWeight: 600,
              boxShadow: '0 8px 30px rgba(0,0,0,0.5)',
              animation: 'webToastIn 0.25s ease',
              whiteSpace: 'nowrap',
              zIndex: 100,
            }}
          >
            Added to Download Client!
          </div>
        )}


        {/* Keyframes */}
        <style>{`@keyframes webModalSpin { to { transform: rotate(360deg); } } @keyframes spin { to { transform: rotate(360deg); } } @keyframes webToastIn { from { opacity: 0; transform: translateX(-50%) translateY(10px); } to { opacity: 1; transform: translateX(-50%) translateY(0); } } @keyframes infoShelfSlideUp { from { transform: translateY(100%); } to { transform: translateY(0); } } @keyframes castShelfBounce { from { transform: translateY(100%); } to { transform: translateY(0); } }`}</style>
      </div>
    </div>,
    document.body,
  )}
    {showFullPlot && detail?.summary && createPortal(
      <FullPlotModal text={detail.summary} onClose={() => setShowFullPlot(false)} />,
      document.body,
    )}
    </>
  );
}
