import { invalidateDetails } from '@/services/modalPrefetch';
import { isMinimal } from '@/minimal';
import { useSurfacePresence } from '@/components/motion/useSurfacePresence';
import { useDetailMounted } from '@/loading/sheetOpenGate';
import { useState, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import type { FixMatchResult } from './FixMatchSheet';
import { useMovieDetail } from '@/hooks/useMovieDetail';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import type { MovieDetailModalProps } from './types';
import { ExpandedLayout } from './ExpandedLayout';
import { CompactLayout } from './CompactLayout';
import { FocusZone } from '@/components/tv/FocusZone';
import { useTVMode } from '@/contexts/TVModeContext';
import { NATIVE_WATCHLIST_URL, notifyWatchlistChanged } from '@/services/watchlistApi';
import { apiFetch } from '../../../apiClient';

export function MovieDetailModal({
  ratingKey,
  tmdbId,
  onClose,
  active = true,
  onLeave,
  origin,
  onPersonSearch,
  restoreSimilar = false,
  searchQuery,
}: MovieDetailModalProps) {
  // Data fetching via shared hook
  // Fix Match can move the item to a different title, so the ids are local state
  // seeded from props; reloadNonce refetches in place.
  const [ids, setIds] = useState<{ ratingKey?: number; tmdbId?: number }>({ ratingKey, tmdbId });
  const [reloadNonce, setReloadNonce] = useState(0);
  const [matchToast, setMatchToast] = useState<string | null>(null);
  const { detail, ratings, ratingsLoading, ratingsSettled, loading, error, trailerKey, ultraBlurColors, resolvedTmdbId } =
    useMovieDetail(ids.ratingKey, ids.tmdbId, reloadNonce);

  const handleMatched = useCallback((r: FixMatchResult) => {
    // The old title's details and ratings must not come back from the cache.
    invalidateDetails();
    setIds((prev) => ({
      ratingKey: r.rating_key || prev.ratingKey,
      tmdbId: r.unmatched ? prev.tmdbId : r.tmdb_id ?? prev.tmdbId,
    }));
    setReloadNonce((n) => n + 1);
    setMatchToast(r.unmatched ? 'Unmatched' : 'Matched');
    window.setTimeout(() => setMatchToast(null), 2200);
  }, []);

  // UI-only state
  const [showSources, setShowSources] = useState(false);
  const [showUpgrade, setShowUpgrade] = useState(false);
  const [showFullPlot, setShowFullPlot] = useState(false);
  const [showTrailer, setShowTrailer] = useState(false);
  const [showReviews, setShowReviews] = useState(false);
  const [showPlayback, setShowPlayback] = useState(false);
  const [showSimilar, setShowSimilar] = useState(restoreSimilar);
  const [showCollection, setShowCollection] = useState(false);
  const [grabToast, setGrabToast] = useState(false);
  const [watchlistToast, setWatchlistToast] = useState(false);
  const [inWatchlist, setInWatchlist] = useState(false);

  // Check if movie is already in Radarr watchlist
  useEffect(() => {
    const id = resolvedTmdbId;
    if (!id) return;
    let cancelled = false;
    // v202: minimal (test) builds read the native watchlist; never Radarr
    apiFetch(isMinimal() ? `${NATIVE_WATCHLIST_URL}/tmdb-ids` : '/api/radarr/watchlist/tmdb-ids')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data?.tmdb_ids) return;
        if ((data.tmdb_ids as number[]).includes(id)) {
          setInWatchlist(true);
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [resolvedTmdbId]);

  // One lifecycle for the sheet (useSurfacePresence): enter phase, a single close, the exit timer owned by this
  // mounted sheet, and Escape / Back only while this sheet is the top one.
  const { mode } = useLayoutMode();
  const nestedOpen = showSources || showUpgrade || showFullPlot || showTrailer || showReviews || showPlayback || showSimilar || showCollection;
  const { phase, requestClose: handleClose } = useSurfacePresence({
    kind: mode === 'compact' ? 'sheet' : 'modal', onExited: onClose, active, escapeBlocked: nestedOpen,
    onClosing: () => { setShowSources(false); setShowCollection(false); onLeave?.(); },
  });

  // Build view-model props
  const viewProps = {
    detail,
    ratings,
    ratingsLoading,
    ratingsSettled,
    trailerKey,
    ultraBlurColors,
    resolvedTmdbId,
    loading,
    error,
    phase,
    active,
    showSources,
    showTrailer,
    showFullPlot,
    showUpgrade,
    showReviews,
    showPlayback,
    showSimilar,
    showCollection,
    onClose: handleClose,
    setShowSources,
    setShowTrailer,
    setShowFullPlot,
    setShowUpgrade,
    setShowReviews,
    setShowPlayback,
    setShowSimilar,
    setShowCollection,
    onPersonSearch,
    origin,
    inWatchlist,
    grabToast,
    watchlistToast,
    searchQuery,
  };

  // Handle sources grab success
  const handleSourcesGrabbed = useCallback(() => {
    setShowSources(false);
    setGrabToast(true);
    setTimeout(() => setGrabToast(false), 2000);
  }, []);

  // Handle upgrade/watchlist success
  const handleUpgradeAdded = useCallback(() => {
    setShowUpgrade(false);
    setInWatchlist(true);
    setWatchlistToast(true);
    setTimeout(() => setWatchlistToast(false), 2000);
    const id = resolvedTmdbId;
    if (id) notifyWatchlistChanged({ tmdbId: id, action: 'added' });
  }, [resolvedTmdbId]);

  const handleWatchlistRemoved = useCallback(() => {
    setInWatchlist(false);
  }, []);

  // Inject the grab/upgrade callbacks into the view props
  // The layouts will pass these through to SourcesPanel/UpgradeModal
  const extendedProps = {
    ...viewProps,
    // Keyed by the resolved ids and the match reload, so a Fix Match starts a new ratings row.
    openKey: `${ids.ratingKey ?? ''}:${ids.tmdbId ?? ''}:${reloadNonce}`,
    onSourcesGrabbed: handleSourcesGrabbed,
    onUpgradeAdded: handleUpgradeAdded,
    onWatchlistRemoved: handleWatchlistRemoved,
    onMatched: handleMatched,
  };

  const { isTvMode } = useTVMode();
  useDetailMounted();

  if (phase === 'gone') return null;

  const matchToastEl = matchToast
    ? createPortal(
        <div
          role="status"
          data-match-toast
          style={{
            position: 'fixed',
            left: '50%',
            bottom: 'calc(env(safe-area-inset-bottom, 0px) + 36px)',
            transform: 'translateX(-50%)',
            zIndex: 10080,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '11px 20px 11px 16px',
            borderRadius: 999,
            background: 'rgba(28,24,52,0.92)',
            backdropFilter: 'blur(16px) saturate(1.4)',
            WebkitBackdropFilter: 'blur(16px) saturate(1.4)',
            border: '1px solid rgba(255,255,255,0.1)',
            boxShadow: '0 10px 30px rgba(0,0,0,0.45)',
            color: '#fff',
            fontSize: 15,
            fontWeight: 600,
            whiteSpace: 'nowrap',
            animation: 'matchToastIn var(--m-dur-reveal) var(--m-ease-reveal)',
            pointerEvents: 'none',
          }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#4ade80" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <polyline points="20 6 9 17 4 12" />
          </svg>
          {matchToast}
          <style>{`@keyframes matchToastIn { from { opacity: 0; transform: translateX(-50%) translateY(12px) scale(0.96); } to { opacity: 1; transform: translateX(-50%) translateY(0) scale(1); } }`}</style>
        </div>,
        document.body,
      )
    : null;

  const layout = (
    <>
      {mode === 'compact'
        ? <CompactLayout {...extendedProps} />
        : <ExpandedLayout {...extendedProps} />}
      {matchToastEl}
    </>
  );

  // In TV mode, wrap the modal in a FocusZone with trapFocus for DPAD containment
  if (isTvMode) {
    return (
      <FocusZone
        zoneId="movie-detail-modal"
        orientation="vertical"
        trapFocus
        onEscape={handleClose}
      >
        {layout}
      </FocusZone>
    );
  }

  return layout;
}
