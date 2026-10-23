import { apiFetch } from '@/apiClient';
import { tmdbImg } from '@/utils/image';
import { mapHeroMovie } from '@/services/api';
import type { HeroMovie, HeroContentResponse } from '@/services/types';

export interface Card {
  key: string; type: string; title: string; subtitle: string;
  show_key?: string; season?: number; episode?: number;
  poster: string | null; art?: string | null; progress?: number;
  /** Trakt rail cards: open the TMDB detail popup (no Plex rating key). */
  tmdb_id?: number;
  /** Watchlist rail cards: prod's digital-release label ('Jan 9'), shown above the title. */
  label?: string | null;
}
export interface Rail {
  id: string; title: string; type: string; items: Card[];
  /** v254 What you're missing: the signed-in owner may long-press a card to dismiss it. */
  dismiss?: boolean;
}
export interface HomeData { continue_watching: Card[]; rails: Rail[]; plex: boolean }
export interface Library { id: string; title: string; type: string; enabled: boolean; source?: 'plex' | 'trakt' | 'watchlist' | 'wym' }

/** Image URLs come from the server complete (URL base included): use as is. */
export const img = (p: string | null | undefined) => tmdbImg(p);

export async function fetchHome(): Promise<HomeData> {
  const res = await apiFetch('/api/home');
  if (!res.ok) throw new Error(String(res.status));
  const d = await res.json();
  return (d && typeof d === 'object' ? d : {}) as HomeData;   // a null body reads as {} (the empty state)
}

export async function fetchLibraries(): Promise<Library[]> {
  const res = await apiFetch('/api/home/libraries');
  if (!res.ok) throw new Error(String(res.status));
  return (await res.json()).libraries || [];
}

export async function saveLibraries(libs: Library[]): Promise<Library[]> {
  const res = await apiFetch('/api/home/libraries', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ libraries: libs.map(l => ({ id: l.id, enabled: l.enabled })) }),
  });
  if (!res.ok) throw new Error(String(res.status));
  return (await res.json()).libraries || [];
}

const TMDB_POSTER = 'https://image.tmdb.org/t/p/w342';

/** v254: What you're missing slate movies as Home cards (app/home_rails.wym_items' shape). */
export function missingCards(movies: unknown): Card[] {
  if (!Array.isArray(movies)) return [];
  const out: Card[] = [];
  for (const m of movies as Record<string, unknown>[]) {
    const tid = m?.tmdb_id;
    if (typeof tid !== 'number' || tid <= 0) continue;
    const p = m.poster_path;
    out.push({ key: `missing-${tid}`, type: 'movie', title: String(m.title ?? ''), subtitle: m.year ? String(m.year) : '',
      tmdb_id: tid, poster: typeof p === 'string' && p.startsWith('/') ? `${TMDB_POSTER}${p}` : null });
  }
  return out;
}

/** v254: dismiss a What you're missing film for good; the answer carries the topped-up slate. */
export async function dismissMissing(tmdbId: number): Promise<{ ok: boolean; items: Card[] }> {
  const res = await apiFetch('/api/rails/what-youre-missing/dismiss', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tmdb_id: tmdbId }),
  });
  if (!res.ok) return { ok: false, items: [] };
  const d = await res.json().catch(() => ({}));
  return { ok: true, items: missingCards(d?.rail?.movies) };
}

export interface KioskLibraries { id: string; libraries: { id: string; title: string }[] }

/** Which movie library the web kiosk slideshow uses (owner/admin only). */
export async function fetchKioskLibrary(): Promise<KioskLibraries> {
  const res = await apiFetch('/api/home/kiosk');
  if (!res.ok) throw new Error(String(res.status));
  const d = await res.json();
  return { id: String(d.id || ''), libraries: Array.isArray(d.libraries) ? d.libraries : [] };
}

export async function saveKioskLibrary(id: string): Promise<KioskLibraries> {
  const res = await apiFetch('/api/home/kiosk', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }),
  });
  if (!res.ok) throw new Error(String(res.status));
  const d = await res.json();
  return { id: String(d.id || ''), libraries: Array.isArray(d.libraries) ? d.libraries : [] };
}

export function move<T>(list: T[], from: number, to: number): T[] {
  if (to < 0 || to >= list.length || from === to) return list;
  const next = list.slice();
  const [it] = next.splice(from, 1);
  next.splice(to, 0, it as T);
  return next;
}

/** Prod kiosk's hero data: the selected library's most recent movies. */
export async function fetchHeroMovies(): Promise<HeroMovie[]> {
  const res = await apiFetch('/api/hero/content');
  if (!res.ok) throw new Error(String(res.status));
  const data = (await res.json()) as HeroContentResponse;
  return data.success ? (data.movies || []).map(mapHeroMovie) : [];
}
