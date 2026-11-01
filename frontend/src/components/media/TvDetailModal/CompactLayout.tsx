import { sheetBase } from '@/utils/posterColors';
import { META_ROW, OVERVIEW_CLAMP, OVERVIEW_TEXT, OVERVIEW_WRAP, PILL, ReservedRows, ratingsExpectedAtTap } from '../sheetRows';
import { APP_BG } from '@/theme/motion';
import { SheetFrame, SheetCard, SeedLayer, useSeededHero, SEEDED_WASH, SHELL_CROSSFADE } from '@/components/layout/SheetFrame';
import { CastPhoto } from '@/components/media/CastPhoto';
import { REVEALED_EVENT, tapShell } from '@/services/modalPrefetch';
import { isMinimal } from '@/minimal';
import { useTitleReveal, logoInput } from '@/hooks/useTitleReveal';
import { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import {
  PERSON_FALLBACK_SVG,
  getCastImageUrl,
  type CastMember,
} from '@/utils/modalParsers';
import type {
  TvSeason,
  TvEpisode,
  OwnershipMap,
} from '@/services/sonarrApi';
import { autoSearchSeason } from '@/services/sonarrApi';
import { isSeasonMonitored } from './tvSourcesUtils';
import { UltraBlurOverlay } from '../UltraBlurOverlay';
import { useOneReveal } from '@/hooks/useOneReveal';
import { BackdropHero } from '../BackdropHero';
import { useBackdropEffects } from '@/hooks/useBackdropEffects';
import { useSwipeToDismiss } from '@/hooks/useSwipeToDismiss';
import { AddToSonarrModal } from './AddToSonarrModal';
import { TvSourcesPanel } from './TvSourcesPanel';
import { EpisodeHeader } from './EpisodeHeader';
import { MobilePlaybackSheet } from '../MovieDetailModal/MobilePlaybackSheet';
import { useModalHost } from '@/contexts/ModalHostContext';
import type { PlexSeason } from '@/services/playbackApi';
import { posterUrl, finalBackdropUrl } from '@/utils/image';
import { useSheetHero, HERO_FADE } from '@/hooks/useSheetBackdrop';
import { reducedMotion } from '@/theme/motion';
import { RATINGS_SLOT_SM } from '@/components/media/RatingsBar';
import { useImdbRatingsState, ratingsDone } from '@/hooks/useImdbRatings';
import { useRatingsSettle } from '@/hooks/useRatingsSettle';
import { RatingsSettle } from '@/components/media/RatingsSettle';
import type { TvModalViewProps } from './types';
import { useAuth } from '@/contexts/AuthContext';
import { seasonTitle, seasonEyebrow, seasonShortLabel, sourcesTargetName } from './seasonLabels';
import {
  SonarrShowStatus, SonarrSeasonNote, SonarrSeasonLine, SonarrEpisodeNote, SonarrEpisodeBadges, SonarrSeasonBadges,
  useSonarrStatusData, sonarrEpisode, sonarrSeason, seasonInFlight, isInFlight, orbWithSonarr,
} from './SonarrStatus';
import { apiUrl, withBase } from '@/urlHelpers';

function resolveEpisodeThumbnail(ep: TvEpisode): string | null {
  if (ep.still_path) {
    // Prefer bare TMDb path via posterUrl (CDN). Always encode when proxying.
    const direct = posterUrl(ep.still_path, 'w500');
    if (direct) return direct;
    return apiUrl(`/api/img/tmdb?path=${encodeURIComponent(ep.still_path)}&size=w500`);
  }
  return null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function episodeKey(season: number, episode: number): string {
  return `s${season}e${episode}`;
}

function computeSeasonOwnership(
  seasons: TvSeason[],
  ownership: OwnershipMap,
): Record<number, { owned: number; total: number }> {
  const result: Record<number, { owned: number; total: number }> = {};
  for (const season of seasons) {
    let owned = 0;
    if (season.episodes.length > 0) {
      for (const ep of season.episodes) {
        const key = episodeKey(season.season_number, ep.episode_number);
        if (ownership[key]?.owned) owned++;
      }
      result[season.season_number] = { owned, total: season.episodes.length };
    } else {
      // Light seasons (no episode rows yet): count ownership keys + TMDb count
      const prefix = `s${season.season_number}e`;
      for (const key of Object.keys(ownership)) {
        if (key.startsWith(prefix) && ownership[key]?.owned) owned++;
      }
      result[season.season_number] = {
        owned,
        total: season.episode_count || owned,
      };
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// ActionButton — circular icon button with label underneath
// ---------------------------------------------------------------------------

function ActionButton({
  icon,
  label,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  const [pressed, setPressed] = useState(false);
  const runPress = useCallback(() => {
    setPressed(true);
    setTimeout(() => setPressed(false), 180);
  }, []);
  return (
    <div
      onClick={() => { runPress(); onClick(); }}
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: '6px',
        cursor: 'pointer',
        transform: pressed ? 'scale(0.88)' : 'scale(1)',
        transition: 'transform 0.18s cubic-bezier(0.34, 1.4, 0.64, 1)',
      }}
    >
      <div
        style={{
          width: '44px',
          height: '44px',
          borderRadius: '50%',
          background: 'rgba(255, 255, 255, 0.18)',
          border: '1px solid rgba(255, 255, 255, 0.15)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#fff',
        }}
      >
        {icon}
      </div>
      <span
        style={{
          fontSize: '12px',
          color: 'rgba(255, 255, 255, 0.9)',
          fontWeight: 500,
        }}
      >
        {label}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function CompactLayout(props: TvModalViewProps) {
  const { user } = useAuth();
  const isOwner = !!user?.is_admin;
  // What the tapped card knew about ownership (null: unknown), read once at mount: the seed reserves from it.
  const seedOwned = useRef(tapShell()?.owned ?? null).current;
  const {
    show,
    seasons,
    ownership,
    showOwned,
    sonarrStatus,
    loading,
    error,
    ultraBlurColors: detailColors,
    selectedSeason,
    setSelectedSeason,
    phase,
    onClose,
    showAddToSonarr,
    setShowAddToSonarr,
    onPersonSearch,
    grabToast,
    watchlistToast,
    onSonarrAdded,
    onSourcesGrabbed,
    tvdbId,
    resolvedTmdbId,
    showSources,
    setShowSources,
    showPlayback,
    setShowPlayback,
    // Episode mode
    episodeContext,
    episodeData,
    episodeLoading,
    showFullShow,
    setShowFullShow,
    onEpisodeClick,
    // Playback
    playbackRatingKey,
    playbackTitle,
    onPlayEpisode,
    // Season grid + shuffle
    plexSeasons,
    shuffleTarget,
    setShuffleTarget,
  } = props;
  // The tap-time seed only stands in until the details load; after that the details (or the ownership lookup) decide,
  // so a stale card orb can't keep Watch in place of Add.
  const showOwnedNow = (loading || !show) ? (showOwned || seedOwned === true) : (showOwned || !!show.isOwned);
  const titleState = useTitleReveal(logoInput(show), show?.title, show?.backdropUrl || show?.posterUrl);
  // Phone: the sheet stays off-screen until it can show fully composed
  // (details, backdrop, logo and colors, or the 600 ms cap), then slides in.
  const composed = !!error || (!loading && !!show && titleState !== 'hold');
  // Past the open budget the sheet starts on the tapped card's own art, colors and title (useSeededHero).
  const hero = useSeededHero(composed, detailColors);
  const heroHold = hero.seeded ? false : titleState === 'hold';
  // ONE backdrop per open: the final (sharp, text-free when picked) image, latched once decoded and settled or at
  // the cap, never swapped (useSheetBackdrop).
  // The hero (backdrop + title) is ONE unit: logo vs. text and the backdrop are decided together before the reveal
  // (1.2s cap from the tap), fade in once together, and never change for that open.
  const sheetHero = useSheetHero(finalBackdropUrl(show?.backdropTextlessUrl ?? show?.backdropUrl), !!show && !show.backdropPending, show?.clearLogoUrl ? withBase(show.clearLogoUrl) : null, logoInput(show) !== undefined, props.openKey, !!show);
  const sheetArt = sheetHero.art;
  const posterFill = show && !show.backdropUrl && !sheetArt ? show.posterUrl : null;
  const ultraBlurColors = hero.colors;
  const sheetBg = sheetBase(ultraBlurColors) ?? APP_BG;
  useEffect(() => {
    if (composed) window.dispatchEvent(new Event(REVEALED_EVENT));
  }, [composed]);
  // Ratings (shared with ExpandedLayout) arrive after the details: as on the movie
  // sheet, keep their row reserved once the sheet shows so nothing below moves.
  const ratingsState = useImdbRatingsState(show?.title, show?.year, show?.ratings);
  const ratingsLoading = ratingsState.loading;
  // The row settles once and fades in together (useRatingsSettle); a later answer is dropped for this open.
  const ratingsSettle = useRatingsSettle(ratingsState.ratings, !!show && ratingsDone(ratingsState, show?.title), props.openKey, !!show);
  const hasRatings = !!ratingsSettle.ratings;
  // ONE reveal: backdrop, title/logo, meta, ratings and buttons together, in one fade (useOneReveal).
  const reveal = useOneReveal(props.openKey, !loading && !error && !!show, sheetHero.state !== 'hold', ratingsSettle.shown,
    sheetHero.deadline, false, false, true, sheetHero.artDeadline);
    // Known at tap time: outside minimal builds the ratings row has a source (OMDb/RT); the seed reserves it and the
  // row keeps that box, so a late ratings fetch never moves the rows below.
  const ratingsExpected = useRef(ratingsExpectedAtTap(false)).current;
  const [reserveRatings, setReserveRatings] = useState(ratingsExpected);
  useEffect(() => { setReserveRatings(ratingsExpected); }, [show?.title]);
  useEffect(() => {
    if (composed && (ratingsLoading || hasRatings)) setReserveRatings(true);
  }, [composed, ratingsLoading, hasRatings]);

  // Determine if we're in active episode-mode display
  // isEpisodeIntent: episodeContext is set → hide show content immediately
  // inEpisodeMode: data has loaded → render EpisodeHeader
  const isEpisodeIntent = !showFullShow && !!episodeContext;
  const inEpisodeMode = isEpisodeIntent && !!episodeData && !episodeLoading;

  const [plotExpanded, setPlotExpanded] = useState(false);
  const [autoSearchLoading, setAutoSearchLoading] = useState(false);
  const [autoSearchToast, setAutoSearchToast] = useState<{ msg: string; ok: boolean } | null>(null);
  // Season action sheet: which season number has the bottom sheet open
  const [seasonActionSheet, setSeasonActionSheet] = useState<number | null>(null);
  // Return-to-sheet stack: when a child sheet closes, re-open the parent
  const [returnToSeasonAction, setReturnToSeasonAction] = useState<number | null>(null);
  // Season detail mode: when set, the modal shows season-specific content
  const [seasonDetailMode, setSeasonDetailMode] = useState<number | null>(null);
  // Seasons shelf + episodes sheet
  const [showSeasonsSheet, setShowSeasonsSheet] = useState(false);
  const [showEpisodesSheet, setShowEpisodesSheet] = useState(false);
  const modalHost = useModalHost();
  const { registerBackHandler } = modalHost;
  const scrollRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);

  // System/gesture back while a nested sheet is open -> close that sheet only
  // (keep the TV/series modal). Same stack discipline as Movie Sources/Collection.
  useEffect(() => {
    if (showSeasonsSheet) {
      registerBackHandler(() => {
        setShowSeasonsSheet(false);
        return true;
      });
      return () => registerBackHandler(null);
    }
    if (showEpisodesSheet) {
      registerBackHandler(() => {
        setShowEpisodesSheet(false);
        if (!showFullShow && episodeContext) {
          setSeasonDetailMode(null);
        }
        return true;
      });
      return () => registerBackHandler(null);
    }
    if (seasonActionSheet !== null) {
      registerBackHandler(() => {
        setSeasonActionSheet(null);
        return true;
      });
      return () => registerBackHandler(null);
    }
    if (!showSources) {
      registerBackHandler(null);
      return;
    }
    registerBackHandler(() => {
      setShowSources(null);
      return true;
    });
    return () => registerBackHandler(null);
  }, [
    showSeasonsSheet,
    showEpisodesSheet,
    seasonActionSheet,
    showSources,
    setShowSources,
    showFullShow,
    episodeContext,
    registerBackHandler,
  ]);

  // Swipe-to-dismiss: drag down from top to close modal
  const { handlers: swipeHandlers } = useSwipeToDismiss({
    scrollRef,
    onDismiss: onClose,
    enabled: phase === 'visible' && !showSources && !showPlayback && !showAddToSonarr && !showSeasonsSheet && !showEpisodesSheet && seasonActionSheet === null,
  });

  // Track which episodes/seasons have been sent to Sonarr (hides search icons)
  const [, setSentToSonarr] = useState<Set<string>>(new Set());

  // Show-level add confirmation. The parent flips `watchlistToast` true only on
  // a successful Sonarr add, so mirror it into a persistent session flag to flip
  // the primary action to an "In watchlist" state immediately (optimistic, and
  // it survives the 2s toast timeout until `sonarrStatus.exists` catches up).
  const [showAddedThisSession, setShowAddedThisSession] = useState(false);
  useEffect(() => {
    if (watchlistToast) setShowAddedThisSession(true);
  }, [watchlistToast]);

  // Auto-search: season — spinner → toast → hide icon
  const handleAutoSearchSeason = useCallback(() => {
    if (!tvdbId || selectedSeason < 0 || autoSearchLoading) return;
    setAutoSearchLoading(true);
    autoSearchSeason(tvdbId, selectedSeason, resolvedTmdbId ?? undefined).catch(() => {});
    // Show spinner briefly, then toast, then hide icon
    setTimeout(() => {
      setAutoSearchLoading(false);
      setAutoSearchToast({ msg: 'Sent to Sonarr', ok: true });
      setSentToSonarr(prev => { const next = new Set(prev); next.add(`season-${selectedSeason}`); return next; });
      setTimeout(() => setAutoSearchToast(null), 1200);
    }, 800);
  }, [tvdbId, selectedSeason, resolvedTmdbId, autoSearchLoading]);

  // Seasons the user just added (optimistic — reflects "monitored" instantly).
  const [addedSeasons, setAddedSeasons] = useState<Set<number>>(new Set());

  // Add a single season as monitored + immediately search Sonarr for it.
  const handleAddSeasonToWatchlist = useCallback((season: number) => {
    if (!tvdbId) return;
    setAddedSeasons(prev => { const next = new Set(prev); next.add(season); return next; });
    autoSearchSeason(tvdbId, season, resolvedTmdbId ?? undefined)
      .then(() => onSonarrAdded?.())
      .catch(() => {});
    setAutoSearchToast({ msg: `Season ${season} added to Sonarr`, ok: true });
    setTimeout(() => setAutoSearchToast(null), 1500);
  }, [tvdbId, resolvedTmdbId, onSonarrAdded]);


  // Plex-like backdrop effects: crop/zoom header + UltraBlur color bleed
  const backdropEffects = useBackdropEffects(show?.backdropUrl, ultraBlurColors);



  // Parallax scroll handler (backdrop: translate up + fade out)
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    let rafId = 0;
    const handleScroll = () => {
      cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        const bd = backdropRef.current;
        if (bd) {
          const scrollY = el.scrollTop;
          bd.style.transform = `translate3d(0, ${-Math.min(scrollY * 0.4, 250)}px, 0)`;
          bd.style.opacity = String(Math.max(0, 1 - scrollY / 300));
        }
      });
    };
    el.addEventListener('scroll', handleScroll, { passive: true });
    return () => {
      el.removeEventListener('scroll', handleScroll);
      cancelAnimationFrame(rafId);
    };
  }, []);


  // -----------------------------------------------------------------------
  // Derived values
  // -----------------------------------------------------------------------
  const creatorsForDisplay = show?.creators ?? [];
  const seasonOwnership = computeSeasonOwnership(seasons, ownership);

  // Episode modal: episode cast/guest stars. Series modal: creators + series cast.
  const episodeCast = (inEpisodeMode && episodeData?.cast && episodeData.cast.length > 0)
    ? episodeData.cast
    : null;
  const rawCast = episodeCast ?? (show?.cast.slice(0, 20) ?? []);
  const allCastForDisplay: CastMember[] = episodeCast
    ? rawCast
    : [
        ...creatorsForDisplay.map((c) => ({
          name: c.name,
          character: 'Creator',
          role: 'Creator',
          thumb: c.thumb ?? undefined,
          profilePath: c.profilePath ?? undefined,
          tmdbPersonId: c.tmdbPersonId,
        })),
        ...rawCast,
      ];

  const seasonsCountDisplay = show?.numberOfSeasons
    ? `${show.numberOfSeasons} Season${show.numberOfSeasons !== 1 ? 's' : ''}`
    : seasons.length > 0
      ? `${seasons.length} Season${seasons.length !== 1 ? 's' : ''}`
      : '';

  // Season detail mode derived values
  const activeSeasonData = seasonDetailMode != null
    ? seasons.find(s => s.season_number === seasonDetailMode) ?? null
    : null;
  const activeSeasonPlexData = activeSeasonData
    ? (plexSeasons ?? []).find(ps => ps.index === seasonDetailMode)
    : null;
  const seasonDetailIsOwned = !!activeSeasonPlexData?.ratingKey;

  // Build meta info items (joined with . separators)
  const metaItems: string[] = [];
  if (activeSeasonData) {
    // Season detail mode: season-specific meta
    if (activeSeasonData.air_date) metaItems.push(String(new Date(activeSeasonData.air_date + 'T00:00:00').getFullYear()));
    metaItems.push(`${activeSeasonData.episode_count} Episode${activeSeasonData.episode_count !== 1 ? 's' : ''}`);
    if (show?.network) metaItems.push(show.network);
  } else {
    if (show?.year) metaItems.push(String(show.year));
    if (seasonsCountDisplay) metaItems.push(seasonsCountDisplay);
    if (show?.network) metaItems.push(show.network);
    if (show?.contentRating) metaItems.push(show.contentRating);
  }

  // -----------------------------------------------------------------------
  // Animation styles
  // -----------------------------------------------------------------------

  // The seed shell (and the details' crossfade over it) only if the sheet had to start before it was composed
  // (open cap): a composed start draws the final hero from frame 0 with nothing fading (Designer F1).
  const go = hero.go;
  const shellShown = useRef(false);
  if (loading && go) shellShown.current = true;
  const detailsIn = !loading && !error && !!show;
  const washEl = (
<>{/* UltraBlur — scrolls with content, slides up over backdrop */}
                {ultraBlurColors && (
                  <div data-hero-keep=""
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
                      ...(hero.seeded ? SEEDED_WASH : {}),
                    }}
                  >
                    <UltraBlurOverlay hold={heroHold} instant colors={ultraBlurColors} variant="mobile" animationDelay={300} />
                  </div>
                )}</>
  );
  const titleEl = (<>
{/* Clearlogo / Title */}
                <div data-hero-keep="" data-title-box=""
                  style={{
                    position: 'relative',
                    margin: '0 auto 24px',
                    maxWidth: '700px',
                    width: '95%',
                    height: '140px',
                    display: 'flex',
                    alignItems: 'flex-end',
                    justifyContent: 'center',
                    overflow: 'visible',
                  }}
                >
                  <div data-hero-title={sheetHero.state} style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
                    opacity: sheetHero.state === 'hold' || !reveal.revealed ? 0 : 1, transition: reducedMotion() ? 'none' : HERO_FADE }}>
                  {sheetHero.state === 'logo' && sheetHero.logo ? (
                    <img data-hero-logo="" src={sheetHero.logo} onError={sheetHero.logoFailed} alt={show?.title}
                        style={{ maxWidth: 'calc(100% - 32px)', maxHeight: '200px', objectFit: 'contain', filter: 'drop-shadow(0 2px 8px rgba(0,0,0,0.5))' }} />
                  ) : (
                  <h1
                    style={{
                      fontSize: '28px',
                      fontWeight: 700,
                      color: '#fff',
                      lineHeight: 1.2,
                      margin: 0,
                      textShadow: '0 2px 8px rgba(0,0,0,0.5)',
                    }}
                  >
                    {hero.seeded ? hero.title : show?.title}
                  </h1>
                  )}
                  </div>
                </div>
  </>);
  // Enter/exit motion, scrim, scroll lock and the app background live in SheetFrame (MOTION.md §1).
  if (phase === 'gone') return null;

  return (
    <SheetFrame name="TvDetailModal" phase={phase} composed={composed} go={go} onScrimClick={onClose}>
      <SheetCard background={sheetBg}>
        {/* Scrollable content */}
        <div
          ref={scrollRef}
          data-scroll-container
          className="luma-scroll-y"
          onTouchStart={swipeHandlers.onTouchStart}
          onTouchMove={swipeHandlers.onTouchMove}
          onTouchEnd={swipeHandlers.onTouchEnd}
          onTouchCancel={swipeHandlers.onTouchCancel}
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
          {/* Loading */}
          <SeedLayer show={shellShown.current} ready={hero.seeded ? detailsIn : composed} overlap={220} hero={hero.seeded ? {
            art: <BackdropHero backdropUrl={sheetArt} posterUrl={null} effects={backdropEffects} phase={phase} instant />,
            wash: washEl, title: titleEl } : undefined} rows={
            <ReservedRows pillKept={seedOwned !== false} ratingsH={ratingsExpected ? RATINGS_SLOT_SM : 0} actions={
              <div data-modal-actions="" style={{ display: 'flex', justifyContent: 'center', gap: '18px', padding: '20px 16px' }}>
                <span data-modal-keep="" style={{ display: 'contents' }}><ActionButton icon={<svg width="18" height="18" aria-hidden="true" />} label={'\u00a0'} onClick={() => {}} /></span>
              </div>} />} />

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
              <p style={{ fontSize: '16px', color: 'rgba(255,255,255,0.8)' }}>
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
          {detailsIn && (
            <>
              {/* Backdrop — Plex-like cover-cropped header via BackdropHero */}
              <BackdropHero
                backdropUrl={sheetArt}
                posterUrl={posterFill}
                effects={backdropEffects}
                phase={phase}
                containerRef={backdropRef}
                hold={!reveal.revealed} instant
              />

              {/* === CENTERED CONTENT AREA === */}
              <div data-reveal={titleState !== 'hold' ? 'shown' : 'hold'} data-reveal-gate={reveal.revealed ? 'shown' : 'hold'} style={{
                position: 'relative',
                zIndex: 3,
                textAlign: 'center',
                marginTop: '-220px',
                // The sheet is opaque from its first frame; only the title waits for the logo (titleTextStyle).
                // Details that replace the seed shell crossfade in.
                animation: hero.seeded || !shellShown.current ? 'none' : SHELL_CROSSFADE,
              }}>
                {washEl}
                {titleEl}

                {/* Season detail: season name (back handled by top-left chevron).
                    Hidden in episode mode so opening Episodes sheet (which sets
                    seasonDetailMode) does not alter episode chrome underneath. */}
                {activeSeasonData && !isEpisodeIntent && (
                  <div style={{ textAlign: 'center', marginTop: '6px', marginBottom: '2px' }}>
                    {seasonEyebrow(activeSeasonData.name, seasonDetailMode!) && (
                      <p data-season-eyebrow style={{ fontSize: '12px', fontWeight: 600, color: 'rgba(255,255,255,0.55)', margin: '0 0 3px', textShadow: '0 1px 4px rgba(0,0,0,0.5)' }}>
                        {seasonEyebrow(activeSeasonData.name, seasonDetailMode!)}
                      </p>
                    )}
                    <p style={{ fontSize: '17px', fontWeight: 600, color: 'rgba(255,255,255,0.9)', margin: 0, textShadow: '0 1px 4px rgba(0,0,0,0.5)' }}>
                      {seasonTitle(activeSeasonData.name, seasonDetailMode!)}
                    </p>
                  </div>
                )}

                {/* === Stage 3: Details — individually staggered cascade === */}
                <div style={{ position: 'relative', zIndex: 1 }}>

                {/* === EPISODE MODE — loading spinner while fetching === */}
                {isEpisodeIntent && !inEpisodeMode && (
                  <div style={{ display: 'flex', justifyContent: 'center', padding: '40px 0' }}>
                    <div
                      style={{
                        width: '28px',
                        height: '28px',
                        borderRadius: '50%',
                        border: '2px solid rgba(255,255,255,0.15)',
                        borderTopColor: 'rgba(255,255,255,0.7)',
                        animation: 'mobileTvModalSpin 0.8s linear infinite',
                      }}
                    />
                  </div>
                )}

                {/* === EPISODE MODE HEADER === */}
                {inEpisodeMode && episodeData && (
                  <EpisodeHeader
                    episodeData={episodeData}
                    size="sm"
                    onPlay={episodeData.isOwned && episodeContext?.episodeRatingKey
                      ? () => onPlayEpisode?.(
                          episodeContext!.episodeRatingKey!,
                          `${show?.title ?? ''} - S${episodeData.seasonNumber}E${episodeData.episodeNumber} - ${episodeData.title}`,
                        )
                      : undefined}
                    onViewShow={() => {
                      setSeasonDetailMode(null);
                      setSelectedSeason(-1);
                      setPlotExpanded(false);
                      setShowFullShow(true);
                    }}
                    onOpenSeasons={() => setShowSeasonsSheet(true)}
                    onOpenEpisodes={() => {
                      const sn = episodeData?.seasonNumber ?? episodeContext?.seasonNumber;
                      if (sn == null) return;
                      // Stay in episode mode under the sheet; dismiss returns to episode.
                      setSeasonDetailMode(sn);
                      setSelectedSeason(sn);
                      setShowEpisodesSheet(true);
                    }}
                    onOpenSources={isOwner
                      ? () => setShowSources({
                          type: 'episode',
                          season: episodeData.seasonNumber,
                          episode: episodeData.episodeNumber,
                        })
                      : undefined}
                  />
                )}

                {/* === SHOW MODE — meta, rating, actions (hidden in episode mode) === */}
                {!isEpisodeIntent && (
                  <>
                    {/* Meta info line: year . seasons . network . content_rating */}
                    {metaItems.length > 0 && (
                      <div data-box="meta"
                        style={{
                          ...META_ROW,
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
                              <span style={{ color: 'rgba(255, 255, 255, 0.5)' }}>
                                •
                              </span>
                            )}
                            <span>{item}</span>
                          </span>
                        ))}
                      </div>
                    )}

                    {/* Ratings bar — centered icons (same slot as the movie sheet) */}
                    <RatingsSettle settle={ratingsSettle} size="sm" reserve={reserveRatings ? RATINGS_SLOT_SM : 0} />

                    {activeSeasonData ? (
                      <div style={{ marginTop: '10px' }}>
                        <SonarrSeasonLine
                          season={seasonDetailMode!}
                          have={seasonOwnership[seasonDetailMode!]?.owned ?? 0}
                          total={seasonOwnership[seasonDetailMode!]?.total ?? 0}
                        />
                      </div>
                    ) : (
                      <SonarrShowStatus fontSize={13} />
                    )}

                    {/* Primary action area */}
                    <div data-modal-actions>
                    {/* Ownership the seed couldn't know: minimal builds show only the owned Watch pill, so its box stays
                        reserved (empty) for the sheet's life unless Watch lands in it; the rows below never move. */}
                    {isMinimal() && seedOwned === null && !activeSeasonData && !showOwnedNow && (
                      <div data-box="watchlist" data-modal-keep="" aria-hidden="true" style={{ ...PILL, visibility: 'hidden' }}>
                        <svg width="16" height="16" aria-hidden="true" /><span>{'\u00a0'}</span>
                      </div>
                    )}
                    {(activeSeasonData ? seasonDetailIsOwned : showOwnedNow) ? (
                      /* Watch button — full-width white pill */
                      <button data-box="watchlist"
                        type="button"
                        data-modal-keep=""
                        data-owned-watch=""
                        onClick={() => {
                          if (activeSeasonData && activeSeasonPlexData?.ratingKey) {
                            onPlayEpisode?.(activeSeasonPlexData.ratingKey, `${show?.title ?? 'TV show'} – ${activeSeasonData.name || `Season ${seasonDetailMode}`}`);
                          } else if (show?.ratingKey) {
                            onPlayEpisode?.(show.ratingKey, show?.title ?? 'TV show');
                          }
                        }}
                        style={{
                          ...PILL,
                          background: '#ffffff',
                          color: '#000000',
                          border: 'none',
                          cursor: 'pointer',
                        }}
                      >
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                          <polygon points="6,3 20,12 6,21" />
                        </svg>
                        Watch
                      </button>
                    ) : (activeSeasonData && (addedSeasons.has(seasonDetailMode!) || isSeasonMonitored(sonarrStatus, seasonDetailMode!))) ? (
                      /* Season already monitored — show status, not an action */
                      <div data-box="watchlist"
                        style={{
                          ...PILL,
                          // the 1px border sits inside the pill's box (padding - 1px), same 52px as every pill
                          padding: '13px 19px',
                          background: 'rgba(74,222,128,0.12)',
                          color: '#4ade80',
                          border: '1px solid rgba(74,222,128,0.35)',
                        }}
                      >
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                        Monitoring Season
                      </div>
                    ) : (!activeSeasonData && (sonarrStatus?.exists || showAddedThisSession)) ? (
                      /* Show already in the watchlist (Sonarr) — status, not an action */
                      <div data-box="watchlist"
                        style={{
                          ...PILL,
                          // the 1px border sits inside the pill's box (padding - 1px), same 52px as every pill
                          padding: '13px 19px',
                          background: 'rgba(74,222,128,0.12)',
                          color: '#4ade80',
                          border: '1px solid rgba(74,222,128,0.35)',
                        }}
                      >
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                        In watchlist
                      </div>
                    ) : (
                      /* Add to watchlist: full-width #3b82f6 primary button (v158a; was the white pill).
                         Show-level add needs a resolved tvdbId (both the
                         AddToSonarr modal and the fast-add path are keyed on it),
                         so disable it until tvdbId lands to avoid a no-op tap. */
                      <button data-box="watchlist"
                        type="button"
                        data-testid="tv-add-watchlist"
                        disabled={!activeSeasonData && !tvdbId}
                        onClick={() => {
                          if (activeSeasonData) {
                            handleAddSeasonToWatchlist(seasonDetailMode!);
                          } else if (tvdbId) {
                            setShowAddToSonarr(true);
                          }
                        }}
                        style={{
                          ...PILL,
                          background: '#3b82f6',
                          color: '#ffffff',
                          borderRadius: '12px',
                          cursor: (!activeSeasonData && !tvdbId) ? 'default' : 'pointer',
                          opacity: (!activeSeasonData && !tvdbId) ? 0.6 : 1,
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
                        {activeSeasonData ? 'Add season to watchlist' : 'Add to watchlist'}
                      </button>
                    )}
                    </div>{/* end action stagger wrapper */}

                    {/* Action buttons row — circular icons */}
                    <div
                      data-modal-actions
                      style={{
                        display: 'flex',
                        justifyContent: 'center',
                        gap: '18px',
                        padding: '20px 16px',
                      }}
                    >

                      {/* Sources (owner) */}
                      {isOwner && (
                        <span data-modal-sources="" style={{ display: 'contents' }}>
                        <ActionButton
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
                        onClick={() => {
                          const season = activeSeasonData
                            ? seasonDetailMode!
                            : selectedSeason >= 0 ? selectedSeason : (seasons[0]?.season_number ?? 1);
                          setShowSources({ type: 'season', season });
                        }}
                      />
                        </span>
                      )}

                      {/* Seasons / Episodes toggle — show as soon as we know
                          the show has seasons (numberOfSeasons from show detail
                          or light seasons summary). Do not wait on full episode
                          ownership / library grid. */}
                      {/* v217: Episodes / Seasons show in minimal builds too, as prod 1b4ea74. */}
                      {(seasons.length > 0 || (show?.numberOfSeasons ?? 0) > 0) && (
                        <span data-modal-keep="" style={{ display: 'contents' }}>{activeSeasonData ? (
                          <ActionButton
                            icon={
                              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <rect x="2" y="3" width="20" height="4" rx="1" />
                                <rect x="2" y="10" width="20" height="4" rx="1" />
                                <rect x="2" y="17" width="20" height="4" rx="1" />
                              </svg>
                            }
                            label="Episodes"
                            onClick={() => setShowEpisodesSheet(true)}
                          />
                        ) : (
                          <ActionButton
                            icon={
                              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <rect x="2" y="3" width="20" height="4" rx="1" />
                                <rect x="2" y="10" width="20" height="4" rx="1" />
                                <rect x="2" y="17" width="20" height="4" rx="1" />
                              </svg>
                            }
                            label="Seasons"
                            onClick={() => setShowSeasonsSheet(true)}
                          />
                        )}</span>
                      )}

                      {/* Shuffle — season-level in season detail mode, show-level otherwise */}
                      {/* v219: data-modal-keep keeps prod 1b4ea74's Shuffle in minimal builds (playback is on). */}
                      <span data-modal-keep="" data-tv-shuffle="" style={{ display: 'contents' }}>
                      {setShuffleTarget && (
                        activeSeasonData
                          ? (seasonDetailIsOwned && activeSeasonPlexData?.ratingKey && (
                              <ActionButton
                                icon={
                                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                    <polyline points="16 3 21 3 21 8" />
                                    <line x1="4" y1="20" x2="21" y2="3" />
                                    <polyline points="21 16 21 21 16 21" />
                                    <line x1="15" y1="15" x2="21" y2="21" />
                                    <line x1="4" y1="4" x2="9" y2="9" />
                                  </svg>
                                }
                                label="Shuffle"
                                onClick={() => setShuffleTarget!({ ratingKey: activeSeasonPlexData.ratingKey, label: `Shuffling ${activeSeasonData.name || `Season ${seasonDetailMode}`}...` })}
                              />
                            ))
                          : (showOwnedNow && show?.ratingKey && (
                              <ActionButton
                                icon={
                                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                    <polyline points="16 3 21 3 21 8" />
                                    <line x1="4" y1="20" x2="21" y2="3" />
                                    <polyline points="21 16 21 21 16 21" />
                                    <line x1="15" y1="15" x2="21" y2="21" />
                                    <line x1="4" y1="4" x2="9" y2="9" />
                                  </svg>
                                }
                                label="Shuffle"
                                onClick={() => setShuffleTarget!({ ratingKey: show.ratingKey!, label: `Shuffling ${show.title}...` })}
                              />
                            ))
                      )}
                      </span>

                      {/* Series — jump from season detail back to show-level modal */}
                      {activeSeasonData && (
                        <ActionButton
                          icon={
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
                              <line x1="8" y1="21" x2="16" y2="21" />
                              <line x1="12" y1="17" x2="12" y2="21" />
                            </svg>
                          }
                          label="Series"
                          onClick={() => setSeasonDetailMode(null)}
                        />
                      )}
                    </div>
                  </>
                )}

                {/* Plot summary — season overview in season detail mode, show overview otherwise */}
                {!isEpisodeIntent && (activeSeasonData?.overview || show.summary) && (
                  <div style={OVERVIEW_WRAP}>
                    <p data-box="overview"
                      onClick={() => setPlotExpanded((v) => !v)}
                      style={{
                        ...OVERVIEW_TEXT,
                        color: 'rgba(255, 255, 255, 0.9)',
                        cursor: 'pointer',
                        ...(!plotExpanded ? OVERVIEW_CLAMP : {}),
                      }}
                    >
                      {activeSeasonData?.overview || show.summary}
                    </p>
                    {!plotExpanded && (activeSeasonData?.overview || show.summary || '').length > 150 && (
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

                {/* Seasons & Episodes moved to sheet — opened via "Seasons" action button */}

                {/* Cast & Crew — horizontal scroll with circular photos */}
                {/* Creators appear first, then cast */}
                {allCastForDisplay.length > 0 && (
                  <div style={{ marginBottom: '20px', textAlign: 'left' }}>
                    <h2
                      style={{
                        fontSize: '18px',
                        fontWeight: 600,
                        color: '#fff',
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

                {/* Bottom spacing — extends past safe area */}
                <div
                  style={{
                    height: 'calc(var(--luma-end-pad) + 40px)',
                  }}
                />

                </div>{/* end stage 3 details wrapper */}
              </div>
            </>
          )}
        </div>

        {/* Close button — bare white X, fixed top-left */}
        <button
          type="button"
          onClick={onClose}
          style={{
            position: 'absolute',
            top: '8px',
            left: '15px',
            zIndex: 20,
            background: 'none',
            border: 'none',
            padding: '8px',
            color: '#fff',
            cursor: 'pointer',
            filter: 'drop-shadow(0 1px 4px rgba(0,0,0,0.6))',
          }}
          aria-label="Close"
        >
          <svg
            width="28"
            height="28"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </SheetCard>

      {/* Playback sheet — normal playback */}
      {showPlayback && playbackRatingKey && !shuffleTarget && (
        <MobilePlaybackSheet
          ratingKey={playbackRatingKey}
          title={playbackTitle ?? show?.title ?? ''}
          onClose={() => setShowPlayback(false)}
        />
      )}

      {/* Playback sheet — shuffle mode */}
      {shuffleTarget && (
        <MobilePlaybackSheet
          ratingKey={shuffleTarget.ratingKey}
          title={show?.title ?? ''}
          shuffle
          shuffleLabel={shuffleTarget.label}
          onClose={() => {
            setShuffleTarget?.(null);
            if (returnToSeasonAction !== null) {
              setSeasonActionSheet(returnToSeasonAction);
              setReturnToSeasonAction(null);
            }
          }}
        />
      )}

      {/* Add to Sonarr modal */}
      {showAddToSonarr && tvdbId && show && (
        <AddToSonarrModal
          tvdbId={tvdbId}
          title={show.title}
          seasons={seasons}
          onClose={() => setShowAddToSonarr(false)}
          onAdded={() => { setShowAddToSonarr(false); onSonarrAdded?.(); }}
        />
      )}

      {/* Sources panel — bottom sheet */}
      {showSources && (tvdbId || show?.title) && (
        <TvSourcesPanel
          target={showSources}
          targetName={sourcesTargetName(showSources, seasons, episodeData)}
          tvdbId={tvdbId ?? 0}
          tmdbId={resolvedTmdbId}
          title={show?.title}
          year={show?.year ?? null}
          seedQuery={props.searchQuery}
          onClose={() => {
            setShowSources(null);
            if (returnToSeasonAction !== null) {
              setSeasonActionSheet(returnToSeasonAction);
              setReturnToSeasonAction(null);
            }
          }}
          onGrabbed={() => onSourcesGrabbed?.()}
        />
      )}

      {/* Season action sheet */}
      {seasonActionSheet !== null && (
        <SeasonActionSheet
          seasonNumber={seasonActionSheet}
          seasons={seasons}
          plexSeasons={plexSeasons ?? []}
          showOwned={showOwned}
          sonarrEnabled={!!sonarrStatus?.enabled}
          autoSearchLoading={autoSearchLoading}
          ownership={ownership}
          onShuffle={(ratingKey, label) => {
            setReturnToSeasonAction(seasonActionSheet);
            setSeasonActionSheet(null);
            setShuffleTarget?.({ ratingKey, label });
          }}
          onAutoSearch={() => {
            handleAutoSearchSeason();
            setSeasonActionSheet(null);
          }}
          onBrowseSources={isOwner ? () => {
            setReturnToSeasonAction(seasonActionSheet);
            setShowSources({ type: 'season', season: seasonActionSheet });
            setSeasonActionSheet(null);
          } : undefined}
          onEpisodeClick={(s, e) => {
            setSeasonActionSheet(null);
            onEpisodeClick?.(s, e);
          }}
          onClose={() => setSeasonActionSheet(null)}
          onEpisodeSources={isOwner ? (s, e) => setShowSources({ type: 'episode', season: s, episode: e }) : undefined}
        />
      )}

      {/* Seasons shelf */}
      {showSeasonsSheet && (
        <SeasonsShelf
          seasons={seasons}
          seasonOwnership={seasonOwnership}
          plexSeasons={plexSeasons ?? []}
          showPosterUrl={show?.posterUrl}
          onSelectSeason={(num) => {
            setShowSeasonsSheet(false);
            setShowFullShow(true);       // exit episode mode
            setSeasonDetailMode(num);
            setSelectedSeason(num);      // season page; Episodes action opens the list
          }}
          onSeasonSources={isOwner ? (num) => setShowSources({ type: 'season', season: num }) : undefined}
          onClose={() => setShowSeasonsSheet(false)}
        />
      )}

      {/* Episodes browsing sheet — opened from season detail mode */}
      {showEpisodesSheet && seasonDetailMode != null && (
        <EpisodesBrowsingSheet
          season={seasons.find(s => s.season_number === seasonDetailMode)!}
          seasonNumber={seasonDetailMode}
          ownership={ownership}
          sonarrEnabled={!!sonarrStatus?.enabled}
          onEpisodeClick={(s, e) => {
            setShowEpisodesSheet(false);
            // If still in episode mode, drop seasonDetailMode so chrome stays episode-only.
            if (!showFullShow && episodeContext) {
              setSeasonDetailMode(null);
            }
            onEpisodeClick?.(s, e);
          }}
          onEpisodeSources={isOwner ? (s, e) => setShowSources({ type: 'episode', season: s, episode: e }) : undefined}
          onClose={() => {
            setShowEpisodesSheet(false);
            if (!showFullShow && episodeContext) {
              setSeasonDetailMode(null);
            }
          }}
        />
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
            animation: 'mobileToastEnter var(--m-dur-standard) var(--m-ease-decel)',
          }}
        >
          Download started!
        </div>
      )}

      {/* Watchlist toast */}
      {watchlistToast && (
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
            animation: 'mobileToastEnter var(--m-dur-standard) var(--m-ease-decel)',
          }}
        >
          Added to watchlist
        </div>
      )}


      {/* Auto-search popup — portaled to body so it's always on top of action sheets */}
      {autoSearchToast && createPortal(
        <div className="luma-fullscreen luma-bleed"
          style={{
            position: 'fixed',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 10010,
            pointerEvents: 'none',
          }}
        >
          <div
            style={{
              background: '#1e1e1e',
              color: '#fff',
              padding: '20px 32px',
              borderRadius: '14px',
              fontSize: '16px',
              fontWeight: 600,
              boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
              animation: 'searchPopupFadeIn 0.2s ease-out',
            }}
          >
            {autoSearchToast.msg}
          </div>
        </div>,
        document.body,
      )}

      {/* Keyframes */}
      <style>{`
        @keyframes mobileTvModalSpin { to { transform: rotate(360deg); } }
        @keyframes searchPopupFadeIn { from { opacity: 0; transform: scale(0.95); } to { opacity: 1; transform: scale(1); } }
      `}</style>
    </SheetFrame>
  );
}

// ---------------------------------------------------------------------------
// SeasonActionSheet — bottom sheet with season actions
// ---------------------------------------------------------------------------

const SEASON_SHEET_DISMISS = 120;

function SeasonActionSheet({
  seasonNumber,
  seasons,
  plexSeasons,
  showOwned,
  sonarrEnabled,
  autoSearchLoading,
  ownership,
  onShuffle,
  onAutoSearch,
  onBrowseSources,
  onEpisodeClick,
  onClose,
  onEpisodeSources,
}: {
  seasonNumber: number;
  seasons: TvSeason[];
  plexSeasons: PlexSeason[];
  showOwned: boolean;
  sonarrEnabled: boolean;
  autoSearchLoading: boolean;
  ownership: OwnershipMap;
  onShuffle: (ratingKey: number, label: string) => void;
  onAutoSearch: () => void;
  onBrowseSources?: () => void;
  onEpisodeClick: (season: number, episode: number) => void;
  onClose: () => void;
  onEpisodeSources?: (season: number, episode: number) => void;
}) {
  const sonarrSt = useSonarrStatusData();
  const [entered, setEntered] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [dragOffset, setDragOffset] = useState(0);
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    requestAnimationFrame(() => setEntered(true));
  }, []);

  const handleDismiss = useCallback(() => {
    setDismissing(true);
    setTimeout(onClose, 250);
  }, [onClose]);

  // ESC to close
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && !document.querySelector('[data-sources-title],[data-show-monitor-sheet]')) { e.stopPropagation(); handleDismiss(); }
    }
    window.addEventListener('keydown', handleKey, true);
    return () => window.removeEventListener('keydown', handleKey, true);
  }, [handleDismiss]);

  // Drag-to-dismiss from grab handle only — never fight episode list scroll.
  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest('[data-sheet-handle]') && e.touches.length > 0) {
      dragRef.current = { active: true, startY: e.touches[0]!.clientY, currentY: e.touches[0]!.clientY };
    }
  }, []);

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragRef.current.active || e.touches.length === 0) return;
    const y = e.touches[0]!.clientY;
    const dy = y - dragRef.current.startY;
    if (dy > 0) { dragRef.current.currentY = y; setDragOffset(dy); }
  }, []);

  const onTouchEnd = useCallback(() => {
    if (!dragRef.current.active) return;
    const dy = dragRef.current.currentY - dragRef.current.startY;
    dragRef.current.active = false;
    if (dy > SEASON_SHEET_DISMISS) { handleDismiss(); } else { setDragOffset(0); }
  }, [handleDismiss]);

  const onTouchCancel = useCallback(() => {
    dragRef.current.active = false;
    setDragOffset(0);
  }, []);

  const season = seasons.find((s) => s.season_number === seasonNumber);
  const seasonLabel = season?.name || `Season ${seasonNumber}`;
  const episodes = season?.episodes ?? [];
  const ownedEpCount = episodes.filter((ep) => ownership[episodeKey(seasonNumber, ep.episode_number)]?.owned).length;
  const plexSeason = plexSeasons.find((ps) => ps.index === seasonNumber);
  const canShuffle = showOwned && !!plexSeason;

  const sheetTranslate = dismissing ? '100%' : entered ? `${dragOffset}px` : '100%';

  // Build action list — always show all 3 options, disable unavailable ones
  const actions: { key: string; icon: React.ReactNode; label: string; shortLabel: string; onClick: () => void; disabled: boolean }[] = [
    {
      key: 'shuffle',
      icon: (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="16 3 21 3 21 8" /><line x1="4" y1="20" x2="21" y2="3" />
          <polyline points="21 16 21 21 16 21" /><line x1="15" y1="15" x2="21" y2="21" />
          <line x1="4" y1="4" x2="9" y2="9" />
        </svg>
      ),
      label: `Shuffle ${seasonLabel}`,
      shortLabel: 'Shuffle',
      disabled: !canShuffle,
      onClick: () => { if (canShuffle) onShuffle(plexSeason!.ratingKey, `Shuffling ${plexSeason!.title || seasonLabel}...`); },
    },
    {
      key: 'autosearch',
      icon: (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
        </svg>
      ),
      label: 'Auto search',
      shortLabel: 'Search',
      disabled: !sonarrEnabled || autoSearchLoading,
      onClick: onAutoSearch,
    },
    {
      key: 'sources',
      icon: (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <line x1="8" y1="6" x2="21" y2="6" /><line x1="8" y1="12" x2="21" y2="12" /><line x1="8" y1="18" x2="21" y2="18" />
          <line x1="3" y1="6" x2="3.01" y2="6" /><line x1="3" y1="12" x2="3.01" y2="12" /><line x1="3" y1="18" x2="3.01" y2="18" />
        </svg>
      ),
      label: 'Browse sources',
      shortLabel: 'Sources',
      disabled: !sonarrEnabled,
      onClick: () => onBrowseSources?.(),
    },
  ].filter((a) => a.key !== 'sources' || !!onBrowseSources);

  return createPortal(
    <div className="luma-fullscreen luma-bleed"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10001,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'flex-end',
        background: entered && !dismissing ? 'rgba(0,0,0,0.4)' : 'transparent',
        transition: 'background 0.25s ease',
      }}
      onClick={(e) => { if (e.target === e.currentTarget) handleDismiss(); }}
    >
      <div className="luma-bleed-panel"
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        onTouchCancel={onTouchCancel}
        style={{
          height: 'calc(100% - 60px)',
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
          style={{ flexShrink: 0, display: 'flex', justifyContent: 'center', padding: '12px 0 8px', cursor: 'grab', touchAction: 'none' }}
        >
          <div style={{ width: '40px', height: '5px', borderRadius: '3px', background: 'rgba(255,255,255,0.3)' }} />
        </div>

        {/* Header */}
        <div style={{ flexShrink: 0, padding: '0 20px 16px', textAlign: 'center' }}>
          <h3 style={{ fontSize: '18px', fontWeight: 700, color: '#fff', margin: '0 0 4px' }}>
            {seasonLabel}
          </h3>
          <p style={{ fontSize: '13px', color: 'rgba(255,255,255,0.5)', margin: 0 }}>
            {ownedEpCount} / {season?.episode_count ?? 0} episode{(season?.episode_count ?? 0) !== 1 ? 's' : ''}
          </p>
        </div>

        {/* Action rows */}
        <div style={{ flexShrink: 0, padding: '0 16px 12px', display: 'flex', gap: '8px' }}>
          {actions.map((action) => (
            <button
              key={action.key}
              type="button"
              disabled={action.disabled}
              onClick={action.onClick}
              style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: '6px',
                padding: '12px 8px',
                borderRadius: '12px',
                border: 'none',
                background: 'rgba(255, 255, 255, 0.06)',
                color: action.disabled ? 'rgba(255,255,255,0.3)' : '#fff',
                cursor: action.disabled ? 'default' : 'pointer',
                textAlign: 'center',
              }}
            >
              {action.icon}
              <span style={{ fontSize: '11px', fontWeight: 600, lineHeight: 1.2 }}>{action.shortLabel}</span>
            </button>
          ))}
        </div>

        {/* Divider */}
        <div style={{ flexShrink: 0, height: '1px', background: 'rgba(255,255,255,0.08)', margin: '0 16px' }} />

        {/* Episodes header */}
        <div style={{ flexShrink: 0, padding: '12px 16px 8px', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <h4 style={{ fontSize: '15px', fontWeight: 600, color: 'rgba(255,255,255,0.7)', margin: 0 }}>
            Episodes
          </h4>
          {episodes.length > 0 && (
            <span style={{ fontSize: '13px', color: 'rgba(255,255,255,0.4)' }}>
              {ownedEpCount} / {episodes.length}
            </span>
          )}
        </div>

        {/* Scrollable episode list */}
        <div
          ref={scrollRef}
          data-scroll-container
          className="luma-scroll-y"
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            WebkitOverflowScrolling: 'touch',
            overscrollBehavior: 'contain',
            touchAction: 'pan-y',
            padding: '0 16px',
          }}
        >
          {episodes.length === 0 ? (
            <p style={{ textAlign: 'center', color: 'rgba(255,255,255,0.5)', fontSize: '14px', padding: '24px 0' }}>
              {(season?.episode_count ?? 0) > 0 ? 'Loading episodes…' : 'No episodes available'}
            </p>
          ) : (
            episodes.map((ep) => {
              const epOwned = ownership[episodeKey(seasonNumber, ep.episode_number)]?.owned ?? false;
              const airDate = ep.air_date
                ? new Date(ep.air_date + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                : null;
              const runtime = ep.runtime ? `${ep.runtime}m` : null;

              return (
                <div
                  key={ep.episode_number}
                  onClick={() => onEpisodeClick(seasonNumber, ep.episode_number)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '12px',
                    padding: '12px 0',
                    borderBottom: '1px solid rgba(255,255,255,0.06)',
                    cursor: 'pointer',
                  }}
                >
                  {/* Episode number */}
                  <span style={{
                    width: '28px',
                    flexShrink: 0,
                    textAlign: 'center',
                    fontSize: '16px',
                    fontWeight: 700,
                    color: 'rgba(255,255,255,0.4)',
                  }}>
                    {ep.episode_number}
                  </span>

                  {/* Title + meta */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '2px' }}>
                      <span style={{
                        width: '7px',
                        height: '7px',
                        borderRadius: '50%',
                        flexShrink: 0,
                        background: orbWithSonarr(epOwned ? '#4ade80' : '#ef4444', isInFlight(sonarrEpisode(sonarrSt, seasonNumber, ep.episode_number)?.state)),
                      }} />
                      <span style={{
                        fontSize: '15px',
                        fontWeight: 600,
                        color: 'rgba(255,255,255,0.9)',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}>
                        {ep.name}
                      </span>
                    </div>
                    <div style={{ display: 'flex', gap: '6px', fontSize: '12px', color: 'rgba(255,255,255,0.5)' }}>
                      {airDate && <span>{airDate}</span>}
                      {airDate && runtime && <span>·</span>}
                      {runtime && <span>{runtime}</span>}
                    </div>
                  </div>

                  <SonarrEpisodeBadges
                    season={seasonNumber}
                    episode={ep.episode_number}
                    size={32}
                    direction="row"
                    onSources={onEpisodeSources ? () => onEpisodeSources(seasonNumber, ep.episode_number) : undefined}
                  />

                  {/* Chevron */}
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.25)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                    <polyline points="9 18 15 12 9 6" />
                  </svg>
                </div>
              );
            })
          )}

          {/* Safe area bottom */}
          <div style={{ height: 'calc(env(safe-area-inset-bottom, 0px) + 16px)' }} />
        </div>
      </div>
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------------------
// SeasonRow — vertical season row matching episode list card style
// ---------------------------------------------------------------------------

function SeasonRow({
  onSelect,
  children,
}: {
  onSelect: () => void;
  children: React.ReactNode;
}) {
  const [pressed, setPressed] = useState(false);
  return (
    <div
      onClick={() => {
        setPressed(true);
        setTimeout(() => { setPressed(false); onSelect(); }, 160);
      }}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '16px',
        padding: '14px 16px',
        borderRadius: '16px',
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.05)',
        cursor: 'pointer',
        transform: pressed ? 'scale(0.98)' : 'scale(1)',
        transition: 'transform 0.16s cubic-bezier(0.34, 1.4, 0.64, 1)',
      }}
    >
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SeasonsShelf — slide-up sheet with vertical season rows (same chrome as episodes)
// ---------------------------------------------------------------------------

function SeasonsShelf({
  seasons,
  seasonOwnership,
  plexSeasons,
  showPosterUrl,
  onSelectSeason,
  onSeasonSources,
  onClose,
}: {
  seasons: TvSeason[];
  seasonOwnership: Record<number, { owned: number; total: number }>;
  plexSeasons: PlexSeason[];
  showPosterUrl?: string | null;
  onSelectSeason: (seasonNumber: number) => void;
  onSeasonSources?: (seasonNumber: number) => void;
  onClose: () => void;
}) {
  const sonarrSt = useSonarrStatusData();
  const [entered, setEntered] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [dragOffset, setDragOffset] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });

  useEffect(() => {
    requestAnimationFrame(() => setEntered(true));
  }, []);

  const handleDismiss = useCallback(() => {
    setDismissing(true);
    setTimeout(onClose, 300);
  }, [onClose]);

  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && !document.querySelector('[data-sources-title],[data-show-monitor-sheet]')) { e.stopPropagation(); handleDismiss(); }
    }
    window.addEventListener('keydown', handleKey, true);
    return () => window.removeEventListener('keydown', handleKey, true);
  }, [handleDismiss]);

  // Handle-only swipe dismiss — content scroll stays native both directions.
  const onSheetTouchStart = useCallback((e: React.TouchEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest('[data-sheet-handle]') && e.touches.length > 0) {
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
    if (dy > 120) handleDismiss();
    else setDragOffset(0);
  }, [handleDismiss]);

  const onSheetTouchCancel = useCallback(() => {
    dragRef.current.active = false;
    setDragOffset(0);
  }, []);

  const sheetTranslate = dismissing ? '100%' : entered ? `${dragOffset}px` : '100%';

  return createPortal(
    <div className="luma-fullscreen luma-bleed"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10001,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'flex-end',
        background: entered && !dismissing ? 'rgba(0,0,0,0.5)' : 'transparent',
        transition: 'background 0.3s ease',
      }}
      onClick={(e) => { if (e.target === e.currentTarget) handleDismiss(); }}
    >
      <div className="luma-bleed-panel"
        onTouchStart={onSheetTouchStart}
        onTouchMove={onSheetTouchMove}
        onTouchEnd={onSheetTouchEnd}
        onTouchCancel={onSheetTouchCancel}
        style={{
          height: '78%',
          maxHeight: 'calc(100% - 140px)',
          display: 'flex',
          flexDirection: 'column',
          background: 'var(--color-kiosk-black)',
          borderRadius: '20px 20px 0 0',
          overflow: 'hidden',
          transform: `translateY(${sheetTranslate})`,
          transition: dragOffset > 0 ? 'none' : 'transform 0.42s cubic-bezier(0.34, 1.35, 0.64, 1)',
          willChange: 'transform',
        }}
      >
        {/* Grab handle — same chrome as episodes sheet */}
        <div data-sheet-handle style={{ display: 'flex', justifyContent: 'center', padding: '12px 0 8px', cursor: 'grab', touchAction: 'none' }}>
          <div style={{ width: '40px', height: '5px', borderRadius: '3px', background: 'rgba(255,255,255,0.3)' }} />
        </div>

        {/* Header */}
        <div style={{
          flexShrink: 0,
          padding: '0 20px 12px',
          borderBottom: '1px solid rgba(255,255,255,0.08)',
        }}>
          <h3 style={{
            fontSize: '18px',
            fontWeight: 700,
            color: '#fff',
            margin: 0,
            textAlign: 'center',
          }}>
            Seasons
          </h3>
        </div>

        {/* Vertical season rows — same card rhythm as episode list */}
        <div
          ref={scrollRef}
          data-scroll-container
          className="luma-scroll-y"
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            WebkitOverflowScrolling: 'touch',
            overscrollBehavior: 'contain',
            touchAction: 'pan-y',
            padding: '12px 16px',
          }}
        >
          {seasons.length === 0 ? (
            <p style={{
              margin: '24px auto',
              textAlign: 'center',
              color: 'rgba(255,255,255,0.55)',
              fontSize: '14px',
            }}>
              Loading seasons…
            </p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {seasons.map((season) => {
                const sOwn = seasonOwnership[season.season_number];
                const ownedCount = sOwn?.owned ?? 0;
                const totalCount = sOwn?.total ?? season.episode_count;
                const isFullyOwned = ownedCount > 0 && ownedCount === totalCount;
                const hasNone = ownedCount === 0;
                const orbColor = orbWithSonarr(
                  isFullyOwned ? '#4ade80' : hasNone ? '#ef4444' : '#fbbf24',
                  seasonInFlight(sonarrSeason(sonarrSt, season.season_number)) > 0,
                );

                const plexSeason = plexSeasons.find(ps => ps.index === season.season_number);
                const plexThumbUrl = plexSeason?.thumbPath
                  ? apiUrl(`/api/img/plex?url=${encodeURIComponent(plexSeason.thumbPath)}`)
                  : null;
                const tmdbPosterUrl = season.poster_path
                  ? posterUrl(season.poster_path, 'w342')
                  : null;
                const imgSrc = tmdbPosterUrl || plexThumbUrl || showPosterUrl || undefined;
                const airYear = season.air_date
                  ? season.air_date.slice(0, 4)
                  : null;

                return (
                  <SeasonRow
                    key={season.season_number}
                    onSelect={() => onSelectSeason(season.season_number)}
                  >
                    {/* Season poster thumb — same row height language as episode still */}
                    <div style={{
                      width: '100px',
                      height: '150px',
                      borderRadius: '12px',
                      overflow: 'hidden',
                      flexShrink: 0,
                      background: 'rgba(255,255,255,0.06)',
                      position: 'relative',
                    }}>
                      {imgSrc ? (
                        <img
                          src={imgSrc}
                          alt={season.name || `Season ${season.season_number}`}
                          loading="lazy"
                          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                        />
                      ) : (
                        <div style={{
                          width: '100%', height: '100%',
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          color: 'rgba(255,255,255,0.25)',
                          fontSize: '18px', fontWeight: 700,
                        }}>
                          {season.season_number}
                        </div>
                      )}
                      {/* Orb + have/total pill */}
                      <div data-season-count style={{
                        position: 'absolute', top: '6px', left: '6px',
                        display: 'flex', alignItems: 'center', gap: '5px',
                      }}>
                        <div style={{
                          width: '12px', height: '12px', borderRadius: '50%', flexShrink: 0,
                          background: orbColor, boxShadow: '0 1px 3px rgba(0,0,0,0.5)',
                        }} />
                        {totalCount > 0 && (
                          <div style={OVERLAY_PILL}>{ownedCount}/{totalCount}</div>
                        )}
                      </div>
                      {/* Season number badge — same style as the episode "E1" badge */}
                      <div data-season-badge style={{ ...OVERLAY_PILL, position: 'absolute', bottom: '6px', left: '6px' }}>
                        {seasonShortLabel(season.season_number)}
                      </div>
                    </div>

                    <div style={{
                      flex: 1,
                      minWidth: 0,
                      display: 'flex',
                      flexDirection: 'column',
                      justifyContent: 'center',
                      gap: '8px',
                      paddingRight: '4px',
                    }}>
                      <p style={{
                        fontSize: '20px',
                        fontWeight: 700,
                        letterSpacing: '-0.02em',
                        color: 'rgba(255,255,255,0.96)',
                        margin: 0,
                        lineHeight: 1.25,
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}>
                        {seasonTitle(season.name, season.season_number)}
                      </p>
                      {airYear && (
                      <p style={{
                        margin: 0,
                        fontSize: '14px',
                        fontWeight: 500,
                        letterSpacing: '0.01em',
                        color: 'rgba(255,255,255,0.45)',
                        lineHeight: 1.35,
                      }}>
                        {airYear}
                      </p>
                      )}
                      <SonarrSeasonActivity season={season.season_number} />
                    </div>
                    <SonarrSeasonBadges
                      season={season.season_number}
                      onSources={onSeasonSources ? () => onSeasonSources(season.season_number) : undefined}
                    />

                  </SeasonRow>
                );
              })}
            </div>
          )}

          <div style={{ height: 'calc(env(safe-area-inset-bottom, 0px) + 16px)' }} />
        </div>
      </div>
    </div>,
    document.body,
  );
}

function EpisodesBrowsingSheet({
  season,
  seasonNumber,
  ownership,
  onEpisodeClick,
  onEpisodeSources,
  onClose,
}: {
  season: TvSeason;
  seasonNumber: number;
  ownership: OwnershipMap;
  sonarrEnabled: boolean;
  onEpisodeClick: (season: number, episode: number) => void;
  onEpisodeSources?: (season: number, episode: number) => void;
  onClose: () => void;
}) {
  const sonarrSt = useSonarrStatusData();
  const [entered, setEntered] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [dragOffset, setDragOffset] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });

  useEffect(() => {
    requestAnimationFrame(() => setEntered(true));
  }, []);

  const handleDismiss = useCallback(() => {
    setDismissing(true);
    setTimeout(onClose, 300);
  }, [onClose]);

  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && !document.querySelector('[data-sources-title],[data-show-monitor-sheet]')) { e.stopPropagation(); handleDismiss(); }
    }
    window.addEventListener('keydown', handleKey, true);
    return () => window.removeEventListener('keydown', handleKey, true);
  }, [handleDismiss]);

  // Handle-only swipe dismiss — list scroll must stay native both directions.
  const onSheetTouchStart = useCallback((e: React.TouchEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest('[data-sheet-handle]') && e.touches.length > 0) {
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
    if (dy > 120) handleDismiss();
    else setDragOffset(0);
  }, [handleDismiss]);

  const onSheetTouchCancel = useCallback(() => {
    dragRef.current.active = false;
    setDragOffset(0);
  }, []);

  const episodes = season?.episodes ?? [];
  const sheetTranslate = dismissing ? '100%' : entered ? `${dragOffset}px` : '100%';

  return createPortal(
    <div className="luma-fullscreen luma-bleed"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10001,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'flex-end',
        background: entered && !dismissing ? 'rgba(0,0,0,0.5)' : 'transparent',
        transition: 'background 0.3s ease',
      }}
      onClick={(e) => { if (e.target === e.currentTarget) handleDismiss(); }}
    >
      <div className="luma-bleed-panel"
        onTouchStart={onSheetTouchStart}
        onTouchMove={onSheetTouchMove}
        onTouchEnd={onSheetTouchEnd}
        onTouchCancel={onSheetTouchCancel}
        style={{
          height: '78%',
          maxHeight: 'calc(100% - 140px)',
          display: 'flex',
          flexDirection: 'column',
          background: 'var(--color-kiosk-black)',
          borderRadius: '20px 20px 0 0',
          overflow: 'hidden',
          transform: `translateY(${sheetTranslate})`,
          transition: dragOffset > 0 ? 'none' : 'transform 0.42s cubic-bezier(0.34, 1.35, 0.64, 1)',
          willChange: 'transform',
        }}
      >
        {/* Grab handle */}
        <div data-sheet-handle style={{ display: 'flex', justifyContent: 'center', padding: '12px 0 8px', cursor: 'grab', touchAction: 'none' }}>
          <div style={{ width: '40px', height: '5px', borderRadius: '3px', background: 'rgba(255,255,255,0.3)' }} />
        </div>

        {/* Header */}
        <div style={{
          flexShrink: 0,
          padding: '0 20px 12px',
          borderBottom: '1px solid rgba(255,255,255,0.08)',
        }}>
          <h3 style={{
            fontSize: '18px',
            fontWeight: 700,
            color: '#fff',
            margin: 0,
            textAlign: 'center',
          }}>
            {season?.name || `Season ${seasonNumber}`}
          </h3>
        </div>

        {/* Episode list */}
        <div
          ref={scrollRef}
          data-scroll-container
          className="luma-scroll-y"
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            WebkitOverflowScrolling: 'touch',
            overscrollBehavior: 'contain',
            touchAction: 'pan-y',
            padding: '12px 16px',
          }}
        >
          {episodes.length === 0 ? (
            <p style={{ textAlign: 'center', color: 'rgba(255,255,255,0.5)', fontSize: '14px', padding: '24px 0' }}>
              {(season?.episode_count ?? 0) > 0 ? 'Loading episodes…' : 'No episodes available'}
            </p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {episodes.map((ep) => {
                const epOwned = ownership[episodeKey(seasonNumber, ep.episode_number)]?.owned ?? false;
                const thumbUrl = resolveEpisodeThumbnail(ep);
                const airDate = ep.air_date
                  ? new Date(ep.air_date + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                  : null;
                const runtime = ep.runtime ? `${ep.runtime}m` : null;

                return (
                  <div
                    key={ep.episode_number}
                    onClick={() => onEpisodeClick(seasonNumber, ep.episode_number)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '14px',
                      padding: '12px 14px',
                      borderRadius: '14px',
                      background: 'rgba(255,255,255,0.03)',
                      border: '1px solid rgba(255,255,255,0.05)',
                      cursor: 'pointer',
                    }}
                  >
                    {/* Episode thumbnail */}
                    <div style={{
                      width: '170px',
                      height: '96px',
                      borderRadius: '10px',
                      overflow: 'hidden',
                      flexShrink: 0,
                      background: 'rgba(255,255,255,0.06)',
                      position: 'relative',
                    }}>
                      {thumbUrl ? (
                        <img
                          src={thumbUrl}
                          alt={ep.name}
                          loading="lazy"
                          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                        />
                      ) : (
                        <div style={{
                          width: '100%', height: '100%',
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          color: 'rgba(255,255,255,0.2)',
                          fontSize: '24px', fontWeight: 700,
                        }}>
                          {ep.episode_number}
                        </div>
                      )}
                      {/* Episode number badge */}
                      <div style={{
                        position: 'absolute',
                        bottom: '6px',
                        left: '6px',
                        padding: '3px 8px',
                        borderRadius: '5px',
                        background: 'rgba(0,0,0,0.7)',
                        fontSize: '12px',
                        fontWeight: 700,
                        color: '#fff',
                      }}>
                        E{ep.episode_number}
                      </div>
                      {/* Ownership indicator */}
                      <div style={{
                        position: 'absolute',
                        top: '6px',
                        left: '6px',
                        width: '10px',
                        height: '10px',
                        borderRadius: '50%',
                        background: orbWithSonarr(epOwned ? '#4ade80' : '#ef4444', isInFlight(sonarrEpisode(sonarrSt, seasonNumber, ep.episode_number)?.state)),
                        boxShadow: '0 1px 3px rgba(0,0,0,0.5)',
                      }} />
                    </div>

                    {/* Episode info — 2-line title + typography hierarchy */}
                    <div style={{
                      flex: 1,
                      minWidth: 0,
                      display: 'flex',
                      flexDirection: 'column',
                      justifyContent: 'center',
                      gap: '7px',
                      paddingRight: '2px',
                    }}>
                      <p style={{
                        fontSize: '15px',
                        fontWeight: 600,
                        letterSpacing: '-0.015em',
                        color: 'rgba(255,255,255,0.96)',
                        margin: 0,
                        lineHeight: 1.3,
                        display: '-webkit-box',
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: 'vertical',
                        overflow: 'hidden',
                        wordBreak: 'break-word',
                      }}>
                        {ep.name}
                      </p>
                      <p style={{
                        margin: 0,
                        fontSize: '12px',
                        fontWeight: 500,
                        letterSpacing: '0.015em',
                        color: 'rgba(255,255,255,0.40)',
                        lineHeight: 1.35,
                      }}>
                        {airDate && <span style={{ whiteSpace: 'nowrap' }}>{airDate}</span>}
                        {airDate && runtime && <span>{' \u00b7 '}</span>}
                        {runtime && <span style={{ whiteSpace: 'nowrap' }}>{runtime}</span>}
                      </p>
                      <SonarrEpisodeNote season={seasonNumber} episode={ep.episode_number} />
                    </div>

                    <SonarrEpisodeBadges
                      season={seasonNumber}
                      episode={ep.episode_number}
                      onSources={onEpisodeSources ? () => onEpisodeSources(seasonNumber, ep.episode_number) : undefined}
                    />
                  </div>
                );
              })}
            </div>
          )}

          {/* Safe area bottom */}
          <div style={{ height: 'calc(env(safe-area-inset-bottom, 0px) + 16px)' }} />
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** Purple "2 downloading" line under a season row (renders nothing when idle). */
function SonarrSeasonActivity({ season }: { season: number }) {
  const x = sonarrSeason(useSonarrStatusData(), season);
  if (!x || seasonInFlight(x) === 0) return null;
  return (
    <p style={{ margin: 0, fontSize: '13px', fontWeight: 500, lineHeight: 1.25 }}>
      <SonarrSeasonNote season={season} lead={false} />
    </p>
  );
}

/** Small dark label on artwork — matches the episode "E1" badge. */
const OVERLAY_PILL: React.CSSProperties = {
  padding: '3px 8px',
  borderRadius: '5px',
  background: 'rgba(0,0,0,0.7)',
  fontSize: '12px',
  fontWeight: 700,
  color: '#fff',
  lineHeight: 1.2,
  whiteSpace: 'nowrap',
};
