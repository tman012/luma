// ---------------------------------------------------------------------------
// BackdropHero — Shared Plex-like cover-cropped backdrop for mobile modals.
//
// Renders the hero backdrop image with:
//  - object-fit: cover (no letterboxing)
//  - upward-biased center crop (default 50% 35%)
//  - post-cover zoom (default scale 1.18, Plex-like tight framing)
//  - gradient mask when UltraBlur palette is active
//  - fallback bottom gradient when no palette
//  - vignette overlay for cinematic rolloff
//
// Used by both MovieDetailModal/CompactLayout and TvDetailModal/CompactLayout.
// ---------------------------------------------------------------------------

import { useMemo } from 'react';
import { BACKDROP_HEIGHT } from '@/hooks/useBackdropEffects';
import type { BackdropEffects } from '@/hooks/useBackdropEffects';

export interface BackdropHeroProps {
  /** Primary backdrop image URL. */
  backdropUrl?: string | null;
  /** Fallback poster URL when no backdrop available. */
  posterUrl?: string | null;
  /** Computed effects from useBackdropEffects hook. */
  effects: BackdropEffects;
  /** Current animation phase. */
  phase: 'entering' | 'visible' | 'exiting' | 'gone';
  /** Optional overlay layers rendered inside the backdrop container. */
  children?: React.ReactNode;
  /** Ref to the container div, for parallax scroll effects. */
  containerRef?: React.RefObject<HTMLDivElement | null>;
}

export function BackdropHero({
  backdropUrl,
  posterUrl,
  effects,
  phase,
  children,
  containerRef,
}: BackdropHeroProps) {
  const containerStyle = useMemo<React.CSSProperties>(
    () => ({
      position: 'sticky',
      top: 0,
      width: '100%',
      height: typeof BACKDROP_HEIGHT === 'number' ? `${BACKDROP_HEIGHT}px` : BACKDROP_HEIGHT,
      overflow: 'hidden',
      zIndex: 0,
      borderTopLeftRadius: '14px',
      borderTopRightRadius: '14px',
      opacity: phase === 'visible' ? 1 : 0,
      transition: 'opacity 0.4s ease-out',
      willChange: 'opacity',
      ...effects.headerContainerMask,
    }),
    [phase, effects.headerContainerMask],
  );

  return (
    <div ref={containerRef} style={containerStyle}>
      {backdropUrl ? (
        <img
          src={backdropUrl}
          alt=""
          style={effects.headerImgStyle}
        />
      ) : posterUrl ? (
        <img
          src={posterUrl}
          alt=""
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            opacity: 0.6,
          }}
        />
      ) : null}

      {/* Subtle bottom vignette — just enough for text readability.
          The container-level mask handles the smooth fade to transparent. */}
      <div
        style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          right: 0,
          height: '35%',
          background: 'linear-gradient(to bottom, transparent 0%, rgba(0,0,0,0.25) 100%)',
          pointerEvents: 'none',
        }}
      />

      {children}
    </div>
  );
}
