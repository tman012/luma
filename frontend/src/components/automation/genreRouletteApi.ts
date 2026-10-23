/** v252 Settings > Automation > Genre Roulette: /api/settings/automation/genre-roulette (automation.db on the test install; owner only). */
import { apiFetch } from '@/apiClient';

export interface GrSettings {
  enabled: boolean; dry_run: boolean; time: string; days: 'daily' | 'weekdays' | 'weekends';
  genres_per_run: number; movies_per_genre: number; include_genres: string[]; exclude_genres: string[];
  year_from: number; year_to: number; language: string; min_votes: number; imdb_min_rating: number; imdb_min_votes: number;
  profile_id: number | null; min_seeders: number; skip_same_collection: boolean;
}
export interface GrPick { tmdb_id: number; title: string; year: number | null; genre: string; poster_path: string | null }
export interface GrRun {
  id: number; started_at: number; finished_at: number | null; kind: string; dry: boolean; status: string;
  wanted: number; grabbed: number; stop_reason: string | null; genres: string[]; picks: GrPick[];
  failures: { genre: string; reason: string }[]; error: string | null;
}
export interface GrStatus {
  running: boolean; enabled: boolean; next_run: string | null; failures_7d: number;
  last_run: Pick<GrRun, 'id' | 'started_at' | 'finished_at' | 'grabbed' | 'wanted' | 'status' | 'stop_reason' | 'error' | 'dry'> | null;
}
export interface GrProfile { id: number; name: string; default: boolean }
export interface GrView { settings: GrSettings; genres: string[]; profiles: GrProfile[]; imdb_active: boolean; status: GrStatus; history: GrRun[] }
export interface GrPreview { running: boolean; result: (Partial<GrRun> & { wanted?: number }) | null }
export interface GrLive { status: GrStatus; history: GrRun[]; preview: GrPreview }

export type R<T> = { ok: true; data: T } | { ok: false; status: number; errors?: Record<string, string> };
export const URL = '/api/settings/automation/genre-roulette';

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

export const loadGr = () => call<GrView>(URL);
export const saveGr = (patch: Partial<GrSettings>) => call<GrView>(URL, json('PUT', patch));
export const loadGrLive = () => call<GrLive>(`${URL}/status`);
export const runGr = () => call<{ ok: boolean }>(`${URL}/run`, { method: 'POST' });
export const previewGr = () => call<{ ok: boolean }>(`${URL}/preview`, { method: 'POST' });
