/** Settings > Library paths API (owner only, luma-test builds). */
import { apiFetch } from '@/apiClient';
import { SAVE_FAILED } from '@/utils/messages';

export const LP_URL = '/api/settings/library-paths';
export const RULES_URL = '/api/settings/library-rules';
export const MAP_URL = '/api/settings/library-paths/mapping';

/** 'unavailable' (v170, TV only): the last scan skipped the path as empty (not mounted?). */
export type PathStatus = 'ok' | 'missing' | 'read_only' | 'unavailable';
export interface LibPath {
  id: string; name: string; path: string; status: PathStatus;
  /** Sections (TV) that selected this path: never a Movies rule destination or the fallback. */
  used_by?: string[];
}
/** The paths Movies may file into: not selected by another section (TV). */
export const moviePaths = (paths: LibPath[]) => paths.filter(p => !p.used_by?.length);
export type Unit = 'months' | 'years';
export interface Rule {
  id?: string; enabled: boolean; path_id: string;
  released_within: { n: number; unit: Unit } | null;
  resolution: string[]; source: string[];
}
export interface PathMapRow { remote: string; local: string }
export type Transfer = 'copy' | 'hardlink';
export interface NotFound { title: string; since: number; reason?: 'not_found' | 'not_mapped' }
/** A grab the mover failed (not enough space on the destination); Retry puts it back to waiting. */
export interface FailedJob { hash: string; title: string; since: number; reason: 'no_space' }
export interface LibraryView {
  paths: LibPath[]; rules: Rule[]; fallback_path_id: string | null; path_map: PathMapRow[]; transfer: Transfer;
  downloads?: { path: string; status: 'ok' | 'missing' };
  not_found?: NotFound[];
  failed?: FailedJob[];
}
export type Errors = Record<string, string>;
export type Result = { ok: true; view: LibraryView } | { ok: false; errors: Errors; error: string };

export const UNREACHABLE = "Couldn't reach Luma";
export { SAVE_FAILED };

export const RESOLUTIONS: { id: string; label: string }[] = [
  { id: '2160p', label: '2160p' }, { id: '1080p', label: '1080p' }, { id: '720p', label: '720p' }, { id: 'sd', label: 'SD' },
];
export const SOURCES: { id: string; label: string }[] = [
  { id: 'remux', label: 'Remux' }, { id: 'bluray', label: 'Blu-ray' }, { id: 'web', label: 'WEB' }, { id: 'hdtv', label: 'HDTV' },
];

async function call(url: string, init?: RequestInit): Promise<Result> {
  try {
    const res = await apiFetch(url, init);
    const data = await res.json().catch(() => ({})) as Partial<LibraryView> & { errors?: Errors; error?: string };
    if (res.ok && Array.isArray(data.paths)) return { ok: true, view: data as LibraryView };
    return { ok: false, errors: data.errors || {}, error: data.error || SAVE_FAILED };
  } catch {
    return { ok: false, errors: {}, error: UNREACHABLE };
  }
}
const send = (url: string, method: string, body?: unknown) =>
  call(url, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });

export const loadLibrary = () => call(LP_URL);
export const addPath = (name: string, path: string) => send(LP_URL, 'POST', { name, path });
export const editPath = (id: string, name: string, path: string) => send(`${LP_URL}/${encodeURIComponent(id)}`, 'PUT', { name, path });
export const deletePath = (id: string) => send(`${LP_URL}/${encodeURIComponent(id)}`, 'DELETE');
export const saveRules = (rules: Rule[], fallback_path_id: string | null) => send(RULES_URL, 'PUT', { rules, fallback_path_id });
export const retryJob = (hash: string) => send(`${LP_URL}/jobs/${encodeURIComponent(hash)}/retry`, 'POST');
export const saveMapping = (body: { path_map?: PathMapRow[]; transfer?: Transfer }) => send(MAP_URL, 'PUT', body);

/** Persist media.downloads_root via PUT /api/settings (settings win over env). */
export async function saveDownloadsRoot(path: string): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    const res = await apiFetch('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ media: { downloads_root: path } }),
    });
    const data = await res.json().catch(() => ({})) as { success?: boolean; errors?: string[]; error?: string; sign_in?: boolean };
    if (res.status === 401 || data.sign_in) return { ok: false, error: 'Sign in first' };
    if (res.status === 403) return { ok: false, error: 'Only the owner can change this' };
    if (res.ok && data.success) return { ok: true };
    const err = (Array.isArray(data.errors) && data.errors[0]) || data.error || SAVE_FAILED;
    return { ok: false, error: String(err) };
  } catch {
    return { ok: false, error: UNREACHABLE };
  }
}

/** "Past 2 years · 2160p · Remux", or "All movies". */
export function ruleSummary(r: Rule): string {
  const parts: string[] = [];
  if (r.released_within) {
    const { n, unit } = r.released_within;
    parts.push(`Past ${n} ${n === 1 ? unit.slice(0, -1) : unit}`);
  }
  const lbl = (list: { id: string; label: string }[], ids: string[]) => list.filter(x => ids.includes(x.id)).map(x => x.label).join(', ');
  if (r.resolution.length) parts.push(lbl(RESOLUTIONS, r.resolution));
  if (r.source.length) parts.push(lbl(SOURCES, r.source));
  return parts.length ? parts.join(' · ') : 'All movies';
}

/** "Since 3:05 PM" today, else "Since Oct 16". */
export function sinceText(ts: number, now = Date.now()): string {
  const d = new Date(ts * 1000);
  const today = new Date(now);
  const same = d.toDateString() === today.toDateString();
  return 'Since ' + (same
    ? d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
    : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }));
}

export const BROWSE_URL = '/api/settings/library-paths/browse';
export interface Folder { name: string; path: string }
export type Browse = { ok: true; path: string | null; parent: string | null; dirs: Folder[] } | { ok: false; error: string };

/** Folders for the picker: no path lists the allowed roots. */
export async function browseFolders(path: string | null): Promise<Browse> {
  try {
    const res = await apiFetch(path ? `${BROWSE_URL}?path=${encodeURIComponent(path)}` : BROWSE_URL);
    const data = await res.json().catch(() => ({})) as { path?: string | null; parent?: string | null; dirs?: Folder[]; error?: string };
    if (res.ok && Array.isArray(data.dirs)) return { ok: true, path: data.path ?? null, parent: data.parent ?? null, dirs: data.dirs };
    return { ok: false, error: data.error || SAVE_FAILED };
  } catch {
    return { ok: false, error: UNREACHABLE };
  }
}
