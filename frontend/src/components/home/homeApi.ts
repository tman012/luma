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
export interface Rail { id: string; title: string; type: string; items: Card[] }
export interface HomeData { continue_watching: Card[]; rails: Rail[]; plex: boolean }
export interface Library { id: string; title: string; type: string; enabled: boolean; source?: 'plex' | 'trakt' }

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
