import { useEffect, useRef, useState } from 'react';
// ---------------------------------------------------------------------------
// UltraBlurOverlay — Shared blob-based color bleed overlay for media modals.
//
// Replaces per-layout radial-gradient overlays with organic, Plex-like blurred
// circular blobs that fade in naturally below the backdrop area. Eliminates the
// hard horizontal seam artifact caused by radial-gradient corner approach.
//
// Usage:
//   <UltraBlurOverlay colors={ultraBlurColors} variant="web" />
//   <UltraBlurOverlay colors={ultraBlurColors} variant="mobile" animationDelay={300} />
//   <UltraBlurOverlay colors={ultraBlurColors} variant="tablet" borderRadius="16px" />
// ---------------------------------------------------------------------------

import type { UltraBlurColors } from '@/utils/modalParsers';
import { MOTION } from '@/theme/motion';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface UltraBlurOverlayProps {
  /** Stays invisible while true, then fades in with the backdrop and title
   *  (useTitleReveal). Replaces the delayed keyframe fade when set. */
  hold?: boolean;
  /** No fade of its own on reveal (the phone sheet fades in as one). */
  instant?: boolean;
  colors: UltraBlurColors | null;
  variant: 'web' | 'mobile' | 'tablet';
  /** Delay before opacity fade-in animation (ms). 0 = no animation. */
  animationDelay?: number;
  /** Border-radius for the container. Defaults vary by variant if not provided. */
  borderRadius?: string;
  /** When true, skip GPU-intensive blur filter and use oversized blobs instead.
   *  Creates similar color wash effect without the compositing cost. Tablet perf. */
  lite?: boolean;
}

// ---------------------------------------------------------------------------
// Variant configuration
// ---------------------------------------------------------------------------

interface VariantConfig {
  blurRadius: number;
  brightness: number;
  saturation: number;
  blobOpacity: number;
  /** Blob width/height as percentage of container. */
  blobSize: number;
  mask: string;
  defaultBorderRadius: string;
}

const VARIANT_CONFIG: Record<string, VariantConfig> = {
  web: {
    blurRadius: 100,
    brightness: 1.4,
    saturation: 1.5,
    blobOpacity: 0.7,
    blobSize: 70,
    mask: 'linear-gradient(to bottom, transparent 15%, rgba(0,0,0,0.3) 35%, black 60%)',
    defaultBorderRadius: '20px',
  },
  mobile: {
    blurRadius: 100,
    brightness: 1.25,
    saturation: 1.4,
    blobOpacity: 0.6,
    blobSize: 65,
    mask: 'none',
    defaultBorderRadius: '0px',
  },
  tablet: {
    blurRadius: 100,
    brightness: 1.25,
    saturation: 1.4,
    blobOpacity: 0.6,
    blobSize: 65,
    mask: 'linear-gradient(to bottom, transparent 62%, rgba(0,0,0,0.4) 75%, black 88%)',
    defaultBorderRadius: '0px',
  },
};

// ---------------------------------------------------------------------------
// Blob position mapping from legacy 4-corner data
// ---------------------------------------------------------------------------

interface BlobDef {
  color: string;
  x: number;
  y: number;
}

function buildBlobsFromCorners(colors: UltraBlurColors): BlobDef[] {
  // If the new palette format is available, use it directly
  if (colors.format === 'palette' && colors.blobs) {
    return colors.blobs.map((b) => ({
      color: b.color,
      x: b.x,
      y: b.y,
    }));
  }

  // Map non-null corners to positioned blobs
  const blobs: BlobDef[] = [];
  const cornerColors: string[] = [];

  if (colors.topLeft) {
    blobs.push({ color: colors.topLeft, x: 0.12, y: 0.2 });
    cornerColors.push(colors.topLeft);
  }
  if (colors.topRight) {
    blobs.push({ color: colors.topRight, x: 0.88, y: 0.2 });
    cornerColors.push(colors.topRight);
  }
  if (colors.bottomRight) {
    blobs.push({ color: colors.bottomRight, x: 0.85, y: 0.85 });
    cornerColors.push(colors.bottomRight);
  }
  if (colors.bottomLeft) {
    blobs.push({ color: colors.bottomLeft, x: 0.15, y: 0.85 });
    cornerColors.push(colors.bottomLeft);
  }

  // Generate a 5th "center" blob by averaging the non-null corner colors
  if (cornerColors.length > 0) {
    const avgColor = averageColors(cornerColors);
    blobs.push({ color: avgColor, x: 0.5, y: 0.55 });
  }

  return blobs;
}

// ---------------------------------------------------------------------------
// Color averaging helper (parses rgb/rgba/hex strings)
// ---------------------------------------------------------------------------

function parseColor(color: string): [number, number, number] {
  // Handle rgb(r, g, b) or rgba(r, g, b, a)
  const rgbMatch = color.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
  if (rgbMatch) {
    return [
      parseInt(rgbMatch[1]!, 10),
      parseInt(rgbMatch[2]!, 10),
      parseInt(rgbMatch[3]!, 10),
    ];
  }

  // Handle hex (#rrggbb or #rgb)
  let hex = color.replace('#', '');
  if (hex.length === 3) {
    hex = hex[0]! + hex[0]! + hex[1]! + hex[1]! + hex[2]! + hex[2]!;
  }
  if (hex.length === 6) {
    return [
      parseInt(hex.substring(0, 2), 16),
      parseInt(hex.substring(2, 4), 16),
      parseInt(hex.substring(4, 6), 16),
    ];
  }

  // Fallback: dark gray
  return [30, 30, 30];
}

function averageColors(colors: string[]): string {
  let r = 0;
  let g = 0;
  let b = 0;
  for (const c of colors) {
    const [cr, cg, cb] = parseColor(c);
    r += cr;
    g += cg;
    b += cb;
  }
  const n = colors.length;
  return `rgb(${Math.round(r / n)}, ${Math.round(g / n)}, ${Math.round(b / n)})`;
}

/** Perceived brightness 0..1 (BT.601 luma weights). */
function perceivedLuminance(color: string): number {
  const [r, g, b] = parseColor(color);
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
}


// ---------------------------------------------------------------------------
// Phone sheet blobs: radial gradients, not blurred elements
// ---------------------------------------------------------------------------
// A blob blurred with filter: blur(100px) is re-rastered by the first commit of the sheet, and on the phone sheet that
// raster (five filtered blobs per overlay, masked and contained) held the main thread for 250-350 ms right after the
// slide's first frame, so the slide's own frames were never painted. The same soft color field is one element with
// radial gradients that fall off like the blurred disk: no filter, nothing to re-raster, drawn with the sheet's own layer.

/** CSS saturate() then brightness() applied to the blob color here, since the gradient has no filter. */
function adjustColor(color: string, saturation: number, brightness: number): [number, number, number] {
  const [r, g, b] = parseColor(color);
  const s = saturation;
  const m = [
    0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s,
    0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s,
    0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s,
  ];
  const out = [0, 1, 2].map((i) => {
    const v = (m[i * 3]! * r + m[i * 3 + 1]! * g + m[i * 3 + 2]! * b) * brightness;
    return Math.max(0, Math.min(255, Math.round(v)));
  });
  return out as [number, number, number];
}

/** Alpha along the gradient's radius for a blob of the phone's size blurred by 100px: the blurred disk's own profile
 *  (peak below 1, a tail about 260px past the disk), sampled at tenths of the gradient radius. */
const FALLOFF = [0.752, 0.727, 0.655, 0.539, 0.403, 0.269, 0.154, 0.077, 0.032, 0.011, 0] as const;
const BLUR_REACH_PX = 260;

export function blobGradient(blob: BlobDef, size: number, opacity: number, saturation: number, brightness: number): string {
  const [r, g, b] = adjustColor(blob.color, saturation, brightness);
  const reach = `calc(${size / 2}% + ${BLUR_REACH_PX}px)`;
  const stops = FALLOFF.map((a, i) => `rgba(${r},${g},${b},${+(a * opacity).toFixed(3)}) ${Math.round((i / (FALLOFF.length - 1)) * 100)}%`).join(',');
  return `radial-gradient(ellipse ${reach} ${reach} at ${blob.x * 100}% ${blob.y * 100}%,${stops})`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

function BlurLayer({
  colors,
  variant,
  animationDelay = 0,
  borderRadius,
  lite = false,
  hold,
  instant,
}: UltraBlurOverlayProps) {
  if (!colors) return null;

  const baseConfig = VARIANT_CONFIG[variant] ?? VARIANT_CONFIG.web!;

  const { blurRadius, brightness, saturation, blobOpacity, blobSize, mask } = baseConfig;

  const resolvedRadius = borderRadius ?? baseConfig.defaultBorderRadius;
  const blobs = buildBlobsFromCorners(colors);

  if (blobs.length === 0) return null;

  // Auto-dim for bright backdrops: compute average perceived luminance of all
  // blob colors.  When the colors are very bright (luminance > 0.65) we gently
  // reduce opacity and brightness boost so overlaid text stays readable.
  // Threshold raised from 0.55 and curves softened to preserve color vibrancy.
  const avgLum =
    blobs.reduce((sum, b) => sum + perceivedLuminance(b.color), 0) / blobs.length;

  const dimFactor = avgLum > 0.65
    ? Math.max(0.6, 1 - (avgLum - 0.65) * 1.0)
    : 1;
  const effectiveOpacity = blobOpacity * dimFactor;

  // Scale down brightness boost only for very bright colors
  const effectiveBrightness = avgLum > 0.65
    ? 1.0 + (brightness - 1.0) * Math.max(0.3, 1 - (avgLum - 0.65) * 1.5)
    : brightness;

  // Lite mode: skip GPU-intensive blur, use oversized blobs for a similar color wash.
  // Blobs at 130% with lower opacity overlap to create soft color transitions.
  const liteBlobSize = 130;
  const liteOpacityFactor = 0.35;

  const effectiveBlobSize = lite ? liteBlobSize : blobSize;
  const effectiveBlobOpacity = lite ? effectiveOpacity * liteOpacityFactor : effectiveOpacity;

  // Build filter string — blur only when NOT in lite mode
  const filterParts: string[] = [];
  if (!lite) filterParts.push(`blur(${blurRadius}px)`);
  if (saturation !== 1.0) filterParts.push(`saturate(${saturation})`);
  if (effectiveBrightness !== 1.0) filterParts.push(`brightness(${effectiveBrightness})`);
  const filterStr = filterParts.length > 0 ? filterParts.join(' ') : 'none';

  const flat = variant === 'mobile' && !lite;
  if (flat) {
    // Last blob on top, as the blurred blobs stacked.
    const layers = blobs.map((blob) => blobGradient(blob, effectiveBlobSize, effectiveBlobOpacity, saturation, effectiveBrightness)).reverse().join(',');
    return (
      <div data-ultrablur-overlay="" data-blur-flat=""
        style={{
          position: 'absolute', inset: 0, zIndex: 0, overflow: 'hidden', pointerEvents: 'none', contain: 'strict',
          borderRadius: resolvedRadius, backfaceVisibility: 'hidden', backgroundImage: layers,
          ...(hold !== undefined ? { opacity: hold ? 0 : 1, transition: instant ? 'none' : 'opacity var(--m-dur-reveal) var(--m-ease-reveal)' } : {}),
        }}
      />
    );
  }

  // Build animation style for delayed fade-in
  const hasAnimation = animationDelay > 0 && hold === undefined;
  const animationName = 'ultraBlurFadeIn';

  return (
    <>
      <div
        data-ultrablur-overlay
        style={{
          position: 'absolute',
          inset: 0,
          zIndex: 0,
          overflow: 'hidden',
          pointerEvents: 'none',
          contain: 'strict',
          WebkitMaskImage: mask,
          maskImage: mask,
          borderRadius: resolvedRadius,
          backfaceVisibility: 'hidden',
          ...(hold !== undefined ? { opacity: hold ? 0 : 1, transition: instant ? 'none' : 'opacity var(--m-dur-reveal) var(--m-ease-reveal)' } : {}),
          // Animation: start invisible if delayed, animate to visible
          ...(hasAnimation
            ? {
                // Fades in with the backdrop (the palette is settled before the
                // popup renders it), never after it.
                animation: `${animationName} var(--m-dur-reveal) var(--m-ease-reveal) both`,
              }
            : {}),
        }}
      >
        {blobs.map((blob, i) => (
          <div
            key={i}
            style={{
              position: 'absolute',
              width: `${effectiveBlobSize}%`,
              height: `${effectiveBlobSize}%`,
              borderRadius: '50%',
              backgroundColor: blob.color,
              filter: filterStr,
              opacity: effectiveBlobOpacity,
              transform: 'translate(-50%, -50%)',
              left: `${blob.x * 100}%`,
              top: `${blob.y * 100}%`,
              pointerEvents: 'none',
              // No will-change: the blobs never move; as composited layers their 100px blur was redrawn on every frame of a sheet slide (viz-bound). Rasterized once instead.
            }}
          />
        ))}
        {/* Subtle noise texture to prevent color banding — skip in lite mode (no blur = no banding) */}
        {!lite && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              opacity: 0.03,
              pointerEvents: 'none',
              backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")`,
              backgroundRepeat: 'repeat',
              backgroundSize: '128px 128px',
            }}
          />
        )}
      </div>
      {/* Inject keyframe for fade-in animation (only when needed) */}
      {hasAnimation && (
        <style>{`
          @keyframes ${animationName} {
            from { opacity: 0; }
            to { opacity: 1; }
          }
        `}</style>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Public component: when the palette changes after it's shown (the poster-
// derived seed giving way to the fetched palette), the new layer fades in over
// the old one, which is then dropped: an invisible crossfade, never a pop.
// ---------------------------------------------------------------------------

export const CROSSFADE_MS = MOTION.dur.palette;

export function UltraBlurOverlay(props: UltraBlurOverlayProps) {
  const { colors } = props;
  const [prev, setPrev] = useState<UltraBlurColors | null>(null);
  const shown = useRef<UltraBlurColors | null>(colors);
  useEffect(() => {
    const old = shown.current;
    shown.current = colors;
    if (!old || !colors || old === colors) { setPrev(null); return; }
    setPrev(old);
    const t = window.setTimeout(() => setPrev(null), CROSSFADE_MS + 50);
    return () => window.clearTimeout(t);
  }, [colors]);
  if (!prev) return <BlurLayer {...props} />;
  return (
    <>
      <BlurLayer {...props} colors={prev} />
      <div data-blur-crossfade style={{ position: 'absolute', inset: 0, animation: `ultraBlurFadeIn ${CROSSFADE_MS}ms var(--m-ease-standard) both` }}>
        <BlurLayer {...props} />
      </div>
      <style>{'@keyframes ultraBlurFadeIn { from { opacity: 0; } to { opacity: 1; } }'}</style>
    </>
  );
}
