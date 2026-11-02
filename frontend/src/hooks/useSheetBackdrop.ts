/** The movie/TV sheet's hero (backdrop + title) as ONE unit (Taylor, 2026-10-31): chosen before the reveal, painted once.
 *
 * - `art` is the final backdrop (the text-free / sharp URL, see finalBackdropUrl), `logo` the clearlogo. Both are
 *   decoded off-screen; the tap's warm-up started both downloads, so they decode during the sheet's enter motion.
 * - The hero latches once the backdrop has decoded and its pick is `artSettled` (no better pick still on its way),
 *   and the logo has decoded or is known to be absent (`logoSettled`); or at BACKDROP_CAP_MS from the tap.
 * - Logo vs. text is decided at the latch: the logo only if it decoded in time, otherwise the text title.
 * - At the deadline (SURFACE_DEADLINE_MS from the tap, the sheet's one revealGate) the hero latches as soon as the
 *   pick is settled and a known backdrop has decoded (or failed, or BACKDROP_ART_DEADLINE_MS passed): a backdrop not decoded by then is dropped for that open (the
 *   no-art state), a logo not decoded is the text title. Once latched nothing changes for that open (`openKey`):
 *   no backdrop arrives later, no swap, no text-to-logo jump. Nothing paints before the latch. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { tapElapsed } from '@/services/modalPrefetch';
import type { TitleState } from '@/hooks/useTitleReveal';
import { SURFACE_DEADLINE_MS } from '@/loading/revealGate';

export const BACKDROP_CAP_MS = SURFACE_DEADLINE_MS;
/** A cold open's backdrop-only deadline (PM, v341): past BACKDROP_CAP_MS the whole reveal still waits up to this
 *  long from the tap for a known backdrop to decode, so backdrop and title fade in together. */
export const BACKDROP_ART_DEADLINE_MS = 1600;
/** Details that land past the 1.0s deadline (a slow TMDb): their backdrop still gets this long to decode. */
export const BACKDROP_LATE_DECODE_MS = BACKDROP_ART_DEADLINE_MS - BACKDROP_CAP_MS;
/** The hero's one soft fade (backdrop and title/logo together). */
export const HERO_FADE = 'opacity 0.45s ease-out';

/** The URL once its decode has finished, and whether it succeeded. */
function useDecoded(url: string | null): { url: string | null; ok: boolean } {
  const [done, setDone] = useState<{ url: string | null; ok: boolean }>({ url: null, ok: false });
  useEffect(() => {
    if (!url) return;
    let live = true;
    const im = new Image();
    (im as HTMLImageElement & { fetchPriority?: string }).fetchPriority = 'high';
    im.src = url;
    const p = typeof im.decode === 'function' ? im.decode() : new Promise<void>((res, rej) => { im.onload = () => res(); im.onerror = () => rej(); });
    // A decode error settles this URL too (ok: false): a backdrop would show at the cap anyway; a logo falls back to text.
    p.then(() => { if (live) setDone({ url, ok: true }); }, () => { if (live) setDone({ url, ok: false }); });
    // Drop the pending download too (a closed sheet or a new candidate), not just its result.
    return () => { live = false; im.onload = null; im.onerror = null; im.src = ''; };
  }, [url]);
  return done;
}

export function capLeft(cap: number = BACKDROP_CAP_MS): number {
  const e = tapElapsed();
  return e == null ? cap : Math.max(0, cap - e);
}

export interface SheetHero {
  state: TitleState; art: string | null; logo: string | null;
  /** The one deadline has passed for this open. */
  deadline: boolean;
  /** The cold backdrop-only deadline (BACKDROP_ART_DEADLINE_MS) has passed. */
  artDeadline: boolean;
  /** The chosen logo failed to paint (img onError): show the text title instead. */
  logoFailed: () => void;
}
interface St { key: unknown; capped: boolean; artCapped?: boolean; title: 'logo' | 'text' | null; logo: string | null; art: string | null; artDone: boolean }
const fresh = (key: unknown): St => ({ key, capped: false, title: null, logo: null, art: null, artDone: false });

export function useSheetHero(art: string | null | undefined, artSettled: boolean, logo: string | null | undefined,
  logoSettled: boolean, openKey: unknown, present: boolean = artSettled): SheetHero {
  const [st, setSt] = useState<St>(() => fresh(openKey));
  const cur = st.key === openKey ? st : fresh(openKey);
  const artOk = useDecoded(cur.artDone ? null : art ?? null);
  const logoOk = useDecoded(cur.title ? null : logo ?? null);
  // The last backdrop that decoded for this open (TV: the Plex art before a TMDb pick replaced the candidate), so the
  // art deadline latches it rather than no art when the newer pick is still decoding.
  const good = useRef<{ key: unknown; url: string | null }>({ key: openKey, url: null });
  if (good.current.key !== openKey) good.current = { key: openKey, url: null };
  if (artOk.ok && artOk.url && artOk.url === art) good.current.url = artOk.url;
  useEffect(() => {
    setSt(s => (s.key === openKey ? s : fresh(openKey)));
    const t = window.setTimeout(() => setSt(s => (s.key === openKey && !s.capped ? { ...s, capped: true } : s)), capLeft());
    return () => window.clearTimeout(t);
  }, [openKey]);
  // Details past the 1.0s deadline: the backdrop known then still gets BACKDROP_LATE_DECODE_MS to decode. `present`:
  // the details are in even if a better pick is still on its way (TV: TMDb pending over Plex art); the same art
  // deadline caps that wait too.
  const lateKnown = cur.capped && present && !cur.artCapped;
  useEffect(() => {
    if (!lateKnown) return;
    const t = window.setTimeout(() => setSt(s => (s.key === openKey && !s.artCapped ? { ...s, artCapped: true } : s)),
      tapElapsed() == null ? BACKDROP_LATE_DECODE_MS : Math.max(BACKDROP_LATE_DECODE_MS, capLeft(BACKDROP_ART_DEADLINE_MS)));
    return () => window.clearTimeout(t);
  }, [lateKnown, openKey]);
  useEffect(() => {
    if (cur.title) return;
    // Only a successful decode counts; a 404 or corrupt image is settled as absent (text title / no art).
    const logoIn = !!logo && logoOk.url === logo && logoOk.ok;
    const logoBad = !!logo && logoOk.url === logo && !logoOk.ok;
    const artIn = !!art && artOk.url === art && artOk.ok;
    const ready = artSettled && (art ? artOk.url === art : true) && (logo ? logoIn || logoBad : logoSettled);
    // Past the deadline: latch once the pick is settled (the details are in), with what has decoded; a known backdrop
    // still decoding holds the latch until it decodes, fails, or BACKDROP_ART_DEADLINE_MS.
    const artWait = !!art && artOk.url !== art && !cur.artCapped;
    // At the art deadline a pick still pending (TMDb slow or failing) latches with what has decoded (the Plex art), or
    // no art: never a blank sheet past the cap, and nothing swaps in after.
    // Past the 1.0s deadline with the details in and their backdrop already decoded (TV: the Plex art while TMDb
    // is still out): latch now; a better pick still on its way doesn't hold a sheet that has its art.
    const artReady = cur.capped && present && artIn;
    if (!ready && !(cur.capped && artSettled && !artWait) && !(cur.artCapped && present) && !artReady) return;
    const fallback = cur.artCapped && present && !artIn ? good.current.url : null;
    setSt({ ...cur, capped: true, title: logoIn ? 'logo' : 'text', logo: logoIn ? logo! : null, art: artIn ? art! : fallback, artDone: true });
  }, [art, artOk, artSettled, present, logo, logoOk, logoSettled, cur, openKey]);
  const logoFailed = useCallback(() => setSt(s => (s.key === openKey && s.title === 'logo' ? { ...s, title: 'text', logo: null } : s)), [openKey]);
  return { state: cur.title ?? 'hold', art: cur.art, logo: cur.logo, deadline: cur.capped, artDeadline: !!cur.artCapped, logoFailed };
}
/** The same fade for an element that mounts at the latch (the desktop backdrop img). */
export const HERO_ANIM = 'm-crossfade 0.45s ease-out both';

/** Desktop: one trigger for the content and the backdrop layer. The content runs the backdrop's own HERO_ANIM, in
 *  the same commit, on its own compositor layer (will-change), so Chrome gives both pending animations one start
 *  time: a warm open's large art that takes frames to raster no longer leaves the content fading in alone. */
export const CONTENT_WITH_HERO = { transition: 'none', animation: HERO_ANIM, willChange: 'opacity' } as const;
/** Desktop: the reveal waits for its backdrop img element at most until BACKDROP_ART_DEADLINE_MS after the tap. */
export function useArtWaitCap(openKey: unknown): boolean {
  const [k, setK] = useState<unknown>(undefined);
  useEffect(() => {
    const t = window.setTimeout(() => setK(openKey), capLeft(BACKDROP_ART_DEADLINE_MS));
    return () => window.clearTimeout(t);
  }, [openKey]);
  return k === openKey;
}
