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

type Raw = Record<string, unknown>;
const obj = (v: unknown): Raw | null => (v && typeof v === 'object' && !Array.isArray(v) ? v as Raw : null);
const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? v as T[] : []);
const strOr = (v: unknown, d: string): string => (typeof v === 'string' ? v : d);

/** Built-in defaults (backend downloads_guard DEFAULTS), used only for fields an answer leaves out. */
export const DG_FALLBACK: DgSettings = {
  enabled: false, check_interval_minutes: 15, min_free_gb: 150, reclaim_gb: 100, daily_cap_gb: 500, max_run_gb: 2048,
  trackers: [], pill_gb: 100, min_age_days: 2, min_seed_hours: 0, min_ratio: 0, scope: 'all', folder: '',
  protected_tags: [], protected_hashes: [],
};

function settingsOf(raw: unknown, base: DgSettings): DgSettings {
  const r = obj(raw) ?? {};
  const out = { ...base } as unknown as Raw;
  for (const k of Object.keys(base) as (keyof DgSettings)[]) {
    if (!(k in r) || r[k] == null) continue;
    const b = base[k];
    if (Array.isArray(b)) out[k] = arr<string>(r[k]);
    else if (typeof b === 'number') { const n = Number(r[k]); if (Number.isFinite(n)) out[k] = n; }
    else if (typeof b === 'boolean') out[k] = r[k] === true;
    else if (typeof r[k] === 'string') out[k] = r[k];
  }
  return out as unknown as DgSettings;
}

/** v264 (as v263's What you're missing): a settings answer of {} (or one missing fields) never breaks the card.
 * Missing settings read as the last known (`prev`), then the answer's defaults, then DG_FALLBACK; missing lists
 * are empty. No settings, defaults or last known at all reads as a failed load (null). */
export function normalizeDgView(raw: unknown, prev?: DgView | null): DgView | null {
  const r = obj(raw) ?? {};
  if (!obj(r.settings) && !obj(r.defaults) && !prev) return null;
  const defaults = settingsOf(r.defaults, prev?.defaults ?? DG_FALLBACK);
  const settings = settingsOf(r.settings, prev?.settings ?? defaults);
  const names = obj(r.protected_names);
  return {
    settings, defaults,
    folder: strOr(r.folder, prev?.folder ?? ''),
    media_folder: strOr(r.media_folder, prev?.media_folder ?? ''),
    trackers: 'trackers' in r ? arr<DgTracker>(r.trackers) : prev?.trackers ?? [],
    allowed: 'allowed' in r ? r.allowed === true : prev?.allowed ?? false,
    protected_names: names ? names as Record<string, string> : prev?.protected_names,
  };
}

/** The status answer null-safe: no status object is null; a last result's lists are always arrays. */
export function normalizeDgStatus(raw: unknown): { status: DgStatus | null; log: DgLogEntry[] } {
  const r = obj(raw) ?? {};
  const st = obj(r.status);
  if (!st) return { status: null, log: arr<DgLogEntry>(r.log) };
  const lr = obj(st.last_result);
  return {
    status: {
      running: st.running === true,
      last_check: typeof st.last_check === 'string' ? st.last_check : null,
      last_run: typeof st.last_run === 'string' ? st.last_run : null,
      last_result: lr ? { ...(lr as unknown as DgResult), deleted: arr<DgEntry>(lr.deleted), failed: arr<string>(lr.failed),
        freed_bytes: Number(lr.freed_bytes) || 0, dry_run: lr.dry_run === true, triggered: lr.triggered === true } : null,
      evaluation: (obj(st.evaluation) as unknown as DgEvaluation) ?? null,
    },
    log: arr<DgLogEntry>(r.log),
  };
}

export const loadDg = () => call<unknown>(URL);
export const saveDg = (patch: Partial<DgSettings>) => call<unknown>(URL, json('PUT', patch));
export const loadDgStatus = () => call<unknown>(`${GUARD}/status`);
/** v264: the tracker summary null-safe (an answer of {} reads as unavailable with no trackers). */
export const loadDgTrackers = async (): Promise<R<DgTrackerSummary>> => {
  const r = await call<unknown>(`${GUARD}/trackers`);
  if (!r.ok) return r;
  const d = obj(r.data) ?? {};
  return { ok: true, data: { available: d.available === true, trackers: arr<DgTrackerSummary['trackers'][number]>(d.trackers) } };
};
export const previewDg = () => call<DgPreview>(`${GUARD}/preview`);
export const runDg = () => call<{ started: boolean }>(`${GUARD}/run`, json('POST', { dry_run: false }));
export const keepDg = (hashes: string[]) => call<{ protected_hashes: string[] }>(`${GUARD}/keep`, json('POST', { hashes }));
export const unkeepDg = (hashes: string[]) => call<{ protected_hashes: string[] }>(`${GUARD}/unkeep`, json('POST', { hashes }));
