/** A second backdrop over the one on screen, in the same box: loaded and decoded off-screen, then faded in over
 *  MOTION.dur.backdropSwap. Used when a text-free backdrop replaces art that is already showing (no remount, no move). */
import { useCallback, useEffect, useState } from 'react';
import { MOTION, reducedMotion } from '@/theme/motion';

export function useDecodedUrl(url: string | null | undefined): string | null {
  const [done, setDone] = useState<string | null>(null);
  useEffect(() => {
    if (!url) { setDone(null); return; }
    let live = true;
    const im = new Image();
    im.src = url;
    const p = typeof im.decode === 'function' ? im.decode() : new Promise<void>((res, rej) => { im.onload = () => res(); im.onerror = () => rej(); });
    p.then(() => { if (live) setDone(url); }).catch(() => { /* keep what is on screen */ });
    return () => { live = false; im.onload = null; im.onerror = null; im.src = ''; };
  }, [url]);
  return done === url ? done : null;
}

export function BackdropSwap({ url, style }: { url: string | null | undefined; style: React.CSSProperties }) {
  const ready = useDecodedUrl(url);
  const fade = useCallback((el: HTMLImageElement | null) => {
    if (!el || reducedMotion() || typeof el.animate !== 'function') return;
    el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: MOTION.dur.backdropSwap, easing: MOTION.ease.contentFade, fill: 'backwards' });
  }, []);
  if (!ready) return null;
  return <img key={ready} ref={fade} src={ready} alt="" data-backdrop-swap="true" style={{ ...style, position: 'absolute', top: 0, left: 0 }} />;
}
