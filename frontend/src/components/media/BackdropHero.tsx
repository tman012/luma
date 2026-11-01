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

import { useCallback, useEffect, useMemo, useState } from 'react';
import { APP_BG, reducedMotion } from '@/theme/motion';
import { BACKDROP_HEIGHT } from '@/hooks/useBackdropEffects';
import type { BackdropEffects } from '@/hooks/useBackdropEffects';

export interface BackdropHeroProps {
  /** Keep the art hidden (navy) until the popup reveals it with the title. */
  hold?: boolean;
  /** No fades of its own (the phone sheet fades in as one, composed). */
  instant?: boolean;
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

/** Backdrops painted by a hero this session (memory only, the last few): a later hero on the same src is a handoff. */
const PAINTED = new Set<string>();
export const PAINTED_MAX = 8;
function notePainted(src: string): void {
  if (PAINTED.has(src)) return;
  PAINTED.add(src);
  if (PAINTED.size > PAINTED_MAX) PAINTED.delete(PAINTED.values().next().value as string);
}
export function _resetPaintedBackdrops(): void { PAINTED.clear(); }

export function BackdropHero({
  backdropUrl,
  posterUrl,
  effects,
  phase,
  children,
  containerRef,
  hold = false,
  instant = false,
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
      opacity: instant || phase === 'visible' ? 1 : 0,
      transition: instant ? 'none' : 'opacity 0.4s ease-out',
      willChange: 'opacity',
      ...effects.headerContainerMask,
    }),
    [phase, instant, effects.headerContainerMask],
  );

  // Each image is its own element (keyed by URL) and stays invisible until it
  // has loaded, then fades in over navy: opening another title never shows
  // the previous title's art, and nothing jumps while the new one decodes.
  const src = backdropUrl || posterUrl || null;
  // A backdrop another hero already painted (the seed handing off to the details) shows at once, with no fade.
  const [handoff] = useState<string | null>(() => (src && PAINTED.has(src) ? src : null));
  const [loadedSrc, setLoadedSrc] = useState<string | null>(handoff);
  const loaded = !!src && loadedSrc === src && !hold;
  useEffect(() => { if (loaded && src) notePainted(src); }, [loaded, src]);
  const markLoaded = useCallback((el: HTMLImageElement | null) => {
    if (el && src && el.complete && el.naturalWidth > 0) setLoadedSrc(src);
  }, [src]);
  // The one backdrop fades in softly when it paints (also on the composed phone sheet, `instant`); reduced motion
  // or a handoff of the same image: no fade.
  const fade: React.CSSProperties = { transition: reducedMotion() || (handoff && handoff === src) ? 'none' : 'opacity 0.45s ease-out' };
  return (
    <div ref={containerRef} style={{ ...containerStyle, background: instant && loaded ? 'transparent' : APP_BG }} data-backdrop-loaded={loaded ? 'true' : 'false'}>
      {backdropUrl ? (
        <img
          key={backdropUrl}
          ref={markLoaded}
          src={backdropUrl}
          alt=""
          onLoad={() => setLoadedSrc(backdropUrl)}
          style={{ ...effects.headerImgStyle, ...fade, opacity: loaded ? 1 : 0 }}
        />
      ) : posterUrl ? (
        <img
          key={posterUrl}
          ref={markLoaded}
          src={posterUrl}
          alt=""
          onLoad={() => setLoadedSrc(posterUrl)}
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            ...fade,
            opacity: loaded ? 0.6 : 0,
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
