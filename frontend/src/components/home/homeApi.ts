import { apiFetch, apiUrl } from '@/apiClient';

export interface Card {
  key: string; type: string; title: string; subtitle: string;
  poster: string | null; art?: string | null; progress?: number;
}
export interface Rail { id: string; title: string; type: string; items: Card[] }
export interface HomeData { continue_watching: Card[]; rails: Rail[]; plex: boolean }
export interface Library { id: string; title: string; type: string; enabled: boolean }

/** Image paths come from the server without the URL base. */
export const img = (p: string | null | undefined) => (p ? apiUrl(p) : null);

export async function fetchHome(): Promise<HomeData> {
  const res = await apiFetch('/api/home');
  if (!res.ok) throw new Error(String(res.status));
  return res.json();
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

export function move<T>(list: T[], from: number, to: number): T[] {
  if (to < 0 || to >= list.length || from === to) return list;
  const next = list.slice();
  const [it] = next.splice(from, 1);
  next.splice(to, 0, it as T);
  return next;
}
