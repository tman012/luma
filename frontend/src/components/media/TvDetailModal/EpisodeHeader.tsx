/**
 * EpisodeHeader — shared episode-mode header for TvDetailModal.
 *
 * Renders below the show clearlogo when episodeContext + episodeData are present.
 * Shows: episode title, meta row (S/E/date/duration/content rating),
 * ownership-based actions, then expandable episode overview under the buttons
 * (matching series modal: actions → plot → Cast & Crew). No episode thumb.
 *
 * Used by ExpandedLayout and CompactLayout — the `size` prop
 * controls font scaling and padding for each viewport.
 */

import { useRef, useState } from 'react';
import type { EpisodeData } from './types';
import { SonarrEpisodeNote } from './SonarrStatus';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Format a date string (YYYY-MM-DD) to "Jan 15, 2024".
 * Returns null if the input is missing or unparseable.
 */
function formatAirDate(dateStr: string | null): string | null {
  if (!dateStr) return null;
  try {
    const d = new Date(dateStr + 'T00:00:00');
    if (isNaN(d.getTime())) return null;
    return d.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  } catch {
    return null;
  }
}

/**
 * Format runtime in minutes to human-readable string.
 * 42 -> "42 min", 62 -> "1h 2m", 120 -> "2h 0m".
 * Returns null if input is missing or zero.
 */
function formatRuntime(minutes: number | null): string | null {
  if (!minutes || minutes <= 0) return null;
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${h}h ${m}m`;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface EpisodeHeaderProps {
  episodeData: EpisodeData;
  /** Controls font sizing: 'sm' for mobile, 'md' for web, 'lg' for tablet */
  size: 'sm' | 'md' | 'lg';
  /** Called when Play/Resume is clicked for an owned episode */
  onPlay?: () => void;
  /** Called when "View Show" is tapped — navigates to full show view */
  onViewShow?: () => void;
  /** Called when Seasons is tapped — opens seasons shelf (compact) */
  onOpenSeasons?: () => void;
  /** Called when Episodes is tapped - opens episodes sheet for this season (compact) */
  onOpenEpisodes?: () => void;
  /**
   * Tap the episode identity block (title / meta / still) to open Sources.
   * Replaces the old Search button — same destination, better affordance.
   */
  onOpenSources?: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function EpisodeHeader({
  episodeData,
  size,
  onPlay,
  onViewShow,
  onOpenSeasons,
  onOpenEpisodes,
  onOpenSources,
}: EpisodeHeaderProps) {
  const {
    title,
    overview,
    airDate,
    runtime,
    contentRating,
    seasonNumber,
    episodeNumber,
    isOwned,
    viewOffset,
    duration,
  } = episodeData;

  // --- Font sizing by viewport ---
  const titleFontSize =
    size === 'lg' ? '28px' : size === 'md' ? '34px' : '20px';
  const metaFontSize =
    size === 'lg' ? '22px' : size === 'md' ? '22px' : '14px';
  const overviewFontSize =
    size === 'lg' ? '18px' : size === 'md' ? '20px' : '15px';
  const sidePadding =
    size === 'lg' ? '40px' : size === 'md' ? '64px' : '16px';
  const pillIconSize = size === 'lg' ? 20 : size === 'md' ? 22 : 16;
  const pillFontSize = size === 'lg' ? '18px' : size === 'md' ? '20px' : '16px';
  const pillPadding = size === 'lg' ? '16px 32px' : size === 'md' ? '16px 34px' : '14px 20px';
  const overviewLineClamp = size === 'sm' ? 3 : size === 'lg' ? 5 : 4;

  const metaItems: string[] = [];
  if (seasonNumber != null && episodeNumber != null) {
    metaItems.push(`S${seasonNumber} E${episodeNumber}`);
  } else if (seasonNumber != null) {
    metaItems.push(`S${seasonNumber}`);
  }
  const formattedDate = formatAirDate(airDate);
  if (formattedDate) metaItems.push(formattedDate);
  const formattedRuntime = formatRuntime(runtime);
  if (formattedRuntime) metaItems.push(formattedRuntime);
  if (contentRating) metaItems.push(contentRating);

  const hasProgress = isOwned && viewOffset != null && viewOffset > 0 && duration != null && duration > 0;
  const minutesLeft = hasProgress ? Math.max(1, Math.ceil((duration! - viewOffset!) / 60000)) : 0;

  const isCompact = size === 'sm';

  const pillBase = isCompact
    ? {
        display: 'flex' as const,
        alignItems: 'center' as const,
        justifyContent: 'center' as const,
        gap: '10px',
        width: '100%',
        padding: '14px 20px',
        borderRadius: '50px',
        fontSize: '16px',
        fontWeight: 600 as const,
        cursor: 'pointer' as const,
        border: 'none' as const,
        transition: 'background 0.15s ease',
      }
    : {
        display: 'inline-flex' as const,
        alignItems: 'center' as const,
        gap: '8px',
        padding: pillPadding,
        borderRadius: '28px',
        fontSize: pillFontSize,
        fontWeight: 600 as const,
        cursor: 'pointer' as const,
        border: 'none' as const,
        transition: 'background 0.15s ease',
      };

  const primaryPill = {
    ...pillBase,
    background: '#fff',
    color: '#111',
  };

  const secondaryPill = {
    ...pillBase,
    background: 'rgba(255,255,255,0.08)',
    color: '#fff',
    border: '1px solid rgba(255,255,255,0.15)',
    backdropFilter: 'blur(12px)',
    WebkitBackdropFilter: 'blur(12px)',
  };

  const identityInteractive = !!onOpenSources;
  const [plotExpanded, setPlotExpanded] = useState(false);
  const tapRef = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const suppressClickRef = useRef(false);
  const TAP_MOVE_PX = 10;

  const openSources = () => {
    onOpenSources?.();
  };

  // Touchstart/end only — no touchmove listener on the identity block.
  // A touchmove handler here intermittently steals the gesture from the modal
  // scroller (can't scroll back up after reaching the bottom).
  const onIdentityTouchStart = (e: React.TouchEvent) => {
    if (!identityInteractive) return;
    const t = e.touches[0];
    if (!t) return;
    tapRef.current = { x: t.clientX, y: t.clientY, moved: false };
  };

  const onIdentityTouchEnd = (e: React.TouchEvent) => {
    const start = tapRef.current;
    tapRef.current = null;
    if (!identityInteractive || !start) return;
    const t = e.changedTouches[0];
    if (!t) return;
    if (
      Math.abs(t.clientX - start.x) > TAP_MOVE_PX ||
      Math.abs(t.clientY - start.y) > TAP_MOVE_PX
    ) {
      return;
    }
    suppressClickRef.current = true;
    openSources();
  };

  const onIdentityTouchCancel = () => {
    tapRef.current = null;
  };

  const onIdentityClick = () => {
    if (!identityInteractive) return;
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    openSources();
  };

  return (
    <div style={{ textAlign: 'center' }}>
      {/* Identity block — title / meta only (no episode thumb). Clear tap opens Sources. */}
      <div
        role={identityInteractive ? 'button' : undefined}
        tabIndex={identityInteractive ? 0 : undefined}
        onClick={identityInteractive ? onIdentityClick : undefined}
        onTouchStart={identityInteractive ? onIdentityTouchStart : undefined}
        onTouchEnd={identityInteractive ? onIdentityTouchEnd : undefined}
        onTouchCancel={identityInteractive ? onIdentityTouchCancel : undefined}
        onKeyDown={identityInteractive ? (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            openSources();
          }
        } : undefined}
        style={{
          marginBottom: size === 'md' ? '100px' : size === 'lg' ? '80px' : '16px',
          cursor: identityInteractive ? 'pointer' : undefined,
          touchAction: 'pan-y',
        }}
      >
        <h2
          style={{
            fontSize: titleFontSize,
            fontWeight: 700,
            color: '#fff',
            margin: `${size === 'sm' ? '14px' : '0'} 0 0`,
            padding: `0 ${sidePadding}`,
            lineHeight: 1.2,
            textShadow: size === 'md' ? '0 2px 12px rgba(0,0,0,0.6)' : undefined,
            letterSpacing: '-0.01em',
          }}
        >
          {title}
        </h2>

        {metaItems.length > 0 && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: size === 'md' ? '16px' : size === 'lg' ? '14px' : '8px',
              marginTop: size === 'md' ? '14px' : '10px',
              flexWrap: 'wrap',
              fontSize: metaFontSize,
              fontWeight: 500,
              color: 'rgba(255, 255, 255, 0.85)',
              padding: `0 ${sidePadding}`,
              textShadow: size === 'md' ? '0 1px 4px rgba(0,0,0,0.5)' : undefined,
            }}
          >
            {metaItems.map((item, i) => (
              <span
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: size === 'md' ? '16px' : size === 'lg' ? '14px' : '8px',
                }}
              >
                {i > 0 && (
                  <span style={{ color: 'rgba(255, 255, 255, 0.25)' }}>
                    &middot;
                  </span>
                )}
                <span>{item}</span>
              </span>
            ))}
          </div>
        )}

        {seasonNumber != null && episodeNumber != null && (
          <SonarrEpisodeNote
            season={seasonNumber}
            episode={episodeNumber}
            variant="header"
            fontSize={size === 'sm' ? 13 : 16}
          />
        )}
      </div>

      {/* Action pills — Watch + Series/Episodes/Seasons/Sources. */}
      <div style={{
        marginTop: size === 'md' ? '32px' : '16px',
        padding: `0 ${sidePadding}`,
        display: 'flex',
        flexDirection: isCompact ? 'column' : 'row',
        alignItems: isCompact ? 'stretch' : 'center',
        justifyContent: 'center',
        gap: isCompact ? '10px' : size === 'md' ? '16px' : size === 'lg' ? '14px' : '10px',
        flexWrap: 'wrap',
      }}>
        {isCompact ? (
          <>
            {isOwned && onPlay && (
              <button type="button" onClick={onPlay} style={primaryPill}>
                <svg width={pillIconSize} height={pillIconSize} viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="6,3 20,12 6,21" />
                </svg>
                {hasProgress ? `Resume \u00B7 ${minutesLeft}m left` : 'Watch'}
              </button>
            )}

            {(onViewShow || onOpenEpisodes || onOpenSeasons || onOpenSources) && (
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'center',
                  gap: '18px',
                  padding: '10px 0 0',
                }}
              >
                {onViewShow && (
                  <button
                    type="button"
                    onClick={onViewShow}
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      gap: '6px',
                      cursor: 'pointer',
                      background: 'transparent',
                      border: 'none',
                      padding: 0,
                      color: '#fff',
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
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
                        <line x1="8" y1="21" x2="16" y2="21" />
                        <line x1="12" y1="17" x2="12" y2="21" />
                      </svg>
                    </div>
                    <span style={{ fontSize: '12px', color: 'rgba(255, 255, 255, 0.9)', fontWeight: 500 }}>Series</span>
                  </button>
                )}
                {onOpenEpisodes && (
                  <button
                    type="button"
                    onClick={onOpenEpisodes}
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      gap: '6px',
                      cursor: 'pointer',
                      background: 'transparent',
                      border: 'none',
                      padding: 0,
                      color: '#fff',
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
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="2" y="7" width="20" height="15" rx="2" ry="2" />
                        <polyline points="17 2 12 7 7 2" />
                      </svg>
                    </div>
                    <span style={{ fontSize: '12px', color: 'rgba(255, 255, 255, 0.9)', fontWeight: 500 }}>Episodes</span>
                  </button>
                )}
                {onOpenSeasons && (
                  <button
                    type="button"
                    onClick={onOpenSeasons}
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      gap: '6px',
                      cursor: 'pointer',
                      background: 'transparent',
                      border: 'none',
                      padding: 0,
                      color: '#fff',
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
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="2" y="3" width="20" height="4" rx="1" />
                        <rect x="2" y="10" width="20" height="4" rx="1" />
                        <rect x="2" y="17" width="20" height="4" rx="1" />
                      </svg>
                    </div>
                    <span style={{ fontSize: '12px', color: 'rgba(255, 255, 255, 0.9)', fontWeight: 500 }}>Seasons</span>
                  </button>
                )}
                {onOpenSources && (
                  <button
                    type="button"
                    data-testid="episode-sources"
                    onClick={openSources}
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      gap: '6px',
                      cursor: 'pointer',
                      background: 'transparent',
                      border: 'none',
                      padding: 0,
                      color: '#fff',
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
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
                        <polyline points="7 10 12 15 17 10" />
                        <line x1="12" y1="15" x2="12" y2="3" />
                      </svg>
                    </div>
                    <span style={{ fontSize: '12px', color: 'rgba(255, 255, 255, 0.9)', fontWeight: 500 }}>Sources</span>
                  </button>
                )}
              </div>
            )}
          </>
        ) : (
          <>
            {isOwned && onPlay && (
              <button type="button" onClick={onPlay} style={primaryPill}>
                <svg width={pillIconSize} height={pillIconSize} viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="6,3 20,12 6,21" />
                </svg>
                {hasProgress ? `Resume \u00B7 ${minutesLeft}m left` : 'Watch'}
              </button>
            )}

            {onViewShow && (
              <button type="button" onClick={onViewShow} style={secondaryPill}>
                <svg width={pillIconSize} height={pillIconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
                  <line x1="8" y1="21" x2="16" y2="21" />
                  <line x1="12" y1="17" x2="12" y2="21" />
                </svg>
                All Seasons &amp; Episodes
              </button>
            )}
            {onOpenSources && (
              <button type="button" data-testid="episode-sources" onClick={openSources} style={secondaryPill}>
                <svg width={pillIconSize} height={pillIconSize} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
                Sources
              </button>
            )}
          </>
        )}
      </div>


      {/* Plot under action buttons — mirrors series modal (clamp + More/Less) */}
      {overview && (
        <div
          style={{
            padding: `${size === 'md' ? '24px' : '16px'} ${sidePadding} 0`,
            textAlign: size === 'md' ? 'center' : 'left',
          }}
        >
          <p
            onClick={() => setPlotExpanded((v) => !v)}
            style={{
              fontSize: overviewFontSize,
              lineHeight: 1.6,
              color: 'rgba(255, 255, 255, 0.88)',
              margin: size === 'md' ? '0 auto' : '0',
              maxWidth: size === 'md' ? '1100px' : undefined,
              textShadow: size === 'md' ? '0 1px 4px rgba(0,0,0,0.5)' : undefined,
              cursor: 'pointer',
              ...(!plotExpanded
                ? {
                    display: '-webkit-box',
                    WebkitLineClamp: overviewLineClamp,
                    WebkitBoxOrient: 'vertical' as const,
                    overflow: 'hidden',
                  }
                : {}),
            }}
          >
            {overview}
          </p>
          {!plotExpanded && overview.length > 150 && (
            <button
              type="button"
              onClick={() => setPlotExpanded(true)}
              style={{
                background: 'none',
                border: 'none',
                color: '#2196F3',
                fontSize: size === 'sm' ? '14px' : '16px',
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
                fontSize: size === 'sm' ? '14px' : '16px',
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
    </div>
  );
}
