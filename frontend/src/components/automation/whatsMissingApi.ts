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

type Raw = Record<string, unknown>;
const obj = (v: unknown): Raw | null => (v && typeof v === 'object' && !Array.isArray(v) ? v as Raw : null);
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? v as T[] : []);

/** v263: a status answer of {} (or one missing fields) never breaks the card: each missing field reads as idle
 * and empty; `prev` keeps the last known values for fields the answer leaves out. */
export function normalizeStatus(raw: unknown, prev?: WymStatus): WymStatus {
  const r = obj(raw) ?? {};
  const has = (k: string) => k in r;
  const base: WymStatus = prev ?? { on_home: true, building: false, last_rebuilt: null, pool_size: 0, next_rebuild: null, error: null };
  const n = Number(r.pool_size);
  return {
    on_home: has('on_home') ? r.on_home !== false : base.on_home,
    building: has('building') ? r.building === true : base.building,
    last_rebuilt: has('last_rebuilt') ? str(r.last_rebuilt) : base.last_rebuilt,
    pool_size: has('pool_size') ? (Number.isFinite(n) && n > 0 ? n : 0) : base.pool_size,
    next_rebuild: has('next_rebuild') ? str(r.next_rebuild) : base.next_rebuild,
    error: has('error') ? str(r.error) : base.error,
  };
}

/** The settings view with a null-safe status and lists; no settings object reads as a failed load. */
export function normalizeView(raw: unknown): WymView | null {
  const r = obj(raw);
  const s = obj(r?.settings);
  if (!r || !s) return null;
  const settings = { ...s, languages: arr<string>(s.languages), trakt_lists: arr<string>(s.trakt_lists),
    tmdb_lists: arr<string>(s.tmdb_lists), exclude_genres: arr<string>(s.exclude_genres) } as unknown as WymSettings;
  const src = obj(r.sources) ?? {};
  return {
    ...(r as unknown as WymView), settings,
    trakt_lists: arr<Opt>(r.trakt_lists), tmdb_lists: arr<Opt>(r.tmdb_lists), genres: arr<string>(r.genres),
    languages: arr<Opt>(r.languages), rebuild_hours: arr<number>(r.rebuild_hours), dismissed: arr<WymDismissed>(r.dismissed),
    imdb_active: r.imdb_active === true, sources: { trakt: src.trakt === true, tmdb: src.tmdb === true, omdb: src.omdb === true },
    status: normalizeStatus(r.status),
  };
}

async function view(url: string, init?: RequestInit): Promise<R<WymView>> {
  const r = await call<unknown>(url, init);
  if (!r.ok) return r;
  const v = normalizeView(r.data);
  return v ? { ok: true, data: v } : { ok: false, status: 0 };
}

export const loadWym = () => view(URL);
export const saveWym = (patch: Partial<WymSettings>) => view(URL, json('PUT', patch));
/** status is undefined when the answer has none (keep the last known). */
export const loadWymStatus = () => call<{ status?: unknown }>(`${URL}/status`);
export const refreshWym = () => call<{ ok?: boolean; status?: unknown }>(`${RAIL}/refresh`, { method: 'POST' });
export const restoreWym = (tmdbId: number) => call<{ ok: boolean; dismissed: WymDismissed[] }>(`${RAIL}/restore`, json('POST', { tmdb_id: tmdbId }));
