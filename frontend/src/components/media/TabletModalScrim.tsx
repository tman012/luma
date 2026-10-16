/**
 * TabletModalScrim — Legibility scrim for tablet detail modals.
 *
 * Renders a two-layer scrim (directional gradient + vignette) that darkens
 * the backdrop behind foreground content (title, year, ratings, buttons)
 * to ensure readability over bright images.
 *
 * Place INSIDE the [data-modal-backdrop] container so it inherits sticky
 * positioning and is clipped to the backdrop area. Content at z-index 2
 * in the parent stacking context renders above this scrim.
 */

interface TabletModalScrimProps {
  /** Peak opacity: 0 = invisible, 1 = solid black at strongest point. Default: 0.72 */
  strength?: number;
  /** Gradient direction. Default: 'vertical' (top-to-bottom darkening) */
  direction?: 'vertical' | 'horizontal-left' | 'horizontal-right';
  /** 'full' covers entire area; 'contentColumn' adds center-weighted darkening. Default: 'full' */
  coverage?: 'full' | 'contentColumn';
}

export function TabletModalScrim({
  strength = 0.72,
  direction = 'vertical',
  coverage = 'full',
}: TabletModalScrimProps) {
  const dir =
    direction === 'vertical'
      ? 'to bottom'
      : direction === 'horizontal-left'
        ? 'to right'
        : 'to left';

  const s = strength;

  // Primary: multi-stop gradient, strong toward content zone (bottom for vertical)
  const primary = `linear-gradient(${dir}, transparent 10%, rgba(0,0,0,${(s * 0.18).toFixed(2)}) 30%, rgba(0,0,0,${(s * 0.55).toFixed(2)}) 55%, rgba(0,0,0,${s.toFixed(2)}) 85%)`;

  // Vignette: radial overlay to suppress bright hotspots
  const vignette =
    coverage === 'contentColumn'
      ? `radial-gradient(ellipse 80% 70% at 50% 55%, rgba(0,0,0,${(s * 0.3).toFixed(2)}) 0%, rgba(0,0,0,${(s * 0.5).toFixed(2)}) 100%)`
      : `radial-gradient(ellipse 140% 100% at 50% 35%, transparent 40%, rgba(0,0,0,${(s * 0.35).toFixed(2)}) 100%)`;

  return (
    <>
      {/* Primary directional gradient */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          zIndex: 1,
          pointerEvents: 'none',
          background: primary,
        }}
      />
      {/* Secondary vignette — tamps down bright hotspots */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          zIndex: 1,
          pointerEvents: 'none',
          background: vignette,
        }}
      />
    </>
  );
}
