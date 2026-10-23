/** v260 Settings > Automation > Downloads guard: /api/settings/automation/downloads-guard (owner only; enabling or
 * widening needs the signed-in owner) and prod's /api/downloads-guard/* status, trackers, preview and run. */
import { apiFetch } from '@/apiClient';

export type Scope = 'all' | 'test_tagged';
export interface DgSettings {
  enabled: boolean; check_interval_minutes: number; min_free_gb: number; reclaim_gb: number; daily_cap_gb: number;
  max_run_gb: number; trackers: string[]; pill_gb: number; min_age_days: number; min_seed_hours: number;
  min_ratio: number; scope: Scope; folder: string;
  protected_tags: string[]; protected_hashes: string[];
}
export interface DgTracker { key: string; label: string }
export interface DgView {
  settings: DgSettings; defaults: DgSettings; folder: string; media_folder: string; trackers: DgTracker[]; allowed: boolean;
  /** v261: qBit's names of the Never delete hashes (a hash qBit never named is absent). */
  protected_names?: Record<string, string>;
}
export interface DgEntry { name: string; size_bytes: number; mtime?: number; tracker_label?: string | null; phase?: string }
/** v260 Preview row: deletion order, kept (Never delete) downloads in place and excluded. */
export interface DgRow extends DgEntry { hashes: string[]; kept: boolean; kept_hashes: string[]; kept_by_tags: string[]; torrent_name?: string }
export interface DgResult {
  at?: string; dry_run: boolean; triggered: boolean; reason?: string; error?: string | null; freed_bytes: number;
  freed_preferred_bytes?: number; freed_fallback_bytes?: number; free_after_bytes?: number; short?: boolean;
  deleted: DgEntry[]; failed: string[];
}
export interface DgEvaluation {
  enabled: boolean; available: boolean; free_bytes: number; total_bytes?: number; min_free_bytes: number;
  daily_cap_bytes: number; freed_today_bytes: number; triggered: boolean; reason?: string;
}
export interface DgStatus {
  running: boolean; last_check: string | null; last_run: string | null; last_result: DgResult | null;
  evaluation: DgEvaluation | null;
}
export interface DgLogEntry { at: string; outcome: string; summary: string }
export interface DgPreview {
  error?: string | null; target_bytes: number; planned_bytes: number; preferred_bytes: number; fallback_bytes: number;
  preferred_available: number; eligible_count: number; short: boolean; entries: DgEntry[]; rows?: DgRow[];
}
export interface DgTrackerSummary { available: boolean; trackers: { key: string; label: string; count: number; size_bytes: number }[] }

export type R<T> = { ok: true; data: T } | { ok: false; status: number; signIn?: boolean; error?: string; errors?: Record<string, string> };
export const URL = '/api/settings/automation/downloads-guard';
export const GUARD = '/api/downloads-guard';

async function call<T>(url: string, init?: RequestInit): Promise<R<T>> {
  try {
    const res = await apiFetch(url, init);
    const data = await res.json().catch(() => ({}));
    if (res.ok) return { ok: true, data: data as T };
    const d = data as { errors?: Record<string, string>; error?: string; sign_in?: boolean };
    return { ok: false, status: res.status, signIn: res.status === 401, error: d.error, errors: d.errors };
  } catch {
    return { ok: false, status: 0 };
  }
}
const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export const loadDg = () => call<DgView>(URL);
export const saveDg = (patch: Partial<DgSettings>) => call<DgView>(URL, json('PUT', patch));
export const loadDgStatus = () => call<{ status: DgStatus; log: DgLogEntry[] }>(`${GUARD}/status`);
export const loadDgTrackers = () => call<DgTrackerSummary>(`${GUARD}/trackers`);
export const previewDg = () => call<DgPreview>(`${GUARD}/preview`);
export const runDg = () => call<{ started: boolean }>(`${GUARD}/run`, json('POST', { dry_run: false }));
export const keepDg = (hashes: string[]) => call<{ protected_hashes: string[] }>(`${GUARD}/keep`, json('POST', { hashes }));
export const unkeepDg = (hashes: string[]) => call<{ protected_hashes: string[] }>(`${GUARD}/unkeep`, json('POST', { hashes }));
