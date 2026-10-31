/** Clearlogo for a seeded sheet (opened from a tapped card, text title on frame 0). The logo waits until it and the
 *  sharp backdrop have both decoded, then fades in over MOTION.dur.backdropSwap in the title's fixed box while the
 *  text title fades out in place. Nothing moves; the sheet's own motion is untouched. */
import { useCallback, useEffect, useState } from 'react';
import { MOTION, reducedMotion } from '@/theme/motion';

function useDecoded(url: string | null | undefined): boolean {
  const [doneUrl, setDoneUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!url) return;
    let alive = true;
    const img = new Image();
    const done = () => { if (alive) setDoneUrl(url); };
    img.onload = () => { (typeof img.decode === 'function' ? img.decode() : Promise.resolve()).then(done, done); };
    img.src = url;
    return () => { alive = false; img.onload = null; };
  }, [url]);
  return !!url && doneUrl === url;
}

export function useLogoSwap(seeded: boolean, logoUrl: string | null | undefined, artUrl: string | null | undefined): boolean {
  const logoOk = useDecoded(seeded ? logoUrl : null);
  const artOk = useDecoded(seeded ? artUrl : null);
  return seeded && !!logoUrl && logoOk && (!artUrl || artOk);
}

/** Style for the text title in a seeded sheet: fades out as the logo fades in. */
export function seededTitleStyle(swap: boolean): React.CSSProperties {
  return { opacity: swap ? 0 : 1, transition: reducedMotion() ? 'none' : `opacity ${MOTION.dur.backdropSwap}ms ${MOTION.ease.contentFade}` };
}

export function SeededLogo({ url, alt }: { url: string; alt?: string }) {
  const fade = useCallback((el: HTMLImageElement | null) => {
    if (!el || reducedMotion() || typeof el.animate !== 'function') return;
    el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: MOTION.dur.backdropSwap, easing: MOTION.ease.contentFade, fill: 'backwards' });
  }, []);
  return (
    <img ref={fade} src={url} alt={alt} data-testid="seeded-logo"
      style={{ position: 'absolute', bottom: 0, left: '50%', transform: 'translateX(-50%)', maxWidth: 'calc(100% - 32px)', maxHeight: '200px',
        objectFit: 'contain', filter: 'drop-shadow(0 2px 8px rgba(0,0,0,0.5))', pointerEvents: 'none' }} />
  );
}
