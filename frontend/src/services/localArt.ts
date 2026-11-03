/** wl-images (trial): a Watchlist item's sheet images stored on the Luma server (/api/wl-image/...). A Watchlist
 *  open registers them; the sheet then uses them in place of the TMDb backdrop / clearlogo. A local image that fails
 *  to load is dropped and the sheet falls back to its usual path (before its one reveal: the hero only latches a
 *  decoded image). Memory only (nothing stored on the device; the service worker never caches /api/). */
import { useEffect, useState } from 'react';
import { isDecoded, loadImage } from '@/loading/imageLoader';
import { withBase } from '@/urlHelpers';

export interface LocalArt { backdrop: string | null; logo: string | null }
type Kind = 'movie' | 'tv';
const reg = new Map<string, LocalArt>();
const listeners = new Set<() => void>();
const k = (kind: Kind, id: number) => `${kind}:${id}`;
const MAX = 200;

/** The payload's `wl_images` ({backdrop, logo}: URLs or null) as LocalArt, or null. */
export function parseWlImages(v: unknown): LocalArt | null {
  if (!v || typeof v !== 'object') return null;
  const o = v as { backdrop?: unknown; logo?: unknown };
  const pick = (x: unknown) => (typeof x === 'string' && x.includes('/api/wl-image/') ? x : null);
  const art = { backdrop: pick(o.backdrop), logo: pick(o.logo) };
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
  const a = { backdrop: abs(art.backdrop), logo: abs(art.logo) };
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

/** The local art for a sheet, re-read when one of its images fails. */
export function useLocalArt(kind: Kind, id: number | null | undefined): LocalArt | null {
  const [, bump] = useState(0);
  useEffect(() => { const l = () => bump((n) => n + 1); listeners.add(l); return () => { listeners.delete(l); }; }, []);
  return localArt(kind, id);
}

export function _resetLocalArt(): void { reg.clear(); }

/** A sheet's details with its local art in place (backdrop and logo; the logo counts as looked up). */
export function withLocalArt<T extends { backdropUrl?: string | null; backdropTextlessUrl?: string | null; clearLogoUrl?: string | null; logoSettled?: boolean; backdropPending?: boolean }>(
  d: T | null, art: LocalArt | null): T | null {
  if (!d || !art || (!art.backdrop && !art.logo)) return d;
  return {
    ...d,
    ...(art.backdrop ? { backdropUrl: art.backdrop, ...(d.backdropTextlessUrl !== undefined ? { backdropTextlessUrl: art.backdrop } : {}) } : {}),
    ...(art.logo ? { clearLogoUrl: art.logo, logoSettled: true } : {}),
  };
}
