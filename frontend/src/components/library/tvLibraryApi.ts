/** Settings > TV API (owner only, luma-test builds): the selected TV libraries, Import
 * and its progress, the review rows and Import all, the shows, fix-match and Upgrades. */
import { apiFetch } from '@/apiClient';
import type { Naming } from './TvNaming';
import { SAVE_FAILED, UNREACHABLE, type Errors, type LibPath } from './libraryPathsApi';

export const TV_URL = '/api/settings/tv';
export type UpgradeMode = 'keep_both' | 'replace_verified';
export interface TvSettingsView { paths: LibPath[]; upgrades: { mode: UpgradeMode }; rescan_interval_h: number; naming?: Naming }
export interface Candidate { tmdb_id: number; title: string; year: number | null; poster_path?: string | null }
export interface Show {
  id: number; tmdb_id: number; title: string; year: number | null; path_id: string; path_name: string; folder: string;
  poster_path: string | null; monitored: boolean; files: number; episodes: number; linked: number; mismatch: number; unrecognized: number;
  /** What needs review, in words ("S02E40 isn't an episode of this show", "1 file not recognized"). */
  review?: string[];
  /** Series grid: aired episodes (no specials), those with a file, and whether TMDB says it continues. */
  aired?: number; have?: number; status?: string | null; continuing?: boolean; added_at?: number | null;
  /** v181: the Series list view (bulk editor) columns. */
  monitor?: string | null; monitor_new_seasons?: 'all' | 'none'; profile_id?: number | null; series_type?: string | null; season_folder?: boolean;
}
export type ReviewTier = 'likely' | 'none' | 'duplicate';
export interface Unmatched {
  id: number; path_id: string; path_name: string; folder: string; guess_title: string | null; guess_year: number | null;
  candidates: Candidate[]; reason: string | null;
  /** likely: best guess picked; none: no match; duplicate: another folder has the show. */
  tier?: ReviewTier; pick?: number | null;
  /** The best guess when nothing backs it (reason "unclear"): shown first, never pre-checked. */
  guess_pick?: number | null;
}
export interface ImportProgress {
  running: boolean; error: string | null; last_scan: number | null;
  total: number; done: number; matched: number; review: number; none: number;
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
/** The TV libraries selector (LibrarySelector / librarySelectionApi). */
export const TV_LIBRARIES_URL = `${TV_URL}/libraries`;
export const saveUpgrades = (mode: UpgradeMode) => call<TvSettingsView>(TV_URL, json('PUT', { upgrades: { mode } }), isView);

export const loadTvLibrary = () => call<TvLibrary>(`${TV_URL}/library`, undefined, isLib);
export const startScan = () => call<{ scan: ScanStatus }>(`${TV_URL}/scan`, json('POST'), d => !!d.scan);
export const startImport = () => call<{ scan: ScanStatus }>(`${TV_URL}/import`, json('POST'), d => !!d.scan);
export const loadProgress = () => call<ImportProgress>(`${TV_URL}/import`, undefined, d => typeof d.done === 'number');
export interface ImportAllResult extends TvLibrary { imported: number[]; failed: { id: number; error: string }[] }
export const importAll = (items: { id: number; tmdb_id: number }[]) =>
  call<ImportAllResult>(`${TV_URL}/import-all`, json('POST', { items }), d => Array.isArray(d.imported) && isLib(d));
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

/** "Last import 9:02 PM" today, else "Last import Oct 16"; '' before the first one. */
export function lastImportText(ts: number | null, now = Date.now()): string {
  return ts ? lastScanText(ts, now).replace(/^Last scan /, 'Last import ') : '';
}

/** "Imported 3 shows", "Imported 1 show". */
export const importedText = (n: number) => `Imported ${n} ${n === 1 ? 'show' : 'shows'}`;

/** Review order: no match, then duplicates, then likely. */
const TIER_ORDER: Record<ReviewTier, number> = { none: 0, duplicate: 1, likely: 2 };
export const reviewTier = (u: Unmatched): ReviewTier => u.tier || (u.reason === 'duplicate' ? 'duplicate' : 'none');
export const sortReview = (rows: Unmatched[]) =>
  [...rows].sort((a, b) => TIER_ORDER[reviewTier(a)] - TIER_ORDER[reviewTier(b)] || a.folder.localeCompare(b.folder));

/** A review row's starting pick: likely and duplicate rows have one; none rows don't. */
export function startingPick(u: Unmatched): Candidate | null {
  const t = reviewTier(u);
  if (t === 'none') return null;
  const id = u.pick ?? u.guess_pick ?? u.candidates[0]?.tmdb_id;
  return u.candidates.find(c => c.tmdb_id === id) || u.candidates[0] || null;
}

/** Likely rows start checked; none and duplicate rows don't, and neither do same-name rows the files
 * don't single out (reason "unclear": pre-picked, unchecked). */
export const startsChecked = (u: Unmatched) => reviewTier(u) === 'likely' && u.reason !== 'unclear' && u.pick != null && !!startingPick(u);
