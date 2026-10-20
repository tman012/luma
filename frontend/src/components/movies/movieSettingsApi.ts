/** v200 Settings > Movies: /api/settings/movies (movies.db on the test install; owner only). */
import { apiFetch } from '@/apiClient';

export interface MovieQuality { id: string; name: string }
export interface MovieProfile {
  id: number; name: string; items: { id: string; allowed: boolean }[]; cutoff: string | null;
  min_mb_per_min: number | null; max_mb_per_min: number | null; default: boolean;
}
export interface MovieTracker { id: number; name: string; enabled: boolean }
export interface MovieSettings {
  monitoring: boolean; preferred_groups: string[]; trackers: MovieTracker[]; profiles: MovieProfile[]; qualities: MovieQuality[];
  picker_profiles: number[];
}
export type R<T> = { ok: true; data: T } | { ok: false; error: string };

export const URL = '/api/settings/movies';
const FAILED = "Couldn't save";

async function call<T>(url: string, init?: RequestInit): Promise<R<T>> {
  try {
    const res = await apiFetch(url, init);
    const data = await res.json().catch(() => ({}));
    return res.ok ? { ok: true, data: data as T } : { ok: false, error: FAILED };
  } catch {
    return { ok: false, error: FAILED };
  }
}
const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export const loadMovieSettings = () => call<MovieSettings>(URL);
export const saveMovieSettings = (body: Partial<Pick<MovieSettings, 'monitoring' | 'preferred_groups' | 'picker_profiles'>> & { trackers?: { id: number; enabled: boolean }[] }) =>
  call<MovieSettings>(URL, json('PUT', body));
export const saveMovieProfile = (p: Omit<MovieProfile, 'id'>, id?: number) =>
  call<MovieProfile>(id == null ? `${URL}/profiles` : `${URL}/profiles/${id}`, json(id == null ? 'POST' : 'PUT', p));
export const deleteMovieProfile = (id: number) => call<{ ok: boolean }>(`${URL}/profiles/${id}`, { method: 'DELETE' });
