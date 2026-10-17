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
  poster_path: string | null; monitored: boolean; files: number; episodes: number; linked: number; mismatch: number; unrecognized: number;
  /** What needs review, in words ("S03E01 is in Season 2", "1 file not recognized"). */
  review?: string[];
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

/** "12 episodes", "1 episode". */
export const episodeCount = (s: Show) => `${s.episodes} ${s.episodes === 1 ? 'episode' : 'episodes'}`;

/** Some files don't fit an episode of the show (mismatch or unrecognized). */
export const needsReview = (s: Show) => s.mismatch + s.unrecognized > 0;

/** "2 need review", "1 needs review", '' when nothing does. */
export function reviewCount(s: Show): string {
  const n = s.mismatch + s.unrecognized;
  return n ? `${n} ${n === 1 ? 'needs' : 'need'} review` : '';
}

/** The show's subtitle: "4 episodes · 2 need review". */
export const showSubtitle = (s: Show) => [episodeCount(s), reviewCount(s)].filter(Boolean).join(' \u00b7 ');

/** "Last scan 3:05 PM" today, else "Last scan Oct 16"; "Not scanned yet". */
export function lastScanText(ts: number | null, now = Date.now()): string {
  if (!ts) return 'Not scanned yet';
  const d = new Date(ts * 1000);
  const same = d.toDateString() === new Date(now).toDateString();
  return 'Last scan ' + (same
    ? d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
    : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }));
}
