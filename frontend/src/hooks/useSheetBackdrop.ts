/** The movie/TV sheet's single backdrop (Taylor, 2026-10-31): one image, chosen before the reveal, painted once.
 *
 * - `candidate` is the final image (the text-free / sharp URL, see finalBackdropUrl). It is decoded off-screen; the
 *   sheet's warm-up started the same download at the tap, so it decodes during the sheet's enter motion.
 * - The backdrop latches once the candidate has decoded and the details are `settled` (no better pick is still on
 *   its way), or at BACKDROP_CAP_MS from the tap, whichever comes first. At the cap the best available image latches
 *   (the current candidate, decoded or not; if there is none yet, the first one that arrives).
 * - Once latched it never changes for that open (`openKey`): later candidates, a changing first art or a refetch are
 *   ignored. Nothing (no seed, no low-res) paints before the latch. */
import { useEffect, useState } from 'react';
import { tapElapsed } from '@/services/modalPrefetch';

export const BACKDROP_CAP_MS = 1200;

function useDecoded(url: string | null): string | null {
  const [done, setDone] = useState<string | null>(null);
  useEffect(() => {
    if (!url) return;
    let live = true;
    const im = new Image();
    im.src = url;
    const p = typeof im.decode === 'function' ? im.decode() : new Promise<void>((res, rej) => { im.onload = () => res(); im.onerror = () => rej(); });
    // A decode error still counts as settled for this URL: the cap would show it anyway.
    p.then(() => { if (live) setDone(url); }, () => { if (live) setDone(url); });
    return () => { live = false; im.onload = null; im.onerror = null; };
  }, [url]);
  return done;
}

export function capLeft(): number {
  const e = tapElapsed();
  return e == null ? BACKDROP_CAP_MS : Math.max(0, BACKDROP_CAP_MS - e);
}

export function useSheetBackdrop(candidate: string | null | undefined, settled: boolean, openKey: unknown): string | null {
  const [st, setSt] = useState<{ key: unknown; url: string | null; capped: boolean }>({ key: openKey, url: null, capped: false });
  const cur = st.key === openKey ? st : { key: openKey, url: null, capped: false };
  const decoded = useDecoded(cur.url ? null : candidate ?? null);
  useEffect(() => {
    setSt(s => (s.key === openKey ? s : { key: openKey, url: null, capped: false }));
    const t = window.setTimeout(() => setSt(s => (s.key === openKey && !s.capped ? { ...s, capped: true } : s)), capLeft());
    return () => window.clearTimeout(t);
  }, [openKey]);
  useEffect(() => {
    if (cur.url || !candidate) return;
    if ((settled && decoded === candidate) || cur.capped) setSt({ key: openKey, url: candidate, capped: true });
  }, [candidate, decoded, settled, cur.capped, cur.url, openKey]);
  return cur.url;
}
