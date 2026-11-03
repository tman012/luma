/** wl-images (trial): a Watchlist item's sheet images stored on the Luma server (/api/wl-image/...). A Watchlist
 *  open registers them; the sheet then uses them in place of the TMDb backdrop / clearlogo. A local image that fails
 *  to load is dropped and the sheet falls back to its usual path (before its one reveal: the hero only latches a
 *  decoded image). Memory only (nothing stored on the device; the service worker never caches /api/). */
import { useEffect, useRef, useState } from 'react';
import { phoneMinimal } from '@/utils/image';
import { isDecoded, loadImage } from '@/loading/imageLoader';
import { withBase } from '@/urlHelpers';

export interface LocalArt { backdrop: string | null; logo: string | null; backdropTextless?: boolean }
type Kind = 'movie' | 'tv';
const reg = new Map<string, LocalArt>();
const listeners = new Set<() => void>();
const k = (kind: Kind, id: number) => `${kind}:${id}`;
const MAX = 200;

/** The payload's `wl_images` ({backdrop, logo}: URLs or null) as LocalArt, or null. */
export function parseWlImages(v: unknown): LocalArt | null {
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

/** A Watchlist open: its stored images start loading now (high priority) and the sheet uses them. */
export function setLocalArt(kind: Kind, id: number | null | undefined, art: LocalArt | null | undefined): void {
  if (!id || !art) return;
  const abs = (u: string | null) => (u ? (u.startsWith('/api/') ? withBase(u) : u) : null);
  // v348: phones keep their TMDb sizes (780/1280 backdrops, w300 logos): the local art (1920 / original) is desktop's.
  if (phoneMinimal()) return;
  const a = { backdrop: abs(art.backdrop), logo: abs(art.logo), backdropTextless: !!art.backdropTextless };
  reg.delete(k(kind, id)); reg.set(k(kind, id), a);
  while (reg.size > MAX) reg.delete(reg.keys().next().value as string);
  for (const which of ['backdrop', 'logo'] as const) {
    const u = a[which];
    if (u) void loadImage(u, 'high').then(() => { if (!isDecoded(u)) drop(kind, id, which, u); });
  }
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

export function _resetLocalArt(): void { reg.clear(); }

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
