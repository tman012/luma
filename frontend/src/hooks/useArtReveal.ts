import { useEffect, useState } from 'react';
import { artReady, preloadImage, ART_MAX_WAIT_MS } from '@/utils/modalArt';

/** Hide the backdrop and logo until their final URLs have decoded (see utils/modalArt). */
export function useArtReveal(
  backdropUrl: string | null | undefined,
  logoUrl: string | null | undefined,
  final: boolean,
): { ready: boolean; logoFailed: boolean } {
  const [settled, setSettled] = useState<Record<string, boolean>>({});
  const [timedOut, setTimedOut] = useState(false);
  const b = backdropUrl || null;
  const l = logoUrl || null;

  useEffect(() => {
    if (!final) {
      setSettled({});
      setTimedOut(false);
      return;
    }
    let cancelled = false;
    for (const url of [b, l]) {
      if (!url) continue;
      preloadImage(url, Image).then((ok) => {
        if (!cancelled) setSettled((s) => (url in s ? s : { ...s, [url]: ok }));
      });
    }
    const timer = window.setTimeout(() => {
      if (!cancelled) setTimedOut(true);
    }, ART_MAX_WAIT_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [final, b, l]);

  return {
    ready: artReady({ final, backdropUrl: b, logoUrl: l, settled, timedOut }),
    logoFailed: !!l && settled[l] === false,
  };
}