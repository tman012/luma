/** Form state for adding or editing a tracker, driven by the type's field
 * schema. Pure functions, so the rules are testable on their own.
 *
 * Secrets: a stored secret is never in the form. Its input starts empty with
 * a masked placeholder; leaving it alone keeps the stored value. A stored
 * secret belongs to the saved URL, so changing the URL drops it (the server
 * does the same) and a required one must be entered again. */
import { SECRET_MASK, type Payload, type Tracker, type TrackerField, type TrackerType } from './trackersApi';

export const REQUIRED = 'Required';
export const INVALID_URL = 'Invalid URL';
export const INVALID = 'Invalid';
export const TOO_LONG = 'Too long';
export const UNREADABLE = 'Credentials unreadable';
export const NAME_MAX = 60;
export const VALUE_MAX = 4096;
export const PRIORITY_MIN = 1;
export const PRIORITY_MAX = 50;
export const GENERIC_TYPES = ['torznab', 'newznab'];

export interface SecretInput { value: string; touched: boolean }
export interface FormState {
  enabled: boolean;
  values: Record<string, string>;        // text, url and number fields as typed
  categories: number[];
  secrets: Record<string, SecretInput>;
}
export type Errors = Record<string, string>;

const isGeneric = (type: TrackerType) => GENERIC_TYPES.includes(type.id);

/** The URL as the server stores it: trimmed, no trailing slash. */
export function normalizeUrl(raw: string): string {
  return raw.trim().replace(/\/+$/, '');
}

export function initialForm(type: TrackerType, tracker?: Tracker | null): FormState {
  const values: Record<string, string> = {};
  const secrets: Record<string, SecretInput> = {};
  let categories: number[] = [];
  for (const f of type.fields) {
    if (f.kind === 'categories') {
      const src = tracker ? tracker.categories : Array.isArray(f.default) ? f.default : [];
      const allowed = new Set(type.categories.map(c => c.id));
      categories = src.filter(c => allowed.has(c));
    } else if (f.secret) {
      secrets[f.key] = { value: '', touched: false };   // never the stored value
    } else if (tracker) {
      const v = tracker[f.key];
      values[f.key] = v === null || v === undefined ? '' : String(v);
    } else if (f.key === 'name') {
      values[f.key] = isGeneric(type) ? '' : type.name;
    } else {
      values[f.key] = f.default === null || f.default === undefined || Array.isArray(f.default) ? '' : String(f.default);
    }
  }
  return { enabled: tracker ? tracker.enabled : true, values, categories, secrets };
}

/** Secrets the server holds for this tracker and will keep: stored, readable
 * and still bound to the URL in the form. */
export function keptSecrets(type: TrackerType, form: FormState, tracker?: Tracker | null): Set<string> {
  const out = new Set<string>();
  if (!tracker) return out;
  if (normalizeUrl(form.values.url ?? '') !== tracker.url) return out;
  if (tracker.status?.state === 'error' && tracker.status.message === UNREADABLE) return out;
  for (const f of type.fields) if (f.secret && tracker[f.key] === SECRET_MASK) out.add(f.key);
  return out;
}

export function checkUrl(raw: string, type: TrackerType): string | null {
  const s = raw.trim();
  if (!s) return REQUIRED;
  if (/\s/.test(s) || s.length > 2048) return INVALID_URL;
  let u: URL;
  try { u = new URL(s); } catch { return INVALID_URL; }
  const scheme = u.protocol.replace(':', '').toLowerCase();
  if (scheme !== 'http' && scheme !== 'https') return INVALID_URL;
  if (!u.hostname || u.username || u.password || u.search || u.hash || s.includes('@')) return INVALID_URL;
  if (/[?#]/.test(s)) return INVALID_URL;
  if (!isGeneric(type) && scheme !== 'https') return INVALID_URL;
  return null;
}

export function checkPriority(raw: string): string | null {
  const s = raw.trim();
  if (!s) return REQUIRED;
  if (!/^\d+$/.test(s)) return INVALID;
  const n = Number(s);
  return n >= PRIORITY_MIN && n <= PRIORITY_MAX ? null : INVALID;
}

export function validate(type: TrackerType, form: FormState, tracker?: Tracker | null): Errors {
  const errors: Errors = {};
  const kept = keptSecrets(type, form, tracker);
  for (const f of type.fields) {
    const err = fieldError(f, type, form, kept);
    if (err) errors[f.key] = err;
  }
  return errors;
}

function fieldError(f: TrackerField, type: TrackerType, form: FormState, kept: Set<string>): string | null {
  if (f.kind === 'categories') return form.categories.length ? null : REQUIRED;
  if (f.secret) {
    const v = (form.secrets[f.key]?.value ?? '').trim();
    if (!v) return f.required && !kept.has(f.key) ? REQUIRED : null;
    return v.length > VALUE_MAX ? TOO_LONG : null;
  }
  const v = (form.values[f.key] ?? '').trim();
  if (f.kind === 'url') return checkUrl(v, type);
  if (f.kind === 'number') return f.key === 'priority' ? checkPriority(v) : (!v && f.required ? REQUIRED : null);
  if (!v) return f.required ? REQUIRED : null;
  if (f.key === 'name' && v.replace(/\s+/g, ' ').length > NAME_MAX) return TOO_LONG;
  return v.length > VALUE_MAX ? TOO_LONG : null;
}

/** The body for Save or Test. A secret is sent only when typed; an optional
 * stored one cleared on purpose is sent as ''; otherwise it is left out, which
 * keeps the stored value. */
export function toPayload(type: TrackerType, form: FormState, tracker?: Tracker | null): Payload {
  const p: Payload = { type: type.id, enabled: form.enabled };
  const kept = keptSecrets(type, form, tracker);
  for (const f of type.fields) {
    if (f.kind === 'categories') { p[f.key] = [...form.categories].sort((a, b) => a - b); continue; }
    if (f.secret) {
      const s = form.secrets[f.key];
      const v = (s?.value ?? '').trim();
      if (v) p[f.key] = v;
      else if (s?.touched && !f.required && kept.has(f.key)) p[f.key] = '';
      continue;
    }
    const v = (form.values[f.key] ?? '').trim();
    if (f.kind === 'number') p[f.key] = /^\d+$/.test(v) ? Number(v) : v;
    else if (f.kind === 'url') p[f.key] = normalizeUrl(v);
    else p[f.key] = f.key === 'name' ? v.replace(/\s+/g, ' ') : v;
  }
  return p;
}

/** True once Save would send something other than what the form opened with. */
export function isDirty(type: TrackerType, form: FormState, start: FormState, tracker?: Tracker | null): boolean {
  return stable(toPayload(type, form, tracker)) !== stable(toPayload(type, start, tracker));
}

function stable(p: Payload): string {
  return JSON.stringify(Object.keys(p).sort().map(k => [k, p[k]]));
}

/** Server field errors keyed by field; anything else is a form-level error. */
export function splitServerErrors(type: TrackerType, fields: Record<string, string> | undefined): { fields: Errors; form: string } {
  const keys = new Set(type.fields.map(f => f.key));
  const out: Errors = {};
  let form = '';
  for (const [k, v] of Object.entries(fields || {})) {
    if (keys.has(k)) out[k] = v;
    else if (!form) form = v;
  }
  return { fields: out, form };
}
