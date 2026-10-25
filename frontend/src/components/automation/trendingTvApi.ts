/** v279 Settings > Automation > Trending TV: /api/settings/automation/trending-tv (automation.db on the test install; owner only). */
import { call, json } from './genreRouletteApi';
import type { MonitorOption } from '@/components/library/tvCalendarApi';

export interface TtvSettings {
  enabled: boolean; limit: number; profile_id: number | null; search_on_add: boolean; languages: string[];
  allow_unknown_language: boolean; skip_plex: boolean; skip_if_plex_unreadable: boolean; no_readd: boolean;
  page: number; path_id: string | null; monitor: MonitorOption; require_tvdb: boolean; one_season_only: boolean;
  interval_hours: number; when_busy: 'skip' | 'retry'; history_kept: 50 | 200 | 500;
}
export interface TtvCounts { checked: number; added: number; skipped: Record<string, number>; errors: string[]; error: string | null }
export interface TtvStatus { running: boolean; enabled: boolean; next_run: number | null; last_run: number | null; last_kind: string | null; last_result: TtvCounts | null }
export interface TtvEntry { id: number; tmdb_id: number; tvdb_id: number | null; title: string; year: number | null; added_at: number }
export interface TtvOption { id: number; name: string; default?: boolean }
export interface TtvView {
  settings: TtvSettings; profiles: TtvOption[]; libraries: { id: string; name: string }[];
  monitor: string[]; languages: string[]; status: TtvStatus; history: TtvEntry[];
}
export type TtvPatch = Partial<TtvSettings>;

export const TTV_URL = '/api/settings/automation/trending-tv';
export const loadTtv = () => call<TtvView>(TTV_URL);
export const saveTtv = (patch: TtvPatch) => call<TtvView>(TTV_URL, json('PUT', patch));
export const loadTtvLive = () => call<{ status: TtvStatus; history: TtvEntry[] }>(`${TTV_URL}/status`);
export const runTtv = () => call<{ ok: boolean }>(`${TTV_URL}/run`, { method: 'POST' });
