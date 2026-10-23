/** v253 Settings > Automation > Anticipated: /api/settings/automation/anticipated (automation.db on the test install; owner only). */
import { call, json } from './genreRouletteApi';
import type { MonitorOption } from '@/components/library/tvCalendarApi';

export type Kind = 'shows' | 'movies';
export interface AnGroup {
  enabled: boolean; limit: number; profile_id: number | null; search_on_add: boolean; languages: string[];
  allow_unknown_language: boolean; skip_plex: boolean; skip_if_plex_unreadable: boolean; no_readd: boolean; page: number;
  path_id?: string | null; monitor?: MonitorOption; require_tvdb?: boolean;
}
export interface AnSettings { shows: AnGroup; movies: AnGroup; interval_hours: number; when_busy: 'skip' | 'retry'; history_kept: 50 | 200 | 500 }
export interface AnCounts { checked: number; added: number; skipped: Record<string, number>; errors: string[]; error: string | null }
export interface AnResult { shows: AnCounts | null; movies: AnCounts | null; error: string | null }
export interface AnStatus { running: boolean; enabled: boolean; next_run: number | null; last_run: number | null; last_kind: string | null; last_result: AnResult | null }
export interface AnEntry { id: number; kind: Kind; tmdb_id: number; tvdb_id: number | null; title: string; year: number | null; added_at: number }
export interface AnOption { id: number; name: string; default?: boolean }
export interface AnView {
  settings: AnSettings; profiles: AnOption[]; movie_profiles: AnOption[]; libraries: { id: string; name: string }[];
  monitor: string[]; languages: string[]; status: AnStatus; history: AnEntry[];
}
export type AnPatch = Partial<Omit<AnSettings, Kind>> & { shows?: Partial<AnGroup>; movies?: Partial<AnGroup> };

export const AN_URL = '/api/settings/automation/anticipated';
export const loadAn = () => call<AnView>(AN_URL);
export const saveAn = (patch: AnPatch) => call<AnView>(AN_URL, json('PUT', patch));
export const loadAnLive = () => call<{ status: AnStatus; history: AnEntry[] }>(`${AN_URL}/status`);
export const runAn = () => call<{ ok: boolean }>(`${AN_URL}/run`, { method: 'POST' });
