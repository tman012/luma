/** A cast photo that never gates the popup: it starts loading only after the
 * popup has revealed (`ready`), off-screen, and only then swaps in, already
 * decoded. Until then (and on failure) it's the person placeholder, so there's
 * never a pending image in view. */
import { useEffect, useState } from 'react';

export function CastPhoto({ src, alt, ready, fallback, style }: {
  src: string | null; alt: string; ready: boolean; fallback: string; style: React.CSSProperties;
}) {
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
      style={{ ...style, backgroundImage: `url("${fallback}")`, backgroundSize: 'cover' }} />;
  }
  return <img src={src} alt={alt} data-cast-photo="loaded" style={style} />;
}
