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
  [field: string]: unknown;
}
export type Payload = Record<string, unknown>;

/** What a call came back with: ok, the HTTP status (0 = unreachable) and the JSON body. */
export interface Result<T> { ok: boolean; status: number; data: T }
export interface ErrorBody { error?: string; fields?: Record<string, string> }
export interface TestBody { ok?: boolean; message?: string; error?: string; fields?: Record<string, string> }

export const UNREACHABLE = "Couldn't reach Luma";

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
