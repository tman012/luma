import { useEffect, useRef } from 'react';
import { cn } from '@/utils/cn';

interface KenBurnsBackdropProps {
  /** The backdrop URL for the currently active movie. */
  currentImage: string | null;
  /** The backdrop URL for the previously active movie (used for crossfade). */
  prevImage: string | null;
  className?: string;
  /** When true, disable ken-burns animation (static backdrop with crossfade only). Tablet perf. */
  disableAnimation?: boolean;
  /** Swap images with no fade (reduced motion). */
  instant?: boolean;
}

/**
 * Two-layer crossfade backdrop with Ken Burns animation.
 *
 * Key design decisions for smooth transitions:
 * - Two persistent <div> layers — never unmounted/remounted.
 * - Background-image swaps (no <img> key changes that trigger remounts).
 * - Opacity transition for crossfade; CSS handles the timing.
 * - Ken Burns animations are per-layer and never restart simultaneously.
 */
export function KenBurnsBackdrop({
  currentImage,
  prevImage,
  className,
  disableAnimation,
  instant,
}: KenBurnsBackdropProps) {
  // Which layer (0 or 1) is currently "on top" (opacity-100).
  const activeLayerRef = useRef(0);
  const prevUrlRef = useRef(currentImage);
  const layerARe = useRef<HTMLDivElement>(null);
  const layerBRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (currentImage === prevUrlRef.current) return;

    const incoming = activeLayerRef.current === 0 ? layerBRef.current : layerARe.current;
    const outgoing = activeLayerRef.current === 0 ? layerARe.current : layerBRef.current;

    if (incoming && currentImage) {
      // Set the new image on the hidden layer before fading it in.
      incoming.style.backgroundImage = `url(${currentImage})`;
    }

    // Small rAF delay so the browser paints the new background-image
    // before we start the opacity transition.
    requestAnimationFrame(() => {
      if (incoming) incoming.style.opacity = '1';
      if (outgoing) outgoing.style.opacity = '0';
    });

    activeLayerRef.current = activeLayerRef.current === 0 ? 1 : 0;
    prevUrlRef.current = currentImage;
  }, [currentImage]);

  // Set the initial image on mount.
  useEffect(() => {
    if (currentImage && layerARe.current) {
      layerARe.current.style.backgroundImage = `url(${currentImage})`;
      layerARe.current.style.opacity = '1';
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!currentImage && !prevImage) return null;

  const shared =
    'absolute inset-0 h-full w-full bg-cover bg-center bg-no-repeat transition-opacity duration-[1200ms] ease-in-out';

  return (
    <div className={cn('absolute inset-0 overflow-hidden', className)}>
      {/* Layer A — starts visible */}
      <div
        ref={layerARe}
        className={cn(shared, !disableAnimation && 'animate-ken-burns')}
        style={{ opacity: 1, ...(instant ? { transition: 'none' } : null) }}
      />
      {/* Layer B — starts hidden */}
      <div
        ref={layerBRef}
        className={cn(shared, !disableAnimation && 'animate-ken-burns-alt')}
        style={{ opacity: 0, ...(instant ? { transition: 'none' } : null) }}
      />
    </div>
  );
}
