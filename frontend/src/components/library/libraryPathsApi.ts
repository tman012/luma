/** Settings > Library paths API (owner only, luma-test builds). */
import { apiFetch } from '@/apiClient';
import { SAVE_FAILED } from '@/utils/messages';

export const LP_URL = '/api/settings/library-paths';
export const RULES_URL = '/api/settings/library-rules';
export const MAP_URL = '/api/settings/library-paths/mapping';

export type PathStatus = 'ok' | 'missing' | 'read_only';
export interface LibPath { id: string; name: string; path: string; status: PathStatus }
export type Unit = 'months' | 'years';
export interface Rule {
  id?: string; enabled: boolean; path_id: string;
  released_within: { n: number; unit: Unit } | null;
  resolution: string[]; source: string[];
}
export interface PathMapRow { remote: string; local: string }
export type Transfer = 'copy' | 'hardlink';
export interface LibraryView {
  paths: LibPath[]; rules: Rule[]; fallback_path_id: string | null; path_map: PathMapRow[]; transfer: Transfer;
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
export const saveMapping = (body: { path_map?: PathMapRow[]; transfer?: Transfer }) => send(MAP_URL, 'PUT', body);

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
