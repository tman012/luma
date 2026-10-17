/** Library paths picked for a section (TV now; Radarr-style Movies later),
 * app/library_selection on the server: GET <url> lists every Library path
 * with `selected`; PUT <url> {selected: [ids]} saves the choice. */
import { apiFetch } from '@/apiClient';
import { SAVE_FAILED, UNREACHABLE, type LibPath } from './libraryPathsApi';

export interface SelectableLibrary extends LibPath { selected: boolean }
export interface SelectionView { libraries: SelectableLibrary[]; selected: string[] }
export type SelectionResult =
  | { ok: true; data: SelectionView }
  | { ok: false; error: string; signIn?: boolean; status?: number };

const isView = (d: unknown): d is SelectionView =>
  !!d && Array.isArray((d as SelectionView).libraries) && Array.isArray((d as SelectionView).selected);

async function call(url: string, init?: RequestInit): Promise<SelectionResult> {
  try {
    const res = await apiFetch(url, init);
    const data = await res.json().catch(() => ({})) as { error?: string; errors?: Record<string, string>; sign_in?: boolean };
    if (res.ok && isView(data)) return { ok: true, data };
    if (res.status === 401 && data.sign_in === true) return { ok: false, error: data.error || SAVE_FAILED, signIn: true, status: 401 };
    return { ok: false, error: data.errors?.selected || data.error || SAVE_FAILED, status: res.status };
  } catch {
    return { ok: false, error: UNREACHABLE };
  }
}

export const loadSelection = (url: string) => call(url);
export const saveSelection = (url: string, selected: string[]) =>
  call(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ selected }) });

/** The ids after tapping *id* (Library paths order is kept by the server). */
export const toggled = (selected: string[], id: string) =>
  (selected.includes(id) ? selected.filter(x => x !== id) : [...selected, id]);
