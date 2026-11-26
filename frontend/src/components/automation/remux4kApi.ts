/** v359 Settings > Automation > 4K Remux auto-grab: /api/settings/automation/4k-remux (automation.db on the test install; owner only). */
import { call, json } from './genreRouletteApi';

export interface FkSettings { enabled: boolean; min_seeders: number; max_age_hours: number; scan_interval_minutes: number; plex_4k_section_id: number }
export interface FkRelease {
  release: string; tracker: string | null; seeders: number; size: number | null; age_hours: number | null;
  decision: string | null; reason: string | null; tmdb_id?: number; title?: string; year?: number | null;
}
export interface FkResult {
  checked: number; remux: number; candidates: number; grabbed: number; would_grab: number; grabbed_movies: string[];
  skipped_in_4k: number; skipped_in_4k_titles: string[]; rejected: number; rejected_details: { title: string; reason: string }[];
  trackers: { name: string; status: string; results: number }[]; releases?: FkRelease[]; error: string | null; dry: boolean;
}
export interface FkStatus {
  running: boolean; enabled: boolean; next_run: number | null; last_run: number | null; last_kind: string | null;
  last_result: FkResult | null; grabbed_total: number;
}
export interface FkRun extends Partial<FkResult> { id: number; started_at: number; finished_at: number | null; kind: string; status: string }
export interface FkGrab { hash: string; tmdb_id: number; title: string; year: number | null; release: string; tracker: string | null; seeders: number | null; grabbed_at: number }
export interface FkPreview { running: boolean; at: number | null; result: FkResult | null }
export interface FkView { settings: FkSettings; libraries: { id: number; title: string }[]; status: FkStatus; history: FkRun[]; grabs: FkGrab[] }
export interface FkLive { status: FkStatus; history: FkRun[]; preview: FkPreview }

export const FK_URL = '/api/settings/automation/4k-remux';
export const loadFk = () => call<FkView>(FK_URL);
export const saveFk = (patch: Partial<FkSettings>) => call<FkView>(FK_URL, json('PUT', patch));
export const loadFkLive = () => call<FkLive>(`${FK_URL}/status`);
export const runFk = () => call<{ ok: boolean }>(`${FK_URL}/run`, { method: 'POST' });
export const previewFk = () => call<{ ok: boolean }>(`${FK_URL}/preview`, { method: 'POST' });
