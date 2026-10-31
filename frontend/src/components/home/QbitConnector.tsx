/** Settings > Connectors > qBittorrent (owner only): URL, username, password,
 * category, Test and Save. Pre-filled from the server's qbittorrent settings
 * (the section downloads use). The password never comes back: a saved one
 * shows only as dots and is sent again only when retyped. */
import { useEffect, useRef, useState } from 'react';
import type React from 'react';
import { apiFetch } from '@/apiClient';
import { FONT } from '@/components/start/StartUI';
import { T } from '@/theme/tokens';
import { Switch } from '@/components/trackers/trackersUi';

export const QBIT_API = '/api/settings/connectors/qbittorrent';
export const QBIT_UNREACHABLE = "Couldn't reach Luma";

export interface QbitView { url: string; username: string; category: string; password: { set: boolean }; add_paused?: boolean }
type Fields = Partial<Record<'url' | 'username' | 'password' | 'category', string>>;
interface Answer { ok?: boolean; message?: string; version?: string; error?: string; fields?: Fields }

async function call<T>(url: string, init?: RequestInit): Promise<{ ok: boolean; status: number; data: T }> {
  try {
    const res = await apiFetch(url, init);
    return { ok: res.ok, status: res.status, data: (await res.json().catch(() => ({}))) as T };
  } catch {
    return { ok: false, status: 0, data: { error: QBIT_UNREACHABLE } as T };
  }
}
const send = <T,>(url: string, method: string, body: unknown) =>
  call<T>(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

/** Normalized for the "changed" check: trimmed, no trailing slash. */
const normUrl = (u: string) => u.trim().replace(/\/+$/, '');

/** A partial reply (older server) still gives the form strings to work with. */
const withDefaults = (v: QbitView): QbitView => ({
  ...v, username: v.username ?? '', category: v.category ?? '', password: v.password ?? { set: false },
});

/** bare: inside the Connectors modal (the modal shows the title). */
export function QbitConnector({ onSaved, bare = false }: { onSaved: (text: string) => void; bare?: boolean }) {
  const [view, setView] = useState<QbitView | null>(null);
  useEffect(() => {
    let alive = true;
    call<QbitView>(QBIT_API).then(r => { if (alive && r.ok && r.data && typeof r.data.url === 'string') setView(withDefaults(r.data)); });
    return () => { alive = false; };
  }, []);
  if (!view) return null;
  return <QbitForm key={JSON.stringify(view)} bare={bare} view={view} onSaved={v => { setView(v); onSaved('Saved'); }} />;
}

function QbitForm({ view, onSaved, bare = false }: {
  bare?: boolean; view: QbitView; onSaved: (v: QbitView) => void }) {
  const [url, setUrl] = useState(view.url);
  const [username, setUsername] = useState(view.username);
  const [category, setCategory] = useState(view.category);
  const [password, setPassword] = useState('');
  const [paused, setPaused] = useState(!!view.add_paused);
  const [show, setShow] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [errors, setErrors] = useState<Fields>({});
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  // A success fades; a failure stays until the next edit or Test.
  useEffect(() => {
    if (!result?.ok) return;
    const t = window.setTimeout(() => setResult(null), 4000);
    return () => window.clearTimeout(t);
  }, [result]);

  const urlChanged = normUrl(url) !== normUrl(view.url);
  // A saved password belongs to the saved URL only.
  const keepsPassword = view.password.set && !urlChanged;
  const dirty = urlChanged || username.trim() !== view.username || category.trim() !== view.category || password !== ''
    || paused !== !!view.add_paused;
  const canSave = !!url.trim() && dirty && !saving;

  const edit = (key: keyof Fields, set: (v: string) => void) => (e: React.ChangeEvent<HTMLInputElement>) => {
    set(e.target.value);
    setErrors(x => { if (!x[key] && !(key === 'url' && x.password)) return x; const n = { ...x }; delete n[key]; if (key === 'url') delete n.password; return n; });
    setResult(r => (r && !r.ok ? null : r));
  };

  const body = () => ({
    url: url.trim(), username: username.trim(), category: category.trim(),
    ...(password !== '' ? { password } : null),
  });
  const saveBody = () => ({ ...body(), add_paused: paused });

  const test = async () => {
    if (testing) return;
    if (!url.trim()) { setErrors(e => ({ ...e, url: 'Required' })); return; }
    setTesting(true);
    setResult(null);
    const r = await send<Answer>(`${QBIT_API}/test`, 'POST', body());
    if (!alive.current) return;
    setTesting(false);
    if (r.data.fields) setErrors(r.data.fields);
    if (r.ok && r.data.ok) {
      setResult({ ok: true, text: r.data.version ? `Connected (${r.data.version})` : 'Connected' });
    } else {
      setResult({ ok: false, text: r.data.error || (r.status === 0 ? QBIT_UNREACHABLE : 'Unexpected response') });
    }
  };

  const save = async () => {
    if (!canSave) { if (!url.trim()) setErrors(e => ({ ...e, url: 'Required' })); return; }
    setSaving(true);
    const r = await send<QbitView & Answer>(QBIT_API, 'PUT', saveBody());
    if (!alive.current) return;
    setSaving(false);
    if (r.ok && typeof r.data.url === 'string') { onSaved(r.data); return; }
    if (r.data.fields) setErrors(r.data.fields);
    else setResult({ ok: false, text: r.data.error || (r.status === 0 ? QBIT_UNREACHABLE : "Couldn't save") });
  };

  const input = (id: keyof Fields, label: string, value: string, onChange: (e: React.ChangeEvent<HTMLInputElement>) => void,
    extra?: React.InputHTMLAttributes<HTMLInputElement>) => (
    <div>
      <label htmlFor={`qbit-${id}`} style={labelStyle}>{label}</label>
      <input id={`qbit-${id}`} data-testid={`qbit-${id}`} style={{ ...inputStyle, ...(errors[id] ? invalid : null) }}
        autoComplete="off" autoCapitalize="off" autoCorrect="off" spellCheck={false}
        aria-invalid={errors[id] ? true : undefined} aria-describedby={errors[id] ? `qbit-${id}-error` : undefined}
        value={value} onChange={onChange} {...extra} />
      {errors[id] && <span id={`qbit-${id}-error`} data-testid={`qbit-${id}-error`} style={fieldError}>{errors[id]}</span>}
    </div>
  );

  return (
    <div data-testid="connector-qbittorrent">
      {!bare && (
        <h2 style={{ ...groupHeader, display: 'flex', alignItems: 'center', gap: 8 }}>
          qBittorrent<span style={dot(!!view.url)} aria-hidden="true" data-testid="qbit-status" />
        </h2>
      )}
      <form style={card} noValidate onSubmit={e => { e.preventDefault(); save(); }}>
        {input('url', 'URL', url, edit('url', setUrl), { type: 'url', inputMode: 'url' })}
        {input('username', 'Username', username, edit('username', setUsername))}
        <div>
          <label htmlFor="qbit-password" style={labelStyle}>Password</label>
          <div style={{ position: 'relative' }}>
            <input id="qbit-password" data-testid="qbit-password" data-secret="true"
              style={{ ...inputStyle, paddingRight: 64, ...(errors.password ? invalid : null) }}
              type={show ? 'text' : 'password'} autoComplete="new-password" autoCapitalize="off" spellCheck={false}
              aria-invalid={errors.password ? true : undefined} aria-describedby={errors.password ? 'qbit-password-error' : undefined}
              placeholder={keepsPassword ? '••••••••' : ''} value={password} onChange={edit('password', setPassword)} />
            <button type="button" style={eye} aria-label={show ? 'Hide password' : 'Show password'} aria-pressed={show}
              onClick={() => setShow(s => !s)}>{show ? 'Hide' : 'Show'}</button>
          </div>
          {errors.password && <span id="qbit-password-error" data-testid="qbit-password-error" style={fieldError}>{errors.password}</span>}
        </div>
        {input('category', 'Category', category, edit('category', setCategory))}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', minHeight: 44 }} data-testid="qbit-paused-row">
          <span id="qbit-paused-label" style={{ ...labelStyle, margin: 0 }}>Add paused</span>
          <Switch small on={paused} label="Add paused" onChange={setPaused} />
        </div>
        <div style={actions}>
          <button type="button" style={ghostBtn} disabled={testing} aria-busy={testing} onClick={test} data-testid="qbit-test">
            {testing ? 'Testing…' : 'Test'}
          </button>
          <button type="submit" style={{ ...primary, opacity: canSave ? 1 : 0.4 }} disabled={!canSave} data-testid="qbit-save">Save</button>
          <span role="status" aria-live="polite" data-testid="qbit-result"
            style={{ fontSize: 13, minWidth: 0, overflowWrap: 'anywhere', color: result ? (result.ok ? T.success : T.dangerText) : 'transparent' }}>
            {result?.text}
          </span>
        </div>
      </form>
    </div>
  );
}

const groupHeader: React.CSSProperties = {
  margin: '0 4px 8px', fontSize: 13, fontWeight: 600, color: T.text3, fontFamily: FONT,
};
const card: React.CSSProperties = {
  borderRadius: 16, padding: 20, background: T.glass,
  border: `1px solid ${T.glass8}`, display: 'flex', flexDirection: 'column', gap: 16, fontFamily: FONT, margin: 0,
};
const labelStyle: React.CSSProperties = {
  display: 'block', margin: '0 0 6px', fontSize: 13, fontWeight: 500, color: T.text2, fontFamily: FONT,
};
const dot = (on: boolean): React.CSSProperties => ({
  width: 8, height: 8, borderRadius: 4, background: on ? T.success : T.text4,
});
const inputStyle: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', height: 44, padding: '0 14px', borderRadius: 10,
  background: T.glass, border: `1px solid ${T.glass12}`, color: T.white,
  fontSize: 16, fontFamily: FONT, outline: 'none',
};
const invalid: React.CSSProperties = { border: `1px solid ${T.danger}` };
const fieldError: React.CSSProperties = { display: 'block', marginTop: 6, fontSize: 13, color: T.dangerText };
const eye: React.CSSProperties = {
  position: 'absolute', right: 0, top: 0, height: 44, minWidth: 56, padding: '0 12px', borderRadius: 10, border: 'none',
  background: 'none', color: T.text2, fontSize: 13, fontFamily: FONT, cursor: 'pointer',
};
const actions: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 12, marginTop: 8, flexWrap: 'wrap' };
const primary: React.CSSProperties = {
  minHeight: 44, padding: '0 22px', borderRadius: 10, border: 'none', background: T.accent, color: T.white,
  fontSize: 15, fontWeight: 600, fontFamily: FONT, cursor: 'pointer',
};
const ghostBtn: React.CSSProperties = {
  minHeight: 44, padding: '0 18px', borderRadius: 10, border: `1px solid ${T.glass12}`,
  background: T.glass, color: T.text, fontSize: 15, fontFamily: FONT, cursor: 'pointer',
};
