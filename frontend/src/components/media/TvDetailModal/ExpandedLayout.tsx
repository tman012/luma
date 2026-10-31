import { Hold, useModalMotion } from '@/components/layout/SheetFrame';
import { useSheetHero, HERO_FADE, HERO_ANIM } from '@/hooks/useSheetBackdrop';
import { useTitleReveal, logoInput, titleTextStyle, revealStyle } from '@/hooks/useTitleReveal';
import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import {
  PERSON_FALLBACK_SVG,
  getCastImageUrl,
  CARD_BG,
  deduplicateCastAndCrew,
  type CastMember,
} from '@/utils/modalParsers';
import {
  type TvEpisode,
  type OwnershipMap,
  autoSearchSeason,
} from '@/services/sonarrApi';
import { UltraBlurOverlay } from '@/components/media/UltraBlurOverlay';
import { RatingsBar } from '@/components/media/RatingsBar';
import { useImdbRatings } from '@/hooks/useImdbRatings';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { TOKENS } from './expandedLayoutTokens';
import { useTabletPerf } from '@/hooks/useTabletPerf';
import { posterUrl, finalBackdropUrl } from '@/utils/image';
import { FullPlotModal } from '@/components/media/MovieDetailModal/FullPlotModal';
import { PlaybackModal } from '@/components/media/PlaybackModal';
import { AddToSonarrModal } from './AddToSonarrModal';
import { isSeasonMonitored } from './tvSourcesUtils';
import { TvSourcesPanel } from './TvSourcesPanel';
import { EpisodeHeader } from './EpisodeHeader';
import { useAuth } from '@/contexts/AuthContext';
import { seasonTitle, seasonEyebrow, seasonShortLabel, sourcesTargetName } from './seasonLabels';
import {
  SonarrShowStatus, SonarrSeasonNote, SonarrSeasonLine, SonarrEpisodeNote, SonarrEpisodeBadges, SonarrSeasonBadges,
  useSonarrStatusData, sonarrEpisode, sonarrSeason, seasonInFlight, isInFlight, orbWithSonarr,
} from './SonarrStatus';
import { useModalHost } from '@/contexts/ModalHostContext';
import type { TvModalViewProps } from './types';
import { useFocusable } from '@/hooks/useFocusable';
import { apiUrl } from '@/urlHelpers';
import { seasonsLabel } from '@/utils/seasonsLabel';
import { EpisodeMark, LumaMonitorContext, SearchMark, SeasonMark, SeriesMark, useLumaSeries } from '@/components/library/tvMonitor';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function episodeKey(season: number, episode: number): string {
  return `s${season}e${episode}`;
}

function computeSeasonOwnership(
  seasons: { season_number: number; episode_count?: number; episodes: { episode_number: number }[] }[],
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

function resolveEpisodeThumbnail(ep: TvEpisode): string | null {
  if (ep.still_path) {
    const direct = posterUrl(ep.still_path, 'w500');
    if (direct) return direct;
    return apiUrl(`/api/img/tmdb?path=${encodeURIComponent(ep.still_path)}&size=w500`);
  }
  return null;
}

// ---------------------------------------------------------------------------
// PillButton — minimal pill with icon + label (matches MovieDetailModal)
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
  tone,
  testid,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  primary?: boolean;
  tone?: 'blue';
  testid?: string;
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
      data-testid={testid}
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '8px',
        padding,
        borderRadius: '28px',
        background: tone === 'blue'
          ? (hovered ? '#60a5fa' : '#3b82f6')
          : primary
          ? (hovered ? 'rgba(255,255,255,0.88)' : '#fff')
          : (hovered ? 'rgba(255,255,255,0.18)' : 'rgba(255,255,255,0.08)'),
        border: primary || tone ? 'none' : '1px solid rgba(255,255,255,0.15)',
        color: tone === 'blue' ? '#fff' : primary ? '#111318' : '#fff',
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
// CastShelf — slide-up shelf with preloaded circular cast photos
// ---------------------------------------------------------------------------

function CastShelf({
  cast,
  isTablet,
  onPersonSearch,
  onClose,
}: {
  cast: CastMember[];
  isTablet: boolean;
  onPersonSearch?: (name: string, tmdbPersonId?: number) => void;
  onClose: () => void;
}) {
  const [imagesReady, setImagesReady] = useState(false);
  const imgSize = isTablet ? 130 : 150;

  useEffect(() => {
    const allPeople = cast
      .map(m => getCastImageUrl(m))
      .filter((u): u is string => !!u);
    if (allPeople.length === 0) { setImagesReady(true); return; }

    let loaded = 0;
    const target = Math.min(allPeople.length, 8);
    const check = () => { if (++loaded >= target) setImagesReady(true); };

    allPeople.slice(0, target).forEach((url) => {
      const img = new Image();
      img.onload = check;
      img.onerror = check;
      img.src = url;
    });

    const timer = setTimeout(() => setImagesReady(true), 1500);
    return () => clearTimeout(timer);
  }, [cast]);

  const allMembers: Array<{ name: string; subtitle: string; imgUrl: string | null; personId?: number }> = cast.map(m => ({
    name: m.name,
    subtitle: m.character ?? m.role ?? '',
    imgUrl: getCastImageUrl(m),
    personId: m.tmdbPersonId,
  }));

  return (
    <>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, zIndex: 7 }} />
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          right: 0,
          zIndex: 8,
          background: 'transparent',
          padding: isTablet ? '40px 0 32px' : '48px 0 48px',
          animation: imagesReady ? 'tvCastShelfBounce 0.55s cubic-bezier(0.34, 1.56, 0.64, 1)' : 'none',
          transform: imagesReady ? 'translateY(0)' : 'translateY(100%)',
        }}
      >
        <div style={{ position: 'relative' }}>
          <div style={{
            display: 'flex',
            gap: isTablet ? '24px' : '32px',
            padding: isTablet ? '0 24px' : '0 48px',
            overflowX: 'auto',
            WebkitOverflowScrolling: 'touch',
            scrollbarWidth: 'none',
            paddingBottom: '8px',
          }}>
            {allMembers.map((member, idx) => (
              <div
                key={`${member.name}-${idx}`}
                onClick={() => onPersonSearch?.(member.name, member.personId)}
                style={{
                  flexShrink: 0,
                  width: `${imgSize}px`,
                  textAlign: 'center',
                  cursor: onPersonSearch ? 'pointer' : 'default',
                }}
              >
                <img
                  src={member.imgUrl ?? PERSON_FALLBACK_SVG}
                  alt={member.name}
                  style={{
                    width: `${imgSize}px`,
                    height: `${imgSize}px`,
                    borderRadius: '50%',
                    objectFit: 'cover',
                    background: 'rgba(255,255,255,0.1)',
                    marginBottom: '12px',
                  }}
                  onError={(e) => { (e.currentTarget as HTMLImageElement).src = PERSON_FALLBACK_SVG; }}
                />
                <p style={{ fontSize: '14px', fontWeight: 500, color: 'rgba(255,255,255,0.9)', margin: '0 0 3px', lineHeight: 1.3, wordWrap: 'break-word' }}>
                  {member.name}
                </p>
                {member.subtitle && (
                  <p style={{ fontSize: '13px', color: 'rgba(255,255,255,0.6)', margin: 0, lineHeight: 1.3 }}>
                    {member.subtitle}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// EpisodeGridModal — full-screen portal grid matching SeasonGridModal
// ---------------------------------------------------------------------------

function EpisodeGridModal({
  seasons,
  ownership,
  selectedSeason,
  onSelectSeason,
  onEpisodeClick,
  isTablet,
  onClose,
  onPlayEpisode,
  showTitle,
  onEpisodeSources,
}: {
  seasons: { season_number: number; name: string; episodes: TvEpisode[]; episode_count: number }[];
  ownership: OwnershipMap;
  selectedSeason: number;
  onSelectSeason: (n: number) => void;
  onEpisodeClick: (season: number, episode: number) => void;
  isTablet: boolean;
  onClose: () => void;
  onPlayEpisode?: (ratingKey: number, title: string, seasonNum: number, epNum: number) => void;
  showTitle?: string;
  onEpisodeSources?: (season: number, episode: number) => void;
}) {
  const sonarrSt = useSonarrStatusData();
  const activeSeason = seasons.find(s => s.season_number === selectedSeason);
  const episodes = activeSeason?.episodes ?? [];
  const isSingleSeason = seasons.length === 1;

  const seasonLabel = activeSeason?.name || `Season ${selectedSeason}`;
  const headerText = showTitle
    ? `${showTitle} \u00B7 ${seasonLabel}`
    : seasonLabel;

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !document.querySelector('[data-sources-title],[data-show-monitor-sheet]')) { e.stopImmediatePropagation(); onClose(); }
    };
    window.addEventListener('keydown', handler, true);
    return () => window.removeEventListener('keydown', handler, true);
  }, [onClose]);

  return createPortal(
    <div className="luma-fullscreen luma-bleed"
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 200,
        background: OVERLAY_SURFACE,
        display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
        overflowY: 'auto',
        animation: 'tvSeasonGridFadeIn 0.25s ease',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '95%', maxWidth: '1400px',
          padding: isTablet ? '48px 20px 40px' : '64px 32px 48px',
        }}
      >
        {/* Header */}
        <h2 style={{
          fontSize: isTablet ? '24px' : '28px', fontWeight: 700, color: '#fff',
          marginBottom: '8px', textAlign: 'center',
          textShadow: '0 2px 8px rgba(0,0,0,0.5)',
        }}>
          {headerText}
        </h2>

        {/* Season tabs for multi-season mode */}
        {!isSingleSeason && (
          <div style={{
            display: 'flex', gap: '8px', justifyContent: 'center',
            flexWrap: 'wrap',
            marginBottom: '24px',
          }}>
            {seasons.map((season) => {
              const isActive = season.season_number === selectedSeason;
              const so = computeSeasonOwnership([season], ownership)[season.season_number];
              const ownedCount = so?.owned ?? 0;
              const totalCount = so?.total ?? season.episode_count;
              const isFullyOwned = ownedCount > 0 && ownedCount === totalCount;
              const isPartial = ownedCount > 0 && ownedCount < totalCount;
              const hasNone = ownedCount === 0;
              const orbColor = orbWithSonarr(isFullyOwned ? '#4ade80' : hasNone ? '#ef4444' : '#fbbf24', seasonInFlight(sonarrSeason(sonarrSt, season.season_number)) > 0);

              return (
                <button
                  key={season.season_number}
                  type="button"
                  onClick={(e) => { e.stopPropagation(); onSelectSeason(season.season_number); }}
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: '8px',
                    padding: '10px 20px', borderRadius: '20px',
                    border: isActive ? '1px solid rgba(255,255,255,0.3)' : '1px solid rgba(255,255,255,0.12)',
                    background: isActive ? 'rgba(255,255,255,0.15)' : 'rgba(255,255,255,0.05)',
                    color: isActive ? '#fff' : 'rgba(255,255,255,0.6)',
                    fontSize: '14px', fontWeight: 600, cursor: 'pointer',
                    transition: 'all 0.15s ease', whiteSpace: 'nowrap',
                  }}
                >
                  <span style={{
                    width: '7px', height: '7px', borderRadius: '50%',
                    background: orbColor, boxShadow: `0 0 4px ${orbColor}40`,
                  }} />
                  {seasonEyebrow(season.name, season.season_number)
                    ? `S${season.season_number} · ${seasonTitle(season.name, season.season_number)}`
                    : seasonTitle(season.name, season.season_number)}
                  {isPartial && (
                    <span style={{ fontSize: '12px', color: '#fbbf24', fontWeight: 700 }}>
                      {ownedCount}/{totalCount}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        )}
        {isSingleSeason && <div style={{ marginBottom: '24px' }} />}
        {activeSeason && (
          <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 8, marginTop: -12, marginBottom: 20 }}>
            <SeasonMark season={activeSeason.season_number} />
            <SearchMark season={activeSeason.season_number} size={36} />
          </div>
        )}

        {/* Episode grid */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: isTablet
            ? 'repeat(auto-fill, minmax(280px, 1fr))'
            : 'repeat(auto-fill, minmax(380px, 1fr))',
          gap: isTablet ? '24px' : '32px',
        }}>
          {episodes.map((ep) => {
            const key = episodeKey(activeSeason!.season_number, ep.episode_number);
            const epOwned = ownership[key]?.owned ?? false;
            const epRatingKey = ownership[key]?.rating_key;
            const thumbUrl = resolveEpisodeThumbnail(ep);
            const airDateDisplay = ep.air_date
              ? new Date(ep.air_date + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
              : null;
            const runtimeDisplay = ep.runtime ? `${ep.runtime}m` : null;
            const orbColor = orbWithSonarr(epOwned ? '#4ade80' : '#ef4444', isInFlight(sonarrEpisode(sonarrSt, activeSeason!.season_number, ep.episode_number)?.state));

            return (
              <div
                key={key}
                onClick={(e) => { e.stopPropagation(); onEpisodeClick(activeSeason!.season_number, ep.episode_number); }}
                data-tv-card="episode"
                style={{ ...CARD_GLASS, cursor: 'pointer', transition: 'transform 0.2s ease' }}
                onMouseEnter={(e) => { e.currentTarget.style.transform = 'scale(1.03)'; }}
                onMouseLeave={(e) => { e.currentTarget.style.transform = 'scale(1)'; }}
              >
                {/* Thumbnail — 16:9 */}
                <div style={{
                  width: '100%', aspectRatio: '16 / 9',
                  borderRadius: '10px', overflow: 'hidden',
                  background: thumbUrl ? 'transparent' : PLACEHOLDER_GLASS, position: 'relative',
                }}>
                  {thumbUrl ? (
                    <img
                      src={thumbUrl.replace('w300', 'w500')}
                      alt={`E${ep.episode_number}`}
                      style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                      loading="lazy"
                      onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
                    />
                  ) : (
                    <div data-tv-placeholder="" style={{
                      width: '100%', height: '100%', background: PLACEHOLDER_GLASS,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      color: 'rgba(255,255,255,0.2)', fontSize: '18px', fontWeight: 600,
                    }}>
                      Episode {ep.episode_number}
                    </div>
                  )}
                  <EpisodeMark season={activeSeason!.season_number} episode={ep.episode_number} />
                  {/* Play overlay for owned */}
                  {epOwned && epRatingKey && (
                    <div
                      onClick={(e) => {
                        e.stopPropagation();
                        onPlayEpisode?.(epRatingKey, ep.name, activeSeason!.season_number, ep.episode_number);
                      }}
                      style={{
                        position: 'absolute', inset: 0,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        background: 'rgba(0,0,0,0.4)',
                        opacity: 0, transition: 'opacity 0.2s ease', cursor: 'pointer',
                      }}
                      onMouseEnter={(e) => { e.currentTarget.style.opacity = '1'; }}
                      onMouseLeave={(e) => { e.currentTarget.style.opacity = '0'; }}
                    >
                      <div style={{
                        width: '48px', height: '48px', borderRadius: '50%',
                        background: 'rgba(255,255,255,0.95)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}>
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="#111318">
                          <polygon points="8,4 20,12 8,20" />
                        </svg>
                      </div>
                    </div>
                  )}
                  {/* Ownership orb */}
                  <div style={{
                    position: 'absolute', top: '8px', left: '8px',
                    width: '9px', height: '9px', borderRadius: '50%',
                    background: orbColor, boxShadow: `0 0 5px ${orbColor}60`,
                  }} />
                  {/* Episode number badge */}
                  <div data-tv-ep-badge="" style={{
                    ...CHIP_GLASS,
                    position: 'absolute', bottom: '10px', left: '10px', borderRadius: '8px',
                    padding: '4px 12px', fontSize: isTablet ? '14px' : '16px', fontWeight: 700,
                    color: 'rgba(255,255,255,0.9)',
                  }}>
                    E{ep.episode_number}
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', marginTop: '12px' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    {/* Title */}
                    <p style={{
                      fontSize: isTablet ? '17px' : '20px', fontWeight: 600,
                      color: 'rgba(255,255,255,0.9)',
                      margin: '0 0 4px',
                      whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                    }}>
                      {ep.name}
                    </p>
                    {/* Air date · Runtime */}
                    <p style={{
                      fontSize: isTablet ? '14px' : '16px',
                      color: 'rgba(255,255,255,0.5)', margin: 0,
                    }}>
                      {[airDateDisplay, runtimeDisplay].filter(Boolean).join(' \u00B7 ')}
                    </p>
                    <div style={{ marginTop: '6px' }}>
                      <SonarrEpisodeNote season={activeSeason!.season_number} episode={ep.episode_number} fontSize={isTablet ? 13 : 14} />
                    </div>
                  </div>
                  <SonarrEpisodeBadges
                    season={activeSeason!.season_number}
                    episode={ep.episode_number}
                    onSources={onEpisodeSources ? () => onEpisodeSources(activeSeason!.season_number, ep.episode_number) : undefined}
                  />
                </div>
              </div>
            );
          })}
        </div>
        {episodes.length === 0 && (
          <p style={{ textAlign: 'center', color: 'rgba(255,255,255,0.4)', fontSize: '14px', padding: '32px 0' }}>
            No episodes available
          </p>
        )}
      </div>
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------------------
// SeasonGridModal — large poster grid for picking a season
// ---------------------------------------------------------------------------

function SeasonGridModal({
  seasons,
  ownership,
  showTitle,
  isTablet,
  onSelectSeason,
  onSeasonSources,
  onClose,
}: {
  seasons: { season_number: number; name: string; overview: string; poster_path: string | null; air_date?: string | null; episode_count: number; episodes: { episode_number: number }[] }[];
  ownership: OwnershipMap;
  showTitle: string;
  isTablet: boolean;
  onSelectSeason: (seasonNumber: number) => void;
  onSeasonSources?: (seasonNumber: number) => void;
  onClose: () => void;
}) {
  const sonarrSt = useSonarrStatusData();
  const seasonOwnershipMap = computeSeasonOwnership(seasons, ownership);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !document.querySelector('[data-sources-title],[data-show-monitor-sheet]')) { e.stopImmediatePropagation(); onClose(); }
    };
    window.addEventListener('keydown', handler, true);
    return () => window.removeEventListener('keydown', handler, true);
  }, [onClose]);

  const posterW = isTablet ? 170 : 200;
  const posterH = Math.round(posterW * 1.5);

  return createPortal(
    <div className="luma-fullscreen luma-bleed"
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 200,
        background: OVERLAY_SURFACE,
        display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
        animation: 'tvSeasonGridFadeIn 0.25s ease',
        overflowY: 'auto',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '95%', maxWidth: '1400px',
          padding: isTablet ? '24px 20px 40px' : '28px 32px 48px',
          scrollbarWidth: 'none',
        }}
      >
        <h2 style={{
          fontSize: isTablet ? '20px' : '24px', fontWeight: 700, color: '#fff',
          marginBottom: '24px', textAlign: 'center',
          textShadow: '0 2px 8px rgba(0,0,0,0.5)',
        }}>
          {showTitle}
        </h2>
        <div style={{
          display: 'grid',
          gridTemplateColumns: `repeat(auto-fill, minmax(${posterW}px, 1fr))`,
          gap: isTablet ? '20px' : '24px',
          justifyItems: 'center',
        }}>
          {seasons.map((season) => {
            const so = seasonOwnershipMap[season.season_number];
            const ownedCount = so?.owned ?? 0;
            const totalCount = so?.total ?? season.episode_count;
            const isFullyOwned = ownedCount > 0 && ownedCount === totalCount;
            const hasNone = ownedCount === 0;
            const orbColor = orbWithSonarr(
              isFullyOwned ? '#4ade80' : hasNone ? '#ef4444' : '#fbbf24',
              seasonInFlight(sonarrSeason(sonarrSt, season.season_number)) > 0,
            );
            const seasonYear = season.air_date ? season.air_date.slice(0, 4) : null;
            const seasonPosterSrc = season.poster_path
              ? (posterUrl(season.poster_path, 'w342')
                  || apiUrl(`/api/img/tmdb?path=${encodeURIComponent(season.poster_path)}&size=w342`))
              : null;

            return (
              <div
                key={season.season_number}
                onClick={() => onSelectSeason(season.season_number)}
                data-tv-card="season"
                style={{
                  ...CARD_GLASS,
                  width: `${posterW + 20}px`, boxSizing: 'border-box', cursor: 'pointer',
                  transition: 'transform 0.2s ease',
                }}
                onMouseEnter={(e) => { e.currentTarget.style.transform = 'scale(1.05)'; }}
                onMouseLeave={(e) => { e.currentTarget.style.transform = 'scale(1)'; }}
              >
                <div style={{
                  width: `${posterW}px`, height: `${posterH}px`,
                  borderRadius: '10px', overflow: 'hidden',
                  background: seasonPosterSrc ? 'transparent' : PLACEHOLDER_GLASS, position: 'relative',
                }}>
                  {seasonPosterSrc ? (
                    <img
                      src={seasonPosterSrc} alt={season.name}
                      style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                      loading="lazy"
                    />
                  ) : (
                    <div data-tv-placeholder="" style={{
                      width: '100%', height: '100%', background: PLACEHOLDER_GLASS,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      color: 'rgba(255,255,255,0.25)', fontSize: '14px', fontWeight: 600,
                    }}>
                      {season.name || `Season ${season.season_number}`}
                    </div>
                  )}
                  {/* Ownership orb + have/total pill */}
                  <div data-season-count style={{
                    position: 'absolute', top: '8px', left: '8px',
                    display: 'flex', alignItems: 'center', gap: '6px',
                  }}>
                    <div style={{
                      width: '10px', height: '10px', borderRadius: '50%', flexShrink: 0,
                      background: orbColor, boxShadow: `0 0 5px ${orbColor}60`,
                    }} />
                    {totalCount > 0 && <div style={GRID_PILL}>{ownedCount}/{totalCount}</div>}
                  </div>
                  <span style={{ position: 'absolute', top: '8px', right: '8px', zIndex: 2 }}>
                    <SearchMark season={season.season_number} />
                  </span>
                  {/* Season number badge — same style as the episode "E1" badge */}
                  <div data-season-badge style={{ ...GRID_PILL, position: 'absolute', left: '8px', bottom: '8px' }}>
                    {seasonShortLabel(season.season_number)}
                  </div>
                  <div style={{ position: 'absolute', right: '8px', bottom: '8px' }}>
                    <SonarrSeasonBadges
                      season={season.season_number}
                      size={34}
                      overlay
                      onSources={onSeasonSources ? () => onSeasonSources(season.season_number) : undefined}
                    />
                  </div>
                </div>
                <p style={{
                  fontSize: isTablet ? '14px' : '16px', fontWeight: 600, color: 'rgba(255,255,255,0.9)',
                  margin: '10px 0 3px', textAlign: 'center',
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                }}>
                  {seasonTitle(season.name, season.season_number)}
                </p>
                {(seasonYear || seasonInFlight(sonarrSeason(sonarrSt, season.season_number)) > 0) && (
                  <p style={{
                    fontSize: isTablet ? '13px' : '14px', margin: 0, textAlign: 'center',
                    color: 'rgba(255,255,255,0.5)', fontWeight: 500,
                  }}>
                    {seasonYear}
                    <SonarrSeasonNote season={season.season_number} lead={!!seasonYear} />
                  </p>
                )}
              </div>
            );
          })}
        </div>
      </div>
      <style>{`
        @keyframes tvSeasonGridFadeIn { from { opacity: 0; } to { opacity: 1; } }
      `}</style>
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------------------
// SeasonDetailModal — full detail view for a single season
// ---------------------------------------------------------------------------

function SeasonDetailModal({
  show,
  season,
  showBackdropUrl,
  ultraBlurColors: parentUltraBlurColors,
  ownership,
  showOwned,
  sonarrStatus,
  castForDisplay,
  creatorsForDisplay,
  isTablet,
  onEpisodeClick,
  onPersonSearch,
  onPlayEpisode,
  onShowSources,
  onEpisodeSources,
  onAddSeasonToWatchlist,
  onClose,
}: {
  show: import('@/utils/modalParsers').TvShowDetail;
  season: { season_number: number; name: string; overview: string; poster_path: string | null; episode_count: number; episodes: TvEpisode[] };
  showBackdropUrl: string | null;
  ultraBlurColors: import('@/utils/modalParsers').UltraBlurColors | null;
  ownership: OwnershipMap;
  showOwned: boolean;
  sonarrStatus: import('@/services/sonarrApi').SonarrStatus | null;
  castForDisplay: CastMember[];
  creatorsForDisplay: import('@/utils/modalParsers').Director[];
  isTablet: boolean;
  onEpisodeClick?: (seasonNumber: number, episodeNumber: number) => void;
  onPersonSearch?: (name: string, personId?: number) => void;
  onPlayEpisode?: (ratingKey: number, title: string, seasonNum: number, epNum: number) => void;
  onShowSources?: (target: import('./types').SourcesTarget) => void;
  onEpisodeSources?: (season: number, episode: number) => void;
  onAddSeasonToWatchlist?: (season: number) => Promise<boolean> | void;
  onClose: () => void;
}) {
  const titleState = useTitleReveal(logoInput(show), show?.title, show?.backdropUrl || show?.posterUrl);
  const [showEpisodeGrid, setShowEpisodeGrid] = useState(false);
  const [showCastShelf, setShowCastShelf] = useState(false);
  // Optimistic: this season was just added to the watchlist this session.
  const [justAdded, setJustAdded] = useState(false);
  const [addFailed, setAddFailed] = useState(false);
  const [addInFlight, setAddInFlight] = useState(false);
  const seasonMonitored = justAdded || isSeasonMonitored(sonarrStatus, season.season_number);
  const seasonModalHost = useModalHost();
  const [phase, setPhase] = useState<'entering' | 'visible' | 'exiting'>('entering');
  const t = isTablet ? TOKENS.tablet : TOKENS.desktop;

  const shelfOpen = showCastShelf;

  useEffect(() => {
    const frame = requestAnimationFrame(() => setPhase('visible'));
    return () => cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !document.querySelector('[data-sources-title],[data-show-monitor-sheet]')) {
        e.stopImmediatePropagation();
        if (shelfOpen) { setShowCastShelf(false); return; }
        handleClose();
      }
    };
    window.addEventListener('keydown', handler, true);
    return () => window.removeEventListener('keydown', handler, true);
  }, [shelfOpen]);

  const handleClose = useCallback(() => {
    setPhase('exiting');
    setTimeout(onClose, 300);
  }, [onClose]);

  // Season-specific meta
  const seasonYear = season.episodes[0]?.air_date
    ? new Date(season.episodes[0].air_date + 'T00:00:00').getFullYear()
    : null;
  const epCountDisplay = `${season.episode_count} Episode${season.episode_count !== 1 ? 's' : ''}`;
  const metaItems: string[] = [];
  if (seasonYear) metaItems.push(String(seasonYear));
  metaItems.push(epCountDisplay);
  if (show.network) metaItems.push(show.network);
  if (show.contentRating) metaItems.push(show.contentRating);

  // Season rating — IMDb via OMDb (never TMDb vote_average)
  const effectiveRatings = useImdbRatings(show.title, show.year);

  const overlayRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);

  const modalEntering = useModalMotion(phase, overlayRef, cardRef);

  return createPortal(
    <Hold hold={modalEntering}><div className="luma-fullscreen luma-bleed" ref={overlayRef}
      onClick={handleClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 200,
        display: 'flex', alignItems: t.overlay.alignItems, justifyContent: 'center',
        background: t.overlay.background,
        padding: t.overlay.padding,
      }}
    >
      <div ref={cardRef}
        onClick={(e) => e.stopPropagation()}
        style={{
          position: 'relative',
          width: '100%', maxWidth: t.card.maxWidth,
          height: t.card.height,
          borderRadius: t.card.borderRadius,
          overflow: 'hidden',
          backgroundColor: parentUltraBlurColors ? t.card.bgUltraBlur : CARD_BG,
          boxShadow: t.card.boxShadow,
          display: 'flex', flexDirection: 'column',
        }}
      >
        {/* Close X */}
        <button
          type="button" onClick={handleClose}
          style={{
            position: 'absolute', top: t.close.top, right: t.close.right, zIndex: 10,
            background: 'rgba(0,0,0,0.3)', border: 'none', padding: '6px', borderRadius: '50%',
            minWidth: '44px', minHeight: '44px',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: '#fff', cursor: 'pointer', opacity: 0.85,
            transition: 'opacity 0.2s ease, background 0.2s ease',
            filter: 'drop-shadow(0 1px 3px rgba(0,0,0,0.6))',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.opacity = '1'; e.currentTarget.style.background = 'rgba(0,0,0,0.45)'; }}
          onMouseLeave={(e) => { e.currentTarget.style.opacity = '0.85'; e.currentTarget.style.background = 'rgba(0,0,0,0.3)'; }}
          aria-label="Close"
        >
          <svg width={t.close.svgSize} height={t.close.svgSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>

        {/* Backdrop — reuse show backdrop */}
        {showBackdropUrl && (
          <div style={{
            position: 'absolute', inset: 0, zIndex: 0, overflow: 'hidden',
            borderRadius: t.card.borderRadius,
            opacity: phase === 'visible' ? 1 : 0,
            transition: 'opacity 0.4s ease-out',
          }}>
            <img
              src={showBackdropUrl}
              alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center top',
                ...(t.backdrop.imgFilter ? { filter: t.backdrop.imgFilter } : {}),
              }}
            />
            <div style={{ position: 'absolute', inset: 0, background: 'radial-gradient(ellipse at 50% 30%, transparent 40%, rgba(0,0,0,0.2) 100%)', pointerEvents: 'none' }} />
          </div>
        )}

        {/* UltraBlur color layer */}
        {parentUltraBlurColors && (
          <div style={{
            position: 'absolute', inset: 0, zIndex: 1, pointerEvents: 'none',
            WebkitMaskImage: t.ultraBlurMask, maskImage: t.ultraBlurMask,
          }}>
            <UltraBlurOverlay hold={titleState === 'hold'} colors={parentUltraBlurColors} variant={t.ultraBlurVariant} animationDelay={300} />
          </div>
        )}

        {/* Readability scrim */}
        <div style={{
          position: 'absolute', inset: 0, zIndex: 2, pointerEvents: 'none',
          background: t.scrim, borderRadius: t.card.borderRadius,
        }} />

        {/* Content container */}
        <div data-scroll-container className="luma-scroll-y" data-reveal={titleState !== 'hold' ? 'shown' : 'hold'} style={{
          ...revealStyle(titleState),
          position: 'relative', zIndex: 3,
          display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
          height: '100%', overflowY: 'auto', overflowX: 'hidden',
          WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y',
        }}>

          {/* === Clearlogo — same as show, always visible === */}
          <div style={{
            position: 'absolute', top: 0, left: 0, right: 0, bottom: '35%',
            zIndex: 9, pointerEvents: 'none',
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            padding: t.contentPadding,
            opacity: phase === 'visible' ? 1 : 0,
            transition: 'none', // the container's single reveal fades it
            willChange: 'opacity',
          }}>
            <div style={{
              maxWidth: t.logo.maxWidth,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              transition: 'transform 0.5s cubic-bezier(0.22, 1, 0.36, 1)',
              transform: shelfOpen ? 'translateY(-15vh)' : 'translateY(0)',
            }}>
              {titleState === 'logo' && show.clearLogoUrl ? (
                <img
                  src={show.clearLogoUrl}
                  alt={show.title}
                  style={{ maxWidth: '100%', maxHeight: t.logo.maxHeight, objectFit: 'contain', filter: t.logo.filter }}
                  onError={(e) => {
                    const img = e.target as HTMLImageElement;
                    img.style.display = 'none';
                    const fallback = img.nextElementSibling as HTMLElement | null;
                    if (fallback) fallback.style.display = 'block';
                  }}
                />
              ) : null}
              <h1 style={{
                ...titleTextStyle(titleState),
                fontSize: t.titleFontSize, fontWeight: 700, color: '#fff',
                lineHeight: 1.15, margin: 0,
                textShadow: '0 2px 12px rgba(0,0,0,0.6)', letterSpacing: '-0.02em',
              }}>
                {show.title}
              </h1>
            </div>
          </div>

          {/* === Bottom content — meta + plot + pills === */}
          <div style={{
            opacity: shelfOpen ? 0 : (phase === 'visible' ? 1 : 0),
            transition: shelfOpen ? 'none' : 'opacity 1s ease-in-out 0.8s',
            willChange: 'opacity',
            display: 'flex', flexDirection: 'column', height: '100%',
            padding: t.contentPadding, textAlign: 'center',
          }}>
            {/* Top spacer — pushes content to bottom */}
            <div style={{ flex: '1 1 0' }} />
            <div style={{ flexShrink: 0 }}>
              {/* Info block */}
              <div style={{ marginBottom: isTablet ? '80px' : '100px' }}>
                {/* Season title — large */}
                <h2 style={{
                  fontSize: isTablet ? '28px' : '36px', fontWeight: 700, color: '#fff',
                  margin: '0 0 16px',
                  textShadow: '0 2px 8px rgba(0,0,0,0.6)',
                  letterSpacing: '-0.01em',
                }}>
                  {seasonEyebrow(season.name, season.season_number) && (
                    <span data-season-eyebrow style={{ display: 'block', fontSize: isTablet ? '14px' : '16px', fontWeight: 600, color: 'rgba(255,255,255,0.6)', marginBottom: '4px' }}>
                      {seasonEyebrow(season.name, season.season_number)}
                    </span>
                  )}
                  {seasonTitle(season.name, season.season_number)}
                </h2>

                {/* Meta line */}
                {metaItems.length > 0 && (
                  <div style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    gap: t.meta.gap, flexWrap: 'wrap',
                    fontSize: t.meta.fontSize, fontWeight: 500,
                    color: 'rgba(255,255,255,0.85)',
                    marginBottom: effectiveRatings ? '12px' : '20px',
                    textShadow: '0 1px 4px rgba(0,0,0,0.5)',
                  }}>
                    {metaItems.map((item, i) => (
                      <span key={i} style={{ display: 'flex', alignItems: 'center', gap: t.meta.gap }}>
                        {i > 0 && <span style={{ color: 'rgba(255,255,255,0.25)' }}>&middot;</span>}
                        <span>{item}</span>
                      </span>
                    ))}
                  </div>
                )}
                <div style={{ margin: '-6px 0 16px' }}>
                  <SonarrSeasonLine
                    season={season.season_number}
                    have={season.episodes.filter((e) => ownership[episodeKey(season.season_number, e.episode_number)]?.owned).length}
                    total={season.episodes.length}
                    fontSize={isTablet ? 14 : 15}
                  />
                </div>

                {/* Ratings */}
                {effectiveRatings && (
                  <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '18px' }}>
                    <RatingsBar ratings={effectiveRatings} size="lg" />
                  </div>
                )}

                {/* Season plot */}
                {season.overview && (
                  <p style={{
                    fontSize: t.plot.fontSize, lineHeight: t.plot.lineHeight,
                    color: 'rgba(255,255,255,0.88)', margin: '0 auto 0',
                    maxWidth: t.plot.maxWidth,
                    textShadow: '0 1px 4px rgba(0,0,0,0.5)',
                    display: '-webkit-box', WebkitLineClamp: t.plot.lineClamp,
                    WebkitBoxOrient: 'vertical' as const, overflow: 'hidden',
                  }}>
                    {season.overview}
                  </p>
                )}
              </div>

              {/* Action pills */}
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                gap: t.pill.gap, flexWrap: 'wrap',
              }}>
                {/* Primary: Watch or Watchlist — only show Watch when season has episodes */}
                {showOwned && season.episodes.length > 0 ? (
                  <PillButton
                    focusKey="sd-watch" zoneId="season-detail-modal"
                    primary padding={t.pill.padding} fontSize={t.pill.fontSize}
                    icon={<svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="currentColor"><polygon points="6,3 20,12 6,21" /></svg>}
                    label="Watch"
                    onClick={() => { if (show.ratingKey) onPlayEpisode?.(show.ratingKey, show.title ?? 'TV show', season.season_number, 1); }}
                  />
                ) : seasonMonitored ? (
                  <PillButton
                    focusKey="sd-sonarr" zoneId="season-detail-modal"
                    primary padding={t.pill.padding} fontSize={t.pill.fontSize}
                    icon={<svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12" /></svg>}
                    label="Monitoring season"
                    onClick={() => {}}
                  />
                ) : (
                  <PillButton
                    focusKey="sd-watchlist" zoneId="season-detail-modal" tone="blue" testid="tv-add-season-watchlist-desktop"
                    padding={t.pill.padding} fontSize={t.pill.fontSize}
                    icon={<svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>}
                    label={addInFlight ? 'Adding…' : addFailed ? 'Retry — add failed' : 'Add season to watchlist'}
                    onClick={async () => {
                      if (addInFlight) return;              // guard rapid double-clicks
                      setAddFailed(false);
                      setAddInFlight(true);
                      // Only flip to "Monitoring Season" once the add truly succeeds.
                      const ok = await onAddSeasonToWatchlist?.(season.season_number);
                      setAddInFlight(false);
                      if (ok) setJustAdded(true);
                      else setAddFailed(true);
                    }}
                  />
                )}

                {/* Episodes — only show when season has episodes */}
                {season.episodes.length > 0 && (
                  <PillButton
                    focusKey="sd-episodes" zoneId="season-detail-modal"
                    padding={t.pill.padding} fontSize={t.pill.fontSize}
                    icon={<svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="7" width="20" height="15" rx="2" ry="2" /><polyline points="17 2 12 7 7 2" /></svg>}
                    label="Episodes"
                    onClick={() => { setShowCastShelf(false); setShowEpisodeGrid(true); }}
                  />
                )}

                {/* Sources (owner) */}
                {onShowSources && (
                  <PillButton
                    focusKey="sd-sources" zoneId="season-detail-modal"
                    padding={t.pill.padding} fontSize={t.pill.fontSize}
                    icon={<svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" /></svg>}
                    label="Sources"
                    onClick={() => { handleClose(); onShowSources?.({ type: 'season', season: season.season_number }); }}
                  />
                )}

                {/* Cast */}
                {(castForDisplay.length > 0 || creatorsForDisplay.length > 0) && (
                  <PillButton
                    focusKey="sd-cast" zoneId="season-detail-modal"
                    padding={t.pill.padding} fontSize={t.pill.fontSize}
                    icon={<svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></svg>}
                    label="Cast"
                    onClick={() => { setShowCastShelf(prev => !prev); }}
                  />
                )}
              </div>
            </div>
          </div>
        </div>

        {/* === SHELVES === */}

        {/* Cast shelf */}
        {showCastShelf && (
          <CastShelf
            cast={deduplicateCastAndCrew(creatorsForDisplay, castForDisplay, 'Creator')}
            isTablet={isTablet}
            onPersonSearch={(name, personId) => {
              if (personId) {
                seasonModalHost.openPerson({ personId, personName: name });
                setShowCastShelf(false);
              } else {
                onPersonSearch?.(name, personId);
              }
            }}
            onClose={() => setShowCastShelf(false)}
          />
        )}

        <style>{`
          @keyframes tvExpandedSpin { to { transform: rotate(360deg); } }
        `}</style>
      </div>

      {/* Episode grid modal */}
      {showEpisodeGrid && (
        <EpisodeGridModal
          seasons={[season]}
          ownership={ownership}
          selectedSeason={season.season_number}
          onSelectSeason={() => {}}
          onEpisodeClick={(s, e) => {
            setShowEpisodeGrid(false);
            onEpisodeClick?.(s, e);
          }}
          isTablet={isTablet}
          onClose={() => setShowEpisodeGrid(false)}
          onPlayEpisode={(rk, title, s, e) => onPlayEpisode?.(rk, title, s, e)}
          showTitle={show.title}
          onEpisodeSources={onEpisodeSources}
        />
      )}
    </div></Hold>,
    document.body,
  );
}

// ---------------------------------------------------------------------------
// Main component — cinema-style expanded layout
// ---------------------------------------------------------------------------

export function ExpandedLayout(props: TvModalViewProps) {
  const { user } = useAuth();
  const lumaMon = useLumaSeries(props.resolvedTmdbId);
  const isOwner = !!user?.is_admin;
  const {
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
    showFullPlot,
    showAddToSonarr,
    showSources,
    onClose,
    setShowSources,
    setShowFullPlot,
    setShowAddToSonarr,
    onPersonSearch,
    onSonarrAdded,
    onSourcesGrabbed,
    // Episode mode
    episodeContext,
    episodeData,
    episodeLoading,
    showFullShow,
    onEpisodeClick,
  } = props;
  const titleState = useTitleReveal(logoInput(show), show?.title, show?.backdropUrl || show?.posterUrl);
  // ONE backdrop per open (useSheetBackdrop): the text-free pick when it arrives before the latch, never swapped after.
  const sheetHero = useSheetHero(finalBackdropUrl(show?.backdropTextlessUrl ?? show?.backdropUrl), !!show && !show.backdropPending, show?.clearLogoUrl, logoInput(show) !== undefined, props.openKey);
  const sheetArt = sheetHero.art;

  // Determine if we're in active episode-mode display
  const isEpisodeIntent = !showFullShow && !!episodeContext;
  const inEpisodeMode = isEpisodeIntent && !!episodeData && !episodeLoading;

  // Local rendering-only state
  const [showCastShelf, setShowCastShelf] = useState(false);
  const [showEpisodeGrid, setShowEpisodeGrid] = useState(false);
  const [showSeasonGrid, setShowSeasonGrid] = useState(false);
  const [seasonDetailTarget, setSeasonDetailTarget] = useState<number | null>(null);
  const modalHost = useModalHost();
  const { isTablet } = useLayoutMode();
  const t = isTablet ? TOKENS.tablet : TOKENS.desktop;
  const perf = useTabletPerf();
  const overlayRef = useRef<HTMLDivElement>(null);
  const logoRef = useRef<HTMLDivElement>(null);

  // Effective ratings — IMDb via OMDb (never TMDb vote_average)
  const effectiveRatings = useImdbRatings(show?.title, show?.year);

  // Trigger reviews fetch when detail loads
  useEffect(() => {
  }, [show]);

  // Derived
  // Episode modal uses episode cast/guest stars; series modal uses show cast.
  const castForDisplay = (inEpisodeMode && episodeData?.cast && episodeData.cast.length > 0)
    ? episodeData.cast
    : (show?.cast?.slice(0, 30) ?? []);
  const creatorsForDisplay = show?.creators ?? [];
  const seasonsCountDisplay = show?.numberOfSeasons
    ? seasonsLabel(show.numberOfSeasons)
    : seasons.length > 0
      ? `${seasons.length} Season${seasons.length !== 1 ? 's' : ''}`
      : '';

  // Build meta info items
  const metaItems: string[] = [];
  if (show?.year) metaItems.push(String(show.year));
  if (seasonsCountDisplay) metaItems.push(seasonsCountDisplay);
  if (show?.network) metaItems.push(show.network);
  if (show?.contentRating) metaItems.push(show.contentRating);

  // Any shelf open
  const shelfOpen = showCastShelf;

  // Auto-select first season when opening episode grid
  useEffect(() => {
    const first = seasons[0];
    if (showEpisodeGrid && selectedSeason === -1 && first) {
      setSelectedSeason(first.season_number);
    }
  }, [showEpisodeGrid, selectedSeason, seasons, setSelectedSeason]);

  // ---------------------------------------------------------------------------
  // Shelf handlers
  // ---------------------------------------------------------------------------
  const handleCastOpen = useCallback(() => {
    setShowCastShelf(prev => !prev);
  }, []);

  const handleSeasonsOpen = useCallback(() => {
    setShowCastShelf(false);
    setShowSeasonGrid(true);
  }, []);

  // Add a single season as monitored + immediately search Sonarr for it.
  // Returns whether it actually succeeded so the button doesn't falsely flip
  // to "Monitoring Season" when Sonarr is down / the add failed.
  const handleAddSeasonToWatchlist = useCallback(async (season: number): Promise<boolean> => {
    if (!tvdbId) return false;
    try {
      const result = await autoSearchSeason(tvdbId, season, resolvedTmdbId ?? undefined);
      if (result?.success) {
        onSonarrAdded?.();
        return true;
      }
      return false;
    } catch {
      return false;
    }
  }, [tvdbId, resolvedTmdbId, onSonarrAdded]);

  // Close shelf on Escape
  useEffect(() => {
    if (!shelfOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopImmediatePropagation();
        setShowCastShelf(false);
      }
    };
    window.addEventListener('keydown', handler, true);
    return () => window.removeEventListener('keydown', handler, true);
  }, [shelfOpen]);

  // ---------------------------------------------------------------------------
  // Episode search handler
  // ---------------------------------------------------------------------------

  // ---------------------------------------------------------------------------
  // Lock body scroll
  // ---------------------------------------------------------------------------
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, []);

  // ---------------------------------------------------------------------------
  // Overlay click closes
  // ---------------------------------------------------------------------------
  const handleOverlayClick = useCallback(
    (e: React.MouseEvent) => { if (e.target === overlayRef.current) onClose(); },
    [onClose],
  );

  // ---------------------------------------------------------------------------
  // Animation
  // ---------------------------------------------------------------------------
  const cardRef = useRef<HTMLDivElement>(null);
  useModalMotion(phase, overlayRef, cardRef);

  if (phase === 'gone') return null;

  return (
    <LumaMonitorContext.Provider value={lumaMon}>
    <>
    {createPortal(
      <div className="luma-fullscreen luma-bleed"
        data-modal="TvDetailModal"
        data-variant="web-cinema"
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
          padding: t.overlay.padding,
        }}
      >
        {/* Card */}
        <div ref={cardRef}
          style={{
            position: 'relative',
            width: '100%',
            maxWidth: t.card.maxWidth,
            height: t.card.height,
            borderRadius: t.card.borderRadius,
            overflow: 'hidden',
            backgroundColor: ultraBlurColors ? t.card.bgUltraBlur : CARD_BG,
            boxShadow: t.card.boxShadow,
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          {/* Close X */}
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
            <svg width={t.close.svgSize} height={t.close.svgSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>

          {/* Backdrop — full-bleed background */}
          {!loading && !error && show && (
            <div
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
              {sheetArt ? (
                <img
                  key={sheetArt}
                  src={perf.limitBackdropResolution && sheetArt.includes('/t/p/original')
                    ? sheetArt.replace('/t/p/original', '/t/p/w1280')
                    : sheetArt}
                  alt=""
                  style={{
                    ...revealStyle(sheetHero.state), animation: HERO_ANIM,
                    width: '100%',
                    height: '100%',
                    objectFit: 'cover',
                    objectPosition: 'center top',
                    ...(t.backdrop.imgFilter ? { filter: t.backdrop.imgFilter } : {}),
                  }}
                />
              ) : !show.backdropUrl && show.posterUrl ? (
                <img key={show.posterUrl} src={show.posterUrl} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', opacity: titleState === 'hold' ? 0 : 0.6, transition: 'opacity 0.45s ease-out' }} />
              ) : null}
              {show.backdropUrl && (
                <div style={{ position: 'absolute', inset: 0, background: 'radial-gradient(ellipse at 50% 30%, transparent 40%, rgba(0,0,0,0.2) 100%)', pointerEvents: 'none' }} />
              )}
            </div>
          )}

          {/* UltraBlur color layer */}
          {ultraBlurColors && (
            <div style={{
              position: 'absolute', inset: 0, zIndex: 1, pointerEvents: 'none',
              WebkitMaskImage: t.ultraBlurMask,
              maskImage: t.ultraBlurMask,
            }}>
              <UltraBlurOverlay hold={titleState === 'hold'} colors={ultraBlurColors} variant={t.ultraBlurVariant} animationDelay={300} />
            </div>
          )}

          {/* Readability scrim */}
          <div style={{
            position: 'absolute', inset: 0, zIndex: 2, pointerEvents: 'none',
            background: t.scrim,
            borderRadius: t.card.borderRadius,
          }} />

          {/* Content — flex column, justify-end */}
          <div data-reveal={loading || titleState !== 'hold' ? 'shown' : 'hold'} style={{
            ...revealStyle(loading ? 'text' : titleState),
            position: 'relative',
            zIndex: 3,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'flex-end',
            height: '100%',
            overflowY: 'auto',
            overflowX: 'hidden',
          }}>

            {/* Loading state */}
            {loading && (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1 }}>
                <div style={{
                  width: '48px', height: '48px', borderRadius: '50%',
                  border: '2px solid rgba(255,255,255,0.15)', borderTopColor: 'rgba(255,255,255,0.7)',
                  animation: 'tvExpandedSpin 0.8s linear infinite',
                }} />
              </div>
            )}

            {/* Error state */}
            {!loading && error && (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '20px', flex: 1 }}>
                <p style={{ fontSize: '22px', color: 'rgba(255,255,255,0.7)' }}>Couldn’t load details</p>
                <button type="button" onClick={onClose} style={{
                  padding: '12px 29px', borderRadius: '10px', border: 'none',
                  background: 'var(--color-accent)', color: 'rgba(255,255,255,0.95)', fontSize: '18px', cursor: 'pointer',
                }}>
                  Close
                </button>
              </div>
            )}

            {/* Content */}
            {!loading && !error && show && (
              <>
                {/* === Clearlogo — always visible, even over shelves === */}
                <div
                  ref={logoRef}
                  style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    right: 0,
                    bottom: '20%',
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
                  <div style={{
                    maxWidth: t.logo.maxWidth,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    opacity: sheetHero.state === 'hold' ? 0 : 1,
                    transition: `transform 0.5s cubic-bezier(0.22, 1, 0.36, 1), ${HERO_FADE}`,
                    transform: shelfOpen ? 'translateY(-15vh)' : 'translateY(0)',
                  }}>
                    {sheetHero.state === 'logo' && sheetHero.logo ? (
                      <img
                        src={sheetHero.logo}
                        alt={show.title}
                        style={{
                          maxWidth: '100%',
                          maxHeight: t.logo.maxHeight,
                          objectFit: 'contain',
                          filter: t.logo.filter,
                        }}
                        onError={sheetHero.logoFailed}
                      />
                    ) : null}
                    <h1 style={{
                      ...titleTextStyle(sheetHero.state),
                      fontSize: t.titleFontSize,
                      fontWeight: 700,
                      color: '#fff',
                      lineHeight: 1.15,
                      margin: 0,
                      textShadow: '0 2px 12px rgba(0,0,0,0.6)',
                      letterSpacing: '-0.02em',
                    }}>
                      {show.title}
                    </h1>
                  </div>
                </div>

                {/* === CONTENT — meta+plot+pills at bottom === */}
                <div style={{
                  opacity: shelfOpen ? 0 : (phase === 'visible' ? 1 : 0),
                  transition: shelfOpen ? 'none' : 'opacity 1s ease-in-out 0.8s',
                  willChange: 'opacity',
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'flex-end',
                  height: '100%',
                  padding: t.contentPadding,
                  paddingBottom: isTablet ? '32px' : '48px',
                  textAlign: 'center',
                }}>
                  {/* Bottom group */}
                  <div style={{ flexShrink: 0 }}>

                    {/* Episode mode: loading spinner */}
                    {isEpisodeIntent && !inEpisodeMode && (
                      <div style={{ display: 'flex', justifyContent: 'center', padding: '40px 0' }}>
                        <div style={{
                          width: '28px', height: '28px', borderRadius: '50%',
                          border: '2px solid rgba(255,255,255,0.15)', borderTopColor: 'rgba(255,255,255,0.7)',
                          animation: 'tvExpandedSpin 0.8s linear infinite',
                        }} />
                      </div>
                    )}

                    {/* Episode mode: episode header */}
                    {inEpisodeMode && episodeData && (
                      <EpisodeHeader
                        episodeData={episodeData}
                        size="md"
                        onPlay={episodeData.isOwned && episodeContext?.episodeRatingKey
                          ? () => props.onPlayEpisode?.(
                              episodeContext!.episodeRatingKey!,
                              `${show?.title ?? ''} - S${episodeData.seasonNumber}E${episodeData.episodeNumber} - ${episodeData.title}`,
                            )
                          : undefined}
                        onViewShow={() => { props.setShowFullShow(true); setSelectedSeason(-1); }}
                        onOpenSources={isOwner
                          ? () => setShowSources({
                              type: 'episode',
                              season: episodeData.seasonNumber,
                              episode: episodeData.episodeNumber,
                            })
                          : undefined}
                      />
                    )}

                    {/* Show mode content */}
                    {!isEpisodeIntent && (
                      <>
                        {/* Info block — shifted up from pills */}
                        <div style={{ marginBottom: isTablet ? '80px' : '100px' }}>
                          {/* Meta line */}
                          {metaItems.length > 0 && (
                            <div style={{
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              gap: t.meta.gap,
                              flexWrap: 'wrap',
                              fontSize: t.meta.fontSize,
                              fontWeight: 500,
                              color: 'rgba(255,255,255,0.85)',
                              marginBottom: effectiveRatings ? '12px' : '20px',
                              textShadow: '0 1px 4px rgba(0,0,0,0.5)',
                            }}>
                              {metaItems.map((item, i) => (
                                <span key={i} style={{ display: 'flex', alignItems: 'center', gap: t.meta.gap }}>
                                  {i > 0 && <span style={{ color: 'rgba(255,255,255,0.25)' }}>&middot;</span>}
                                  <span>{item}</span>
                                </span>
                              ))}
                            </div>
                          )}
                          <div style={{ marginTop: '-6px', marginBottom: '16px' }}>
                            <SonarrShowStatus fontSize={isTablet ? 15 : 16} marginTop={0} />
                          </div>

                          {/* Ratings */}
                          {effectiveRatings && (
                            <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '18px' }}>
                              <RatingsBar ratings={effectiveRatings} size="lg" />
                            </div>
                          )}

                          {/* Plot */}
                          {show.summary && (
                            <p style={{
                              fontSize: t.plot.fontSize,
                              lineHeight: t.plot.lineHeight,
                              color: 'rgba(255,255,255,0.88)',
                              margin: '0 auto 0',
                              maxWidth: t.plot.maxWidth,
                              textShadow: '0 1px 4px rgba(0,0,0,0.5)',
                              display: '-webkit-box',
                              WebkitLineClamp: t.plot.lineClamp,
                              WebkitBoxOrient: 'vertical' as const,
                              overflow: 'hidden',
                            }}>
                              {show.summary}
                            </p>
                          )}
                        </div>

                        {/* Action pills */}
                        <div data-modal-actions style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          gap: t.pill.gap,
                          flexWrap: 'wrap',
                        }}>
                          {/* Primary action: Watch or Watchlist */}
                          {showOwned ? (
                            <span data-modal-keep="" data-owned-watch="" style={{ display: 'contents' }}>
                            <PillButton
                              focusKey="tm-watch"
                              zoneId="tv-detail-modal"
                              primary
                              padding={t.pill.padding}
                              fontSize={t.pill.fontSize}
                              icon={
                                <svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="currentColor">
                                  <polygon points="6,3 20,12 6,21" />
                                </svg>
                              }
                              label="Watch"
                              onClick={() => {
                                if (show?.ratingKey) {
                                  props.onPlayEpisode?.(show.ratingKey, show.title ?? 'TV show');
                                }
                              }}
                            />
                            </span>
                          ) : sonarrStatus?.exists ? (
                            <PillButton
                              focusKey="tm-sonarr-status"
                              zoneId="tv-detail-modal"
                              primary
                              padding={t.pill.padding}
                              fontSize={t.pill.fontSize}
                              icon={
                                <svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                  <polyline points="20 6 9 17 4 12" />
                                </svg>
                              }
                              label="In Sonarr"
                              onClick={() => {}}
                            />
                          ) : (
                            /* v158b: the blue Add to watchlist with a + (the phone's button, on desktop) */
                            <span data-modal-keep="" style={{ display: 'contents' }}>
                            <PillButton
                              focusKey="tm-watchlist"
                              zoneId="tv-detail-modal"
                              tone="blue"
                              testid="tv-add-watchlist-desktop"
                              padding={t.pill.padding}
                              fontSize={t.pill.fontSize}
                              icon={
                                <svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                  <line x1="12" y1="5" x2="12" y2="19" />
                                  <line x1="5" y1="12" x2="19" y2="12" />
                                </svg>
                              }
                              label="Add to watchlist"
                              onClick={() => setShowAddToSonarr(true)}
                            />
                            </span>
                          )}

                          {/* v158a: the Luma TV monitor toggle. data-modal-keep keeps it (and the
                              Seasons pill, the way to the season/episode toggles) in minimal builds,
                              whose popup otherwise hides its actions. Only for a show in Luma TV. */}
                          {lumaMon && <span data-modal-keep="" style={{ display: 'contents' }}><SeriesMark /></span>}

                          {/* Seasons — available as soon as show meta or light
                              season summary is known; do not wait on full grid. */}
                          {(seasons.length > 0 || (show?.numberOfSeasons ?? 0) > 0) && (
                            <span data-modal-keep="" style={{ display: 'contents' }}>
                            <PillButton
                              focusKey="tm-seasons"
                              zoneId="tv-detail-modal"
                              padding={t.pill.padding}
                              fontSize={t.pill.fontSize}
                              icon={
                                <svg width={t.pill.iconSize} height={t.pill.iconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                  <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
                                  <line x1="8" y1="21" x2="16" y2="21" />
                                  <line x1="12" y1="17" x2="12" y2="21" />
                                </svg>
                              }
                              label="Seasons"
                              onClick={handleSeasonsOpen}
                            />
                            </span>
                          )}

                          {/* Sources (owner) */}
                          {isOwner && (
                          <span data-modal-sources="" style={{ display: 'contents' }}>
                          <PillButton
                            focusKey="tm-sources"
                            zoneId="tv-detail-modal"
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
                            onClick={() => {
                              const season = selectedSeason >= 0 ? selectedSeason : (seasons[0]?.season_number ?? 1);
                              setShowSources({ type: 'season', season });
                            }}
                          />
                          </span>
                          )}

                          {/* Cast */}
                          {/* v217: Seasons and Cast show in minimal builds too, as prod 1b4ea74. */}
                          {(castForDisplay.length > 0 || creatorsForDisplay.length > 0) && (
                            <span data-modal-keep="" style={{ display: 'contents' }}>
                            <PillButton
                              focusKey="tm-cast"
                              zoneId="tv-detail-modal"
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
                              label="Cast"
                              onClick={handleCastOpen}
                            />
                            </span>
                          )}
                        </div>
                      </>
                    )}

                  </div>{/* end bottom group */}
                </div>{/* end content area */}
              </>
            )}
          </div>{/* end content container */}

          {/* === SHELVES === */}

          {/* Cast shelf */}
          {showCastShelf && show && (
            <CastShelf
              cast={deduplicateCastAndCrew(creatorsForDisplay, castForDisplay, 'Creator')}
              isTablet={isTablet}
              onPersonSearch={(name, personId) => {
                if (personId) {
                  modalHost.openPerson({ personId, personName: name });
                  setShowCastShelf(false);
                } else {
                  onPersonSearch?.(name, personId);
                }
              }}
              onClose={() => setShowCastShelf(false)}
            />
          )}

          {/* Playback modal */}
          {props.showPlayback && props.playbackRatingKey && (
            <PlaybackModal
              ratingKey={props.playbackRatingKey}
              title={props.playbackTitle ?? show?.title ?? ''}
              mediaType="tv"
              onClose={() => props.setShowPlayback(false)}
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

          {/* Full plot modal */}
          {showFullPlot && show?.summary && (
            <FullPlotModal
              text={show.summary}
              onClose={() => setShowFullPlot(false)}
            />
          )}

          {/* Sources panel */}
          {showSources && show && (
            <TvSourcesPanel
              target={showSources}
              targetName={sourcesTargetName(showSources, seasons, episodeData)}
              tvdbId={tvdbId ?? 0}
              tmdbId={resolvedTmdbId}
              title={show.title}
              year={show.year}
              seedQuery={props.searchQuery}
              onClose={() => setShowSources(null)}
              onGrabbed={() => {
                setShowSources(null);
                onSourcesGrabbed?.();
              }}
            />
          )}

          {/* Grab toast */}
          {props.grabToast && (
            <div style={{
              position: 'absolute', bottom: '32px', left: '50%', transform: 'translateX(-50%)',
              background: 'rgba(34,197,94,0.95)', color: '#fff',
              padding: '12px 28px', borderRadius: '12px', fontSize: '16px', fontWeight: 600,
              boxShadow: '0 8px 30px rgba(0,0,0,0.5)',
              animation: 'tvToastIn 0.25s ease', whiteSpace: 'nowrap', zIndex: 100,
            }}>
              Added to Download Client!
            </div>
          )}

          {/* Watchlist toast */}
          {props.watchlistToast && (
            <div style={{
              position: 'absolute', bottom: '32px', left: '50%', transform: 'translateX(-50%)',
              background: 'rgba(34,197,94,0.95)', color: '#fff',
              padding: '12px 28px', borderRadius: '12px', fontSize: '16px', fontWeight: 600,
              boxShadow: '0 8px 30px rgba(0,0,0,0.5)',
              animation: 'tvToastIn 0.25s ease', whiteSpace: 'nowrap', zIndex: 100,
            }}>
              Added to Sonarr!
            </div>
          )}

          {/* Episode search toast */}

          {/* Keyframes */}
          <style>{`
            @keyframes tvExpandedSpin { to { transform: rotate(360deg); } }
            @keyframes tvToastIn { from { opacity: 0; transform: translateX(-50%) translateY(10px); } to { opacity: 1; transform: translateX(-50%) translateY(0); } }
            @keyframes tvCastShelfBounce { from { transform: translateY(100%); } to { transform: translateY(0); } }
          `}</style>
        </div>
      </div>,
      document.body,
    )}
    {/* Season grid modal — open immediately; show spinner until light/full seasons arrive */}
    {showSeasonGrid && (
      seasons.length > 0 ? (
        <SeasonGridModal
          seasons={seasons}
          ownership={ownership}
          showTitle={show?.title ?? ''}
          isTablet={isTablet}
          onSelectSeason={(num) => {
            setShowSeasonGrid(false);
            setSeasonDetailTarget(num);
          }}
          onSeasonSources={isOwner ? (num) => setShowSources({ type: 'season', season: num }) : undefined}
          onClose={() => setShowSeasonGrid(false)}
        />
      ) : (
        <div className="luma-fullscreen luma-bleed"
          style={{
            position: 'fixed', inset: 0, zIndex: 10001,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'rgba(0,0,0,0.55)',
          }}
          onClick={() => { if (!seasonsLoading) setShowSeasonGrid(false); }}
        >
          <div style={{ color: '#fff', fontSize: 16, fontWeight: 600 }}>
            {seasonsLoading ? 'Loading seasons…' : 'No seasons found'}
          </div>
        </div>
      )
    )}
    {/* Season detail modal */}
    {seasonDetailTarget !== null && show && (() => {
      const targetSeason = seasons.find(s => s.season_number === seasonDetailTarget);
      if (!targetSeason) return null;
      return (
        <SeasonDetailModal
          show={show}
          season={targetSeason}
          showBackdropUrl={show.backdropUrl}
          ultraBlurColors={ultraBlurColors}
          ownership={ownership}
          showOwned={showOwned}
          sonarrStatus={sonarrStatus}
          castForDisplay={castForDisplay}
          creatorsForDisplay={creatorsForDisplay}
          isTablet={isTablet}
          onEpisodeClick={(s, e) => {
            setSeasonDetailTarget(null);
            onEpisodeClick?.(s, e);
          }}
          onPersonSearch={onPersonSearch}
          onPlayEpisode={(rk, title, s, e) => {
            setSeasonDetailTarget(null);
            props.onPlayEpisode?.(rk, `${show.title ?? 'TV show'} - S${s}E${e} - ${title}`);
          }}
          onShowSources={isOwner ? (target) => {
            setSeasonDetailTarget(null);
            setShowSources(target);
          } : undefined}
          onEpisodeSources={isOwner ? (s, e) => setShowSources({ type: 'episode', season: s, episode: e }) : undefined}
          onAddSeasonToWatchlist={(s) => handleAddSeasonToWatchlist(s)}
          onClose={() => setSeasonDetailTarget(null)}
        />
      );
    })()}
    {/* Episode grid modal */}
    {showEpisodeGrid && !seasonsLoading && seasons.length > 0 && (
      <EpisodeGridModal
        seasons={seasons}
        ownership={ownership}
        selectedSeason={selectedSeason >= 0 ? selectedSeason : (seasons[0]?.season_number ?? 1)}
        onSelectSeason={setSelectedSeason}
        onEpisodeClick={(s, e) => {
          setShowEpisodeGrid(false);
          onEpisodeClick?.(s, e);
        }}
        isTablet={isTablet}
        onClose={() => setShowEpisodeGrid(false)}
        onPlayEpisode={(rk, title, s, e) => {
          setShowEpisodeGrid(false);
          props.onPlayEpisode?.(rk, `${show?.title ?? 'TV show'} - S${s}E${e} - ${title}`);
        }}
        onEpisodeSources={isOwner ? (s, e) => setShowSources({ type: 'episode', season: s, episode: e }) : undefined}
      />
    )}
    </>
    </LumaMonitorContext.Provider>
  );
}

/** v158b (Designer): the Episodes/Seasons overlays are opaque (nothing of the detail page shows
 * through); cards are glass .06 with a .08 border; missing stills glass .04; chips glass .10. */
const OVERLAY_SURFACE = '#111318';
const CARD_GLASS: React.CSSProperties = {
  background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '14px', padding: '10px',
};
const PLACEHOLDER_GLASS = 'rgba(255,255,255,0.04)';
const CHIP_GLASS: React.CSSProperties = {
  background: 'rgba(255,255,255,0.10)', backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)',
};

/** Small label on artwork — matches the episode "E1" badge (glass .10). */
const GRID_PILL: React.CSSProperties = {
  ...CHIP_GLASS,
  borderRadius: '6px',
  padding: '3px 8px',
  fontSize: '12px',
  fontWeight: 700,
  color: 'rgba(255,255,255,0.9)',
  lineHeight: 1.2,
  whiteSpace: 'nowrap',
};
