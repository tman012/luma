/** Client for the owner-only tracker API (app/tracker_routes.py).
 * Secrets never come back from the server: a stored one reads as SECRET_MASK,
 * and sending SECRET_MASK (or leaving the key out) keeps it. */
import { apiFetch } from '@/apiClient';

export const SECRET_MASK = '********';
export const BASE_API = '/api/trackers';

export type FieldKind = 'text' | 'url' | 'secret' | 'number' | 'categories';
export interface TrackerField {
  key: string; label: string; kind: FieldKind; required: boolean; secret: boolean;
  default: string | number | number[] | null;
}
export interface TrackerType {
  id: string; name: string; protocol: string;
  fields: TrackerField[]; categories: { id: number; name: string }[];
}
export type TrackerState = 'ok' | 'error' | 'untested';
export interface Tracker {
  id: number; type: string; type_name: string; protocol: string; name: string; enabled: boolean;
  url: string; categories: number[]; priority: number;
  status: { state: TrackerState; message: string; checked_at: number | null };
  /** v170: imported from Prowlarr and no Test has passed yet: it can't be enabled. */
  test_required?: boolean;
  /** Required credentials not saved yet (keys). */
  missing?: string[];
  imported?: boolean;
  [field: string]: unknown;
}
export type Payload = Record<string, unknown>;

/** What a call came back with: ok, the HTTP status (0 = unreachable) and the JSON body. */
export interface Result<T> { ok: boolean; status: number; data: T }
export interface ErrorBody { error?: string; fields?: Record<string, string>; sign_in?: boolean }
export interface TestBody { ok?: boolean; message?: string; error?: string; fields?: Record<string, string> }

export const UNREACHABLE = "Couldn't reach Luma";
export const SIGN_IN_TO_EDIT = 'Sign in to edit trackers';

/** A 401 that asks for sign-in (expired cookie, no session). Other 401s and
 * 403s mean this user can't use trackers at all. */
export const needsSignIn = (r: Result<ErrorBody>) => r.status === 401 && !!r.data.sign_in;

async function call<T>(url: string, init?: RequestInit): Promise<Result<T & ErrorBody>> {
  try {
    const res = await apiFetch(url, { cache: 'no-store', credentials: 'same-origin', ...init });
    const data = (await res.json().catch(() => ({}))) as T & ErrorBody;
    return { ok: res.ok, status: res.status, data: data && typeof data === 'object' ? data : ({} as T & ErrorBody) };
  } catch {
    return { ok: false, status: 0, data: { error: UNREACHABLE } as T & ErrorBody };
  }
}
const send = <T,>(url: string, method: string, body?: unknown) => call<T>(url, {
  method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
});

export const fetchTypes = () => call<{ types: TrackerType[] }>(`${BASE_API}/types`);
export const fetchTrackers = () => call<{ trackers: Tracker[] }>(BASE_API);
export const fetchTracker = (id: number) => call<{ tracker: Tracker }>(`${BASE_API}/${id}`);
export const createTracker = (p: Payload) => send<{ tracker: Tracker }>(BASE_API, 'POST', p);
export const updateTracker = (id: number, p: Payload) => send<{ tracker: Tracker }>(`${BASE_API}/${id}`, 'PUT', p);
export const setTrackerEnabled = (id: number, enabled: boolean) =>
  send<{ tracker: Tracker }>(`${BASE_API}/${id}/enabled`, 'PUT', { enabled });
export const deleteTracker = (id: number) => send<{ ok: boolean }>(`${BASE_API}/${id}`, 'DELETE');
export const testSavedTracker = (id: number) => send<TestBody>(`${BASE_API}/${id}/test`, 'POST');
export const testTrackerForm = (p: Payload) => send<TestBody>(`${BASE_API}/test`, 'POST', p);

// ---------------------------------------------------------------- Trackers > Search (v174)
// Owner only and validated on the server; the luma-test build has no /api/settings.
export const SEARCH_SETTINGS_API = `${BASE_API}/search-settings`;
export interface SearchSettingsBody { movie_categories: string; known_categories: number[] }
export const fetchSearchSettings = () => call<SearchSettingsBody>(SEARCH_SETTINGS_API);
export const saveSearchSettings = (p: { movie_categories: string }) => send<SearchSettingsBody>(SEARCH_SETTINGS_API, 'PUT', p);

// ---------------------------------------------------------------- Import from Prowlarr (v170)

export const IMPORT_API = `${BASE_API}/import/prowlarr`;
export type ImportState = 'ready' | 'needs_credentials' | 'exists' | 'unsupported' | 'invalid';
export interface ImportRow {
  key: number; name: string; type: string | null; type_name: string | null; state: ImportState; missing: string[];
}
export interface ImportPreview { preview_id: string; indexers: ImportRow[] }
export interface ImportResult { created: Tracker[]; skipped: { key: number; reason: string }[] }

export const fetchImportAvailable = () => call<{ available: boolean }>(IMPORT_API);
export const previewImport = () => send<ImportPreview>(`${IMPORT_API}/preview`, 'POST', {});
export const runImport = (previewId: string, keys: number[]) =>
  send<ImportResult>(IMPORT_API, 'POST', { preview_id: previewId, keys });

/** v171 (Designer): the type shown next to a tracker's name, or null when it just repeats the name
 *  (no 'BeyondHD BeyondHD'). Case and spacing don't count as a difference. */
export function secondaryLabel(name: string | null | undefined, typeName: string | null | undefined): string | null {
  const t = (typeName || '').trim();
  if (!t) return null;
  const norm = (x: string) => x.trim().replace(/\s+/g, ' ').toLowerCase();
  return norm(name || '') === norm(t) ? null : t;
}
