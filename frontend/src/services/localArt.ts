/** wl-images (trial): a Watchlist item's sheet images stored on the Luma server (/api/wl-image/...). A Watchlist
 *  open registers them; the sheet then uses them in place of the TMDb backdrop / clearlogo. A local image that fails
 *  to load is dropped and the sheet falls back to its usual path (before its one reveal: the hero only latches a
 *  decoded image). Memory only (nothing stored on the device; the service worker never caches /api/). */
import { useEffect, useRef, useState } from 'react';
import { isDecoded, loadImage } from '@/loading/imageLoader';
import { withBase } from '@/urlHelpers';

export interface LocalArt { backdrop: string | null; logo: string | null; backdropTextless?: boolean }
type Kind = 'movie' | 'tv';
const reg = new Map<string, LocalArt>();
const listeners = new Set<() => void>();
const k = (kind: Kind, id: number) => `${kind}:${id}`;
const MAX = 200;
/** The server's phone copies (app/wl_images.py VARIANTS). At and above the 1024px kiosk/desktop breakpoint the sheet
 *  requests the stored original (no width query, no 1920 ceiling), including high-DPI displays. */
export const PHONE_W = { backdrop: 1280, logo: 300 } as const;
/** Same value as app/wl_images.py URL_REV. Every request URL carries it, so a browser entry for the unversioned
 *  v348–v350 path (public, immutable, a week) is a different URL and is not reused. */
export const WL_IMAGE_REV = '2';
/** Same width cutoff as phone art elsewhere (phoneMinimal / KIOSK_MIN_WIDTH): below it, never the raw original. */
const PHONE_COPY_MAX_W = 1024;

/** The payload's `wl_images` ({backdrop, logo}: URLs or null) as LocalArt, or null. */
export function parseWlImages(v: unknown, kind?: Kind, id?: number | null): LocalArt | null {
  const art = parseWl(v);
  if (art && kind && id) noteLocalArt(kind, id, art);
  return art;
}
function parseWl(v: unknown): LocalArt | null {
  if (!v || typeof v !== 'object') return null;
  const o = v as { backdrop?: unknown; logo?: unknown; backdrop_textless?: unknown };
  const pick = (x: unknown) => (typeof x === 'string' && x.includes('/api/wl-image/') ? x : null);
  const art = { backdrop: pick(o.backdrop), logo: pick(o.logo), backdropTextless: o.backdrop_textless === true };
  return art.backdrop || art.logo ? art : null;
}

function drop(kind: Kind, id: number, which: keyof LocalArt, url: string): void {
  const cur = reg.get(k(kind, id));
  if (!cur || cur[which] !== url) return;
  reg.set(k(kind, id), { ...cur, [which]: null });
  for (const l of Array.from(listeners)) l();
}

/** Below 1024px (phone, and minimal's phone width): the server's phone copies. A desktop gets the stored original. */
function phoneCopy(): boolean {
  if (typeof window === 'undefined') return false;
  return window.innerWidth < PHONE_COPY_MAX_W;
}

/** Absolute request URL: `r` is always the current revision (replacing any older one). `width` sets `w` for a phone
 *  copy; a desktop URL has no `w`. */
function requestUrl(u: string | null, width: number | null): string | null {
  if (!u) return null;
  const abs = u.startsWith('/api/') ? withBase(u) : u;
  const q = abs.indexOf('?');
  const path = q < 0 ? abs : abs.slice(0, q);
  return width != null ? `${path}?r=${WL_IMAGE_REV}&w=${width}` : `${path}?r=${WL_IMAGE_REV}`;
}

/** The URLs a sheet names for stored art: absolute and revisioned. Phones get the server's phone copies
 *  (1280 backdrop, 300 logo), never the raw original. Desktop, including high-DPI, gets the stored original
 *  with no width query. Neither form is the unversioned v348–v350 URL. */
function sizedArt(art: LocalArt): LocalArt {
  const phone = phoneCopy();
  return {
    backdrop: requestUrl(art.backdrop, phone ? PHONE_W.backdrop : null),
    logo: requestUrl(art.logo, phone ? PHONE_W.logo : null),
    backdropTextless: !!art.backdropTextless,
  };
}

/** v350 (Designer NO on ece4395c): a stored backdrop not decoded this long after the open is dropped before the
 *  reveal, so the sheet's usual backdrop (TMDb) starts with time left inside the art deadline
 *  (BACKDROP_ART_DEADLINE_MS 1.6 s): the hero never reveals bare while the normal path would have had art, and a
 *  late local image never swaps in after. */
export const LOCAL_BACKDROP_WAIT_MS = 700;

/** A Watchlist open: its stored images start loading now (high priority) and the sheet uses them. */
export function setLocalArt(kind: Kind, id: number | null | undefined, art: LocalArt | null | undefined): void {
  if (!id || !art) return;
  const a = sizedArt(art);
  noteLocalArt(kind, id, art);
  reg.delete(k(kind, id)); reg.set(k(kind, id), a);
  while (reg.size > MAX) reg.delete(reg.keys().next().value as string);
  for (const which of ['backdrop', 'logo'] as const) {
    const u = a[which];
    if (u) void loadImage(u, 'high').then(() => { if (!isDecoded(u)) drop(kind, id, which, u); });
  }
  const b = a.backdrop;
  if (b) window.setTimeout(() => { if (!isDecoded(b)) drop(kind, id, 'backdrop', b); }, LOCAL_BACKDROP_WAIT_MS);
}

/** Titles whose payload named stored art (Watchlist lists, the Home Watchlist rail), so their warm-ups and the tap
 *  preload the stored backdrop instead of TMDb's (v350: never both). Nothing loads here. */
const known = new Map<string, LocalArt>();
export function noteLocalArt(kind: Kind, id: number | null | undefined, art: LocalArt | null | undefined): void {
  if (!id || !art) return;
  known.delete(k(kind, id)); known.set(k(kind, id), sizedArt(art));
  while (known.size > 2000) known.delete(known.keys().next().value as string);
}

/** The stored backdrop a sheet of this title will show (warm-ups preload it in place of TMDb's), or null: none
 *  stored, or this open dropped it (a load error or LOCAL_BACKDROP_WAIT_MS): then TMDb's is the one to load. */
export function localBackdropFor(kind: Kind, id: number | null | undefined): string | null {
  if (!id) return null;
  const r = reg.get(k(kind, id));
  if (r) return r.backdrop;
  return known.get(k(kind, id))?.backdrop ?? null;
}
export function localLogoFor(kind: Kind, id: number | null | undefined): string | null {
  if (!id) return null;
  const r = reg.get(k(kind, id));
  if (r) return r.logo;
  return known.get(k(kind, id))?.logo ?? null;
}

export function localArt(kind: Kind, id: number | null | undefined): LocalArt | null {
  return id ? reg.get(k(kind, id)) ?? null : null;
}

/** A sheet image that failed to load (an <img> error): the sheet goes back to its usual path. */
export function localArtFailed(kind: Kind, id: number | null | undefined, url: string | null | undefined): void {
  if (!id || !url) return;
  const cur = reg.get(k(kind, id));
  if (cur?.backdrop === url) drop(kind, id, 'backdrop', url);
  if (cur?.logo === url) drop(kind, id, 'logo', url);
}

/** The local art for a sheet, re-read when one of its images fails. v348 (Designer: the same title shows the same
 *  art from every entry point): the entry is the Watchlist open's only; it is cleared when that sheet closes, so a
 *  later search / Home / CW open of the title takes its usual art. */
export function useLocalArt(kind: Kind, id: number | null | undefined): LocalArt | null {
  const [, bump] = useState(0);
  useEffect(() => { const l = () => bump((n) => n + 1); listeners.add(l); return () => { listeners.delete(l); }; }, []);
  const last = useRef<{ kind: Kind; id: number | null | undefined }>({ kind, id });
  last.current = { kind, id };
  useEffect(() => () => { clearLocalArt(last.current.kind, last.current.id); }, []);
  return localArt(kind, id);
}

export function clearLocalArt(kind: Kind, id: number | null | undefined): void {
  if (id) reg.delete(k(kind, id));
}

export function _resetLocalArt(): void { reg.clear(); known.clear(); }

/** A sheet's details with its local art in place. v348 (Designer): an owned title keeps its Plex art (only the logo
 *  is local); a textless local backdrop fills the textless field where the sheet has one (TV), else the backdrop; a
 *  backdrop with text never replaces textless art. */
export function withLocalArt<T extends { backdropUrl?: string | null; backdropTextlessUrl?: string | null; clearLogoUrl?: string | null; logoSettled?: boolean; isOwned?: boolean; plexArtUrl?: string | null }>(
  d: T | null, art: LocalArt | null): T | null {
  if (!d || !art || (!art.backdrop && !art.logo)) return d;
  const plexArt = !!d.isOwned || !!d.plexArtUrl;
  const out: T = { ...d };
  if (art.backdrop && !plexArt) {
    if (art.backdropTextless && 'backdropTextlessUrl' in d) out.backdropTextlessUrl = art.backdrop;
    else if (art.backdropTextless || !d.backdropTextlessUrl) out.backdropUrl = art.backdrop;
  }
  if (art.logo) { out.clearLogoUrl = art.logo; out.logoSettled = true; }
  return out;
}
