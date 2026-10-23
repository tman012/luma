/** v254 Settings > Automation > What you're missing: /api/settings/automation/what-youre-missing (owner only;
 * changes need the signed-in owner) and the rail's restore and refresh routes. */
import { apiFetch } from '@/apiClient';

export interface WymSettings {
  movies_shown: number; rebuild_hours: number; no_repeat_days: number;
  year_from: number; year_to: number | null; languages: string[];
  min_tmdb_votes: number; min_imdb_votes: number; min_trakt_watchers: number;
  trakt_lists: string[]; tmdb_lists: string[]; exclude_genres: string[];
  include_adult: boolean; require_poster: boolean;
}
export interface WymStatus {
  on_home: boolean; building: boolean; last_rebuilt: string | null; pool_size: number;
  next_rebuild: string | null; error: string | null;
}
export interface WymDismissed { tmdb_id: number; title: string; year: number | null; poster_path: string | null; at: number }
export interface Opt { id: string; label: string }
export interface WymView {
  settings: WymSettings; trakt_lists: Opt[]; tmdb_lists: Opt[]; genres: string[]; languages: Opt[];
  rebuild_hours: number[]; movies_min: number; movies_max: number; no_repeat_max: number;
  imdb_active: boolean; sources: { trakt: boolean; tmdb: boolean; omdb: boolean };
  status: WymStatus; dismissed: WymDismissed[];
}

export type R<T> = { ok: true; data: T } | { ok: false; status: number; errors?: Record<string, string> };
export const URL = '/api/settings/automation/what-youre-missing';
export const RAIL = '/api/rails/what-youre-missing';

async function call<T>(url: string, init?: RequestInit): Promise<R<T>> {
  try {
    const res = await apiFetch(url, init);
    const data = await res.json().catch(() => ({}));
    return res.ok ? { ok: true, data: data as T } : { ok: false, status: res.status, errors: (data as { errors?: Record<string, string> }).errors };
  } catch {
    return { ok: false, status: 0 };
  }
}
const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export const loadWym = () => call<WymView>(URL);
export const saveWym = (patch: Partial<WymSettings>) => call<WymView>(URL, json('PUT', patch));
export const loadWymStatus = () => call<{ status: WymStatus }>(`${URL}/status`);
export const refreshWym = () => call<{ ok: boolean; status: WymStatus }>(`${RAIL}/refresh`, { method: 'POST' });
export const restoreWym = (tmdbId: number) => call<{ ok: boolean; dismissed: WymDismissed[] }>(`${RAIL}/restore`, json('POST', { tmdb_id: tmdbId }));
