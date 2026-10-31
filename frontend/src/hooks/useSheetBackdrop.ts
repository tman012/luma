/** The movie/TV sheet's hero (backdrop + title) as ONE unit (Taylor, 2026-10-31): chosen before the reveal, painted once.
 *
 * - `art` is the final backdrop (the text-free / sharp URL, see finalBackdropUrl), `logo` the clearlogo. Both are
 *   decoded off-screen; the tap's warm-up started both downloads, so they decode during the sheet's enter motion.
 * - The hero latches once the backdrop has decoded and its pick is `artSettled` (no better pick still on its way),
 *   and the logo has decoded or is known to be absent (`logoSettled`); or at BACKDROP_CAP_MS from the tap.
 * - Logo vs. text is decided at the latch: the logo only if it decoded in time, otherwise the text title.
 * - At the cap the best available backdrop latches (the current pick, decoded or not; if there is none yet, the
 *   first one that arrives). Once latched nothing changes for that open (`openKey`): no backdrop swap, no
 *   text-to-logo jump. Nothing (no seed, no low-res, no title) paints before the latch. */
import { useEffect, useState } from 'react';
import { tapElapsed } from '@/services/modalPrefetch';
import type { TitleState } from '@/hooks/useTitleReveal';

export const BACKDROP_CAP_MS = 1200;
/** The hero's one soft fade (backdrop and title/logo together). */
export const HERO_FADE = 'opacity 0.45s ease-out';

function useDecoded(url: string | null): string | null {
  const [done, setDone] = useState<string | null>(null);
  useEffect(() => {
    if (!url) return;
    let live = true;
    const im = new Image();
    im.src = url;
    const p = typeof im.decode === 'function' ? im.decode() : new Promise<void>((res, rej) => { im.onload = () => res(); im.onerror = () => rej(); });
    // A decode error still settles this URL: the cap would show it anyway.
    p.then(() => { if (live) setDone(url); }, () => { if (live) setDone(url); });
    // Drop the pending download too (a closed sheet or a new candidate), not just its result.
    return () => { live = false; im.onload = null; im.onerror = null; im.src = ''; };
  }, [url]);
  return done;
}

export function capLeft(): number {
  const e = tapElapsed();
  return e == null ? BACKDROP_CAP_MS : Math.max(0, BACKDROP_CAP_MS - e);
}

export interface SheetHero { state: TitleState; art: string | null; logo: string | null }
interface St { key: unknown; capped: boolean; title: 'logo' | 'text' | null; logo: string | null; art: string | null; artDone: boolean }
const fresh = (key: unknown): St => ({ key, capped: false, title: null, logo: null, art: null, artDone: false });

export function useSheetHero(art: string | null | undefined, artSettled: boolean, logo: string | null | undefined,
  logoSettled: boolean, openKey: unknown): SheetHero {
  const [st, setSt] = useState<St>(() => fresh(openKey));
  const cur = st.key === openKey ? st : fresh(openKey);
  const artOk = useDecoded(cur.artDone ? null : art ?? null);
  const logoOk = useDecoded(cur.title ? null : logo ?? null);
  useEffect(() => {
    setSt(s => (s.key === openKey ? s : fresh(openKey)));
    const t = window.setTimeout(() => setSt(s => (s.key === openKey && !s.capped ? { ...s, capped: true } : s)), capLeft());
    return () => window.clearTimeout(t);
  }, [openKey]);
  useEffect(() => {
    if (cur.title && cur.artDone) return;
    const logoIn = !!logo && logoOk === logo;
    const ready = artSettled && (art ? artOk === art : true) && (logo ? logoIn : logoSettled);
    if (!cur.title) {
      if (!ready && !cur.capped) return;
      setSt({ ...cur, capped: true, title: logoIn ? 'logo' : 'text', logo: logoIn ? logo! : null, art: art ?? null, artDone: !!art });
    } else if (art) {
      // Latched at the cap with no backdrop known yet: the first one that arrives is the only one.
      setSt({ ...cur, art, artDone: true });
    }
  }, [art, artOk, artSettled, logo, logoOk, logoSettled, cur, openKey]);
  return { state: cur.title ?? 'hold', art: cur.art, logo: cur.logo };
}
/** The same fade for an element that mounts at the latch (the desktop backdrop img). */
export const HERO_ANIM = 'm-crossfade 0.45s ease-out both';
