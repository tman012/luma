import { useState, useEffect, useCallback, useMemo } from 'react';
import { SHEET_EXIT_MS, useCloseOnce } from '@/components/layout/SheetFrame';
import { useTvDetail } from '@/hooks/useTvDetail';
import { useEpisodeData } from '@/hooks/useEpisodeData';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { checkSonarrStatus, type SonarrStatus } from '@/services/sonarrApi';
import { fetchPlexSeasons, type PlexSeason } from '@/services/playbackApi';
import type { TvDetailModalProps, SourcesTarget } from './types';
import { ExpandedLayout } from './ExpandedLayout';
import { CompactLayout } from './CompactLayout';
import { OngoingShowPrompt } from './OngoingShowPrompt';
import { useAuth } from '@/contexts/AuthContext';
import { SonarrStatusContext, useSonarrSeriesStatus } from './SonarrStatus';
import { FocusZone } from '@/components/tv/FocusZone';
import { useTVMode } from '@/contexts/TVModeContext';

export function TvDetailModal({
  ratingKey,
  tmdbId,
  onClose,
  origin,
  onPersonSearch,
  episodeContext,
  initialSeason,
  searchQuery,
}: TvDetailModalProps) {
  // Data fetching via shared hook
  const tvData = useTvDetail(ratingKey, tmdbId);
  const {
    show,
    seasons,
    tvdbId,
    ownership,
    showOwned,
    sonarrStatus: hookSonarrStatus,
    loading,
    error,
    seasonsLoading,
    ultraBlurColors,
    resolvedTmdbId,
  } = tvData;

  // Local episode context state — initialized from prop, updated by episode row clicks
  const [localEpisodeContext, setLocalEpisodeContext] = useState(episodeContext);
  // Sync from prop when it changes externally
  useEffect(() => {
    setLocalEpisodeContext(episodeContext);
  }, [episodeContext]);

  // Episode-mode data fetching. The season grid is library-numbered, so pass
  // the matching grid row as a fallback for episodes TMDb can't be keyed on.
  const gridEpisode = useMemo(() => {
    if (!localEpisodeContext) return null;
    const season = seasons.find(
      (s) => s.season_number === localEpisodeContext.seasonNumber,
    );
    return season?.episodes.find(
      (e) => e.episode_number === localEpisodeContext.episodeNumber,
    ) ?? null;
  }, [seasons, localEpisodeContext]);

  const { episodeData, episodeLoading } = useEpisodeData(
    localEpisodeContext, resolvedTmdbId, gridEpisode,
  );

  // Local override for Sonarr status — refreshed after adding to Sonarr
  const [sonarrStatusOverride, setSonarrStatusOverride] = useState<SonarrStatus | null>(null);
  const sonarrStatus = sonarrStatusOverride ?? hookSonarrStatus;

  // UI-only state
  const [selectedSeason, setSelectedSeason] = useState<number>(-1);
  const [showFullShow, setShowFullShow] = useState(false);
  const [phase, setPhase] = useState<'entering' | 'visible' | 'exiting' | 'gone'>('entering');

  // Auto-select season from episodeContext once seasons are loaded
  // (suppressed when user navigated to full show view via clearlogo)
  useEffect(() => {
    if (showFullShow) return;
    if (localEpisodeContext && seasons.length > 0 && selectedSeason === -1) {
      const match = seasons.find(
        (s) => s.season_number === localEpisodeContext.seasonNumber,
      );
      if (match) {
        setSelectedSeason(localEpisodeContext.seasonNumber);
      }
    }
  }, [localEpisodeContext, seasons, selectedSeason, showFullShow]);

  // Auto-select season from initialSeason prop (notification deep link)
  // Only fires when no episodeContext and season hasn't been selected yet
  useEffect(() => {
    if (initialSeason != null && seasons.length > 0 && selectedSeason === -1 && !localEpisodeContext) {
      const match = seasons.find(s => s.season_number === initialSeason);
      if (match) setSelectedSeason(initialSeason);
    }
  }, [initialSeason, seasons, selectedSeason, localEpisodeContext]);
  const [showSources, setShowSources] = useState<SourcesTarget | null>(null);
  const [showOngoingPrompt, setShowOngoingPrompt] = useState(false);
  const [showAddToSonarr, setShowAddToSonarr] = useState(false);
  const [showFullPlot, setShowFullPlot] = useState(false);
  const [showPlayback, setShowPlayback] = useState(false);
  const [playbackRatingKey, setPlaybackRatingKey] = useState<number | undefined>();
  const [playbackTitle, setPlaybackTitle] = useState<string>('');
  const [grabToast, setGrabToast] = useState(false);
  const [watchlistToast, setWatchlistToast] = useState(false);
  // Season grid + shuffle state (mobile only)
  const [seasonViewMode, setSeasonViewMode] = useState<'pills' | 'grid'>('pills');
  const [plexSeasons, setPlexSeasons] = useState<PlexSeason[]>([]);
  const [shuffleTarget, setShuffleTarget] = useState<{ ratingKey: number; label: string } | null>(null);

  // Animate open on the very next frame — deliberately NOT gated on `loading`.
  // Holding the card at opacity 0 until data arrived also hid the layouts' own
  // loading spinner, so the tap read as dead. Shell first, content as it lands.
  // Mirrors MovieDetailModal.
  useEffect(() => {
    const frame = requestAnimationFrame(() => setPhase('visible'));
    return () => cancelAnimationFrame(frame);
  }, []);

  // Fetch Plex season children when show is owned (for season grid + shuffle)
  useEffect(() => {
    if (!showOwned || !show?.ratingKey) return;
    let cancelled = false;
    fetchPlexSeasons(show.ratingKey).then((seasons) => {
      if (!cancelled) setPlexSeasons(seasons);
    });
    return () => { cancelled = true; };
  }, [showOwned, show?.ratingKey]);

  const { mode, isTablet } = useLayoutMode();

  // Animated close — compact: SheetFrame's exit (--m-dur-sheet-exit), tablet 200ms, expanded 300ms
  const closeOnce = useCloseOnce();
  const handleClose = useCallback(() => {
    if (!closeOnce()) return;
    setShowSources(null);
    setShowOngoingPrompt(false);
    setShowAddToSonarr(false);
    setShowFullPlot(false);
    setPhase('exiting');
    const duration = mode === 'compact' ? SHEET_EXIT_MS : isTablet ? 200 : 300;
    setTimeout(() => {
      setPhase('gone');
      onClose();
    }, duration);
  }, [onClose, mode, isTablet, closeOnce]);


  // MOTION.md §1.6: back (AppModalHost popstate) plays the exit from wherever the sheet is, then the host unmounts it.
  useEffect(() => {
    const onDismiss = (e: Event) => { e.preventDefault(); if (closeOnce()) setPhase('exiting'); };
    window.addEventListener('luma:modal-dismiss', onDismiss);
    return () => window.removeEventListener('luma:modal-dismiss', onDismiss);
  }, [closeOnce]);

  // Escape key
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        if (!showSources && !showOngoingPrompt && !showAddToSonarr && !showFullPlot && !showPlayback) {
          handleClose();
        }
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleClose, showSources, showOngoingPrompt, showAddToSonarr, showFullPlot, showPlayback]);

  // Navigate to episode detail view from an episode row click
  const handleEpisodeClick = useCallback((seasonNumber: number, episodeNumber: number) => {
    const key = `s${seasonNumber}e${episodeNumber}`;
    const episodeRatingKey = ownership[key]?.rating_key;
    setLocalEpisodeContext({ seasonNumber, episodeNumber, episodeRatingKey });
    setShowFullShow(false);
  }, [ownership]);

  // Sources always opens the release browser (manual grab). A show that
  // isn't in Sonarr yet is added unmonitored by the backend on demand, so a
  // manual grab never turns on monitoring.
  const requestSources = useCallback((v: SourcesTarget | null) => {
    setShowSources(v);
  }, []);

  // Owner-only, read-only Sonarr status (show / season / episode).
  const { user } = useAuth();
  const isOwner = !!user?.is_admin;
  const {
    data: sonarrSeriesStatus,
    refresh: refreshSonarrSeriesStatus,
    setEpisodeMonitored,
    setSeasonMonitored,
    setSeriesMonitored,
    setSeriesMonitorMode,
  } = useSonarrSeriesStatus(tvdbId, resolvedTmdbId, isOwner);
  // Season names + have/total for the show-level monitor prompt.
  const seasonInfo = useMemo(() => seasons.map((s) => {
    const prefix = `s${s.season_number}e`;
    let have = 0;
    for (const k of Object.keys(ownership)) if (k.startsWith(prefix) && ownership[k]?.owned) have++;
    return { season: s.season_number, name: s.name, have, total: s.episodes?.length || s.episode_count || have };
  }), [seasons, ownership]);
  const sonarrCtx = useMemo(
    () => ({
      data: sonarrSeriesStatus, setEpisodeMonitored, setSeasonMonitored, setSeriesMonitored, setSeriesMonitorMode,
      owner: isOwner, showTitle: show?.title, seasonInfo,
    }),
    [sonarrSeriesStatus, setEpisodeMonitored, setSeasonMonitored, setSeriesMonitored, setSeriesMonitorMode, isOwner, show?.title, seasonInfo],
  );

  // Handle sources grab success
  const handleSourcesGrabbed = useCallback(() => {
    setShowSources(null);
    setGrabToast(true);
    setTimeout(() => setGrabToast(false), 2000);
    // Watch Sonarr's queue closely for the next couple of minutes.
    refreshSonarrSeriesStatus(150_000);
  }, [refreshSonarrSeriesStatus]);

  if (phase === 'gone') return null;

  // Build view-model props
  const viewProps = {
    show,
    seasons,
    tvdbId,
    ownership,
    showOwned,
    sonarrStatus,
    loading,
    error,
    seasonsLoading,
    ultraBlurColors,
    resolvedTmdbId,
    selectedSeason,
    setSelectedSeason,
    phase,
    showSources,
    showAddToSonarr,
    showFullPlot,
    showPlayback,
    onClose: handleClose,
    setShowSources: requestSources,
    setShowAddToSonarr,
    setShowFullPlot,
    setShowPlayback,
    onPersonSearch,
    origin,
    grabToast,
    watchlistToast,
    // Episode mode
    episodeContext: localEpisodeContext,
    episodeData,
    episodeLoading,
    showFullShow,
    setShowFullShow,
    onEpisodeClick: handleEpisodeClick,
    // Playback
    playbackRatingKey,
    playbackTitle,
    onPlayEpisode: (rk: number, t: string) => {
      setPlaybackRatingKey(rk);
      setPlaybackTitle(t);
      setShowPlayback(true);
    },
    // Season grid + shuffle
    seasonViewMode,
    setSeasonViewMode,
    plexSeasons,
    shuffleTarget,
    setShuffleTarget,
    searchQuery,
  };


  // Handle Sonarr add success
  const handleSonarrAdded = useCallback(() => {
    // Refresh Sonarr status
    if (tvdbId) {
      checkSonarrStatus(tvdbId, resolvedTmdbId ?? undefined).then(setSonarrStatusOverride);
    }
    refreshSonarrSeriesStatus(180_000);
    setWatchlistToast(true);
    setTimeout(() => setWatchlistToast(false), 2000);
  }, [tvdbId, resolvedTmdbId, refreshSonarrSeriesStatus]);

  // Whole-show "Add to Watchlist" routing: ALWAYS open the monitoring modal so
  // the user explicitly picks what to monitor (All / First Season / Latest /
  // Future / None / specific seasons) instead of blindly grabbing every season.
  // This also guarantees visible feedback — no silent background add.
  const requestAddToWatchlist = useCallback((open: boolean) => {
    setShowAddToSonarr(open);
  }, []);

  const extendedProps = {
    ...viewProps,
    setShowAddToSonarr: requestAddToWatchlist,
    onSourcesGrabbed: handleSourcesGrabbed,
    onSonarrAdded: handleSonarrAdded,
  };

  const { isTvMode } = useTVMode();

  const layout = mode === 'compact'
    ? <CompactLayout {...extendedProps} />
    : <ExpandedLayout {...extendedProps} />;

  const ongoingPrompt = showOngoingPrompt && show && tvdbId ? (
    <OngoingShowPrompt
      show={show}
      seasons={seasons}
      tvdbId={tvdbId}
      tmdbId={resolvedTmdbId}
      onClose={() => setShowOngoingPrompt(false)}
      onAdded={() => {
        setShowOngoingPrompt(false);
        handleSonarrAdded();
      }}
    />
  ) : null;

  const withStatus = (node: React.ReactNode) => (
    <SonarrStatusContext.Provider value={sonarrCtx}>{node}</SonarrStatusContext.Provider>
  );

  if (isTvMode) {
    return withStatus(
      <FocusZone
        zoneId="tv-detail-modal"
        orientation="vertical"
        trapFocus
        onEscape={handleClose}
      >
        {layout}
        {ongoingPrompt}
      </FocusZone>
    );
  }

  return withStatus(
    <>
      {layout}
      {ongoingPrompt}
    </>
  );
}
