/** A cast photo that never gates the popup: it starts loading only after the
 * popup has revealed (`ready`), off-screen, and only then swaps in, already
 * decoded. Until then (and on failure) it's the person placeholder, so there's
 * never a pending image in view. */
import { memo, useEffect, useState } from 'react';

interface CastPhotoProps { src: string | null; alt: string; ready: boolean; fallback: string; style: React.CSSProperties }

/** Re-rendered only when what it draws changes: the sheet re-renders for every landing detail, and a shelf of cast
 *  photos re-rendering with it was most of that cost. `style` is one constant per call site. */
export const CastPhoto = memo(CastPhotoView, (a, b) => a.src === b.src && a.alt === b.alt && a.ready === b.ready && a.fallback === b.fallback);

function CastPhotoView({ src, alt, ready, fallback, style }: CastPhotoProps) {
  const [shown, setShown] = useState<string | null>(null);
  useEffect(() => {
    setShown(null);
    if (!ready || !src || typeof Image === 'undefined') return;
    let alive = true;
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => {
      const d = typeof img.decode === 'function' ? img.decode() : Promise.resolve();
      d.then(() => { if (alive) setShown(src); }, () => { if (alive) setShown(src); });
    };
    img.src = src;
    return () => { alive = false; img.onload = null; };
  }, [src, ready]);
  if (shown !== src || !src) {
    return <div role="img" aria-label={alt} data-cast-photo="placeholder"
      style={{ ...style, display: 'block', backgroundImage: `url("${fallback}")`, backgroundSize: 'cover' }} />;
  }
  // Same box as the placeholder (block, explicit size): swapping in moves nothing.
  const w = typeof style.width === 'string' ? parseInt(style.width, 10) : (style.width as number | undefined);
  const h = typeof style.height === 'string' ? parseInt(style.height, 10) : (style.height as number | undefined);
  return <img src={src} alt={alt} width={w} height={h} data-cast-photo="loaded" style={{ ...style, display: 'block' }} />;
}
