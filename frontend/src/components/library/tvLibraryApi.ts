/** Settings > TV API (owner only, luma-test builds): TV library paths, the
 * scanned shows, unmatched folders, fix-match and Upgrades. */
import { apiFetch } from '@/apiClient';
import { SAVE_FAILED, UNREACHABLE, type Errors, type LibPath } from './libraryPathsApi';

export const TV_URL = '/api/settings/tv';
export type UpgradeMode = 'keep_both' | 'replace_verified';
export interface TvSettingsView { paths: LibPath[]; upgrades: { mode: UpgradeMode }; rescan_interval_h: number }
export interface Candidate { tmdb_id: number; title: string; year: number | null; poster_path?: string | null }
export interface Show {
  id: number; tmdb_id: number; title: string; year: number | null; path_id: string; path_name: string; folder: string;
  poster_path: string | null; monitored: boolean; files: number; linked: number; mismatch: number; unrecognized: number;
}
export interface Unmatched {
  id: number; path_id: string; path_name: string; folder: string; guess_title: string | null; guess_year: number | null;
  candidates: Candidate[]; reason: string | null;
}
export interface ScanStatus { running: boolean; error: string | null; last_scan: number | null }
export interface TvLibrary { shows: Show[]; unmatched: Unmatched[]; scan: ScanStatus }

type R<T> = { ok: true; data: T } | { ok: false; errors: Errors; error: string };

async function call<T>(url: string, init: RequestInit | undefined, valid: (d: Record<string, unknown>) => boolean): Promise<R<T>> {
  try {
    const res = await apiFetch(url, init);
    const data = await res.json().catch(() => ({})) as Record<string, unknown> & { errors?: Errors; error?: string };
    if (res.ok && valid(data)) return { ok: true, data: data as unknown as T };
    return { ok: false, errors: data.errors || {}, error: data.error || SAVE_FAILED };
  } catch {
    return { ok: false, errors: {}, error: UNREACHABLE };
  }
}
const json = (method: string, body?: unknown): RequestInit =>
  ({ method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
const isView = (d: Record<string, unknown>) => Array.isArray(d.paths);
const isLib = (d: Record<string, unknown>) => Array.isArray(d.shows) && Array.isArray(d.unmatched);

export const loadTv = () => call<TvSettingsView>(TV_URL, undefined, isView);
export const addTvPath = (name: string, path: string) => call<TvSettingsView>(`${TV_URL}/paths`, json('POST', { name, path }), isView);
export const editTvPath = (id: string, name: string, path: string) =>
  call<TvSettingsView>(`${TV_URL}/paths/${encodeURIComponent(id)}`, json('PUT', { name, path }), isView);
export const deleteTvPath = (id: string) => call<TvSettingsView>(`${TV_URL}/paths/${encodeURIComponent(id)}`, json('DELETE'), isView);
export const saveUpgrades = (mode: UpgradeMode) => call<TvSettingsView>(TV_URL, json('PUT', { upgrades: { mode } }), isView);

export const loadTvLibrary = () => call<TvLibrary>(`${TV_URL}/library`, undefined, isLib);
export const startScan = () => call<{ scan: ScanStatus }>(`${TV_URL}/scan`, json('POST'), d => !!d.scan);
export const searchShows = (q: string, year?: number | null) =>
  call<{ results: Candidate[] }>(`${TV_URL}/search?q=${encodeURIComponent(q)}${year ? `&year=${year}` : ''}`, undefined,
    d => Array.isArray(d.results));
export const rematchShow = (id: number, tmdb_id: number) =>
  call<TvLibrary>(`${TV_URL}/shows/${id}/match`, json('PUT', { tmdb_id }), isLib);
export const matchFolder = (id: number, tmdb_id: number) =>
  call<TvLibrary>(`${TV_URL}/unmatched/${id}/match`, json('POST', { tmdb_id }), isLib);

/** "Severance (2022)" or "Severance". */
export const showLabel = (c: { title: string; year: number | null }) => (c.year ? `${c.title} (${c.year})` : c.title);

/** "3 linked · 1 mismatch · 2 unrecognized", or "No files". */
export function fileCounts(s: Show): string {
  if (!s.files) return 'No files';
  const parts = [`${s.linked} linked`];
  if (s.mismatch) parts.push(`${s.mismatch} mismatch`);
  if (s.unrecognized) parts.push(`${s.unrecognized} unrecognized`);
  return parts.join(' · ');
}

/** "Last scan 3:05 PM" today, else "Last scan Oct 16"; "Not scanned yet". */
export function lastScanText(ts: number | null, now = Date.now()): string {
  if (!ts) return 'Not scanned yet';
  const d = new Date(ts * 1000);
  const same = d.toDateString() === new Date(now).toDateString();
  return 'Last scan ' + (same
    ? d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
    : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }));
}
