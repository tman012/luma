/** Settings > Connectors > Telegram (owner only): Bot Token, Chat ID, Test and Save.
 * Token masked last4; never logged. No helper text. */
import { useEffect, useRef, useState } from 'react';
import type React from 'react';
import { apiFetch } from '@/apiClient';
import { FONT } from '@/components/start/StartUI';
import { T } from '@/theme/tokens';
import { LUMA_BLUE } from '@/ui/lumaButton';

const API = '/api/settings/connectors/telegram';
const MASK = '********';
const UNREACHABLE = "Couldn't reach Luma";

type View = { bot_token: { set: boolean; last4: string }; chat_id: string };
type Fields = Partial<Record<'bot_token' | 'chat_id', string>>;
interface Answer { ok?: boolean; message?: string; error?: string; fields?: Fields }

async function call<T>(url: string, init?: RequestInit): Promise<{ ok: boolean; status: number; data: T }> {
  try {
    const res = await apiFetch(url, init);
    return { ok: res.ok, status: res.status, data: (await res.json().catch(() => ({}))) as T };
  } catch {
    return { ok: false, status: 0, data: { error: UNREACHABLE } as T };
  }
}
const send = <T,>(url: string, method: string, body: unknown) =>
  call<T>(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export function TelegramConnector({ onSaved }: { onSaved: (text: string) => void }) {
  const [view, setView] = useState<View | null>(null);
  useEffect(() => {
    let alive = true;
    call<View>(API).then(r => { if (alive && r.ok) setView(r.data); });
    return () => { alive = false; };
  }, []);
  if (!view) return null;
  return <TelegramForm key={JSON.stringify(view)} view={view} onSaved={(v, removed) => {
    setView(v);
    onSaved(removed ? 'Removed' : 'Saved');
  }} />;
}

function TelegramForm({ view, onSaved }: {
  view: View; onSaved: (v: View, removed: boolean) => void;
}) {
  const [token, setToken] = useState('');
  const [chat, setChat] = useState(view.chat_id || '');
  const [show, setShow] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<Fields>({});
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  useEffect(() => {
    if (!result) return;
    const t = window.setTimeout(() => setResult(null), 4000);
    return () => window.clearTimeout(t);
  }, [result]);

  const saved = view.bot_token.set;
  const dirty = !!token.trim() || chat.trim() !== (view.chat_id || '');
  const canSave = (!!token.trim() || (saved && chat.trim() !== (view.chat_id || ''))) && dirty && !saving;
  const keepsToken = saved && !token.trim();

  const body = () => ({
    bot_token: token.trim() || (keepsToken ? MASK : ''),
    chat_id: chat.trim(),
  });

  const edit = (field: keyof Fields, set: (v: string) => void) => (e: React.ChangeEvent<HTMLInputElement>) => {
    set(e.target.value);
    setConfirmRemove(false);
    setErrors(er => { const n = { ...er }; delete n[field]; return n; });
    setResult(null);
  };

  const test = async () => {
    setTesting(true);
    setResult(null);
    const r = await send<Answer>(`${API}/test`, 'POST', body());
    if (!alive.current) return;
    setTesting(false);
    if (r.data.fields) setErrors(r.data.fields);
    setResult(r.data.ok
      ? { ok: true, text: r.data.message || 'Connected' }
      : { ok: false, text: r.data.error || (r.status === 0 ? UNREACHABLE : 'Unexpected response') });
  };

  const save = async (remove = false) => {
    setSaving(true);
    setResult(null);
    const payload = remove ? { bot_token: '', chat_id: '' } : body();
    if (!remove && !canSave) {
      if (!token.trim() && !saved) setErrors(e => ({ ...e, bot_token: 'Required' }));
      setSaving(false);
      return;
    }
    const r = await send<View & Answer>(API, 'PUT', payload);
    if (!alive.current) return;
    setSaving(false);
    setConfirmRemove(false);
    if (r.ok && r.data.bot_token) {
      setToken('');
      setShow(false);
      onSaved({ bot_token: r.data.bot_token, chat_id: r.data.chat_id || '' }, remove);
      return;
    }
    if (r.data.fields) setErrors(r.data.fields);
    else setResult({ ok: false, text: r.data.error || (r.status === 0 ? UNREACHABLE : "Couldn't save") });
  };

  const tokenPlaceholder = keepsToken
    ? (view.bot_token.last4 ? `••••${view.bot_token.last4}` : '••••••••')
    : '';

  return (
    <div data-testid="connector-telegram">
      <h2 style={{ ...groupHeader, display: 'flex', alignItems: 'center', gap: 8 }}>
        Telegram<span style={dot(saved)} aria-hidden="true" data-testid="telegram-status" />
      </h2>
      <form style={card} noValidate onSubmit={e => { e.preventDefault(); save(); }}>
        <div>
          <label htmlFor="tg-bot-token" style={labelStyle}>Bot Token</label>
          <div style={{ position: 'relative' }}>
            <input
              id="tg-bot-token" data-testid="telegram-bot-token" data-secret="true"
              style={{ ...inputStyle, paddingRight: 64, ...(errors.bot_token ? invalid : null) }}
              type={show ? 'text' : 'password'} autoComplete="new-password" spellCheck={false}
              aria-invalid={errors.bot_token ? true : undefined}
              placeholder={tokenPlaceholder}
              value={token} onChange={edit('bot_token', setToken)}
            />
            <button type="button" style={eye} aria-label={show ? 'Hide' : 'Show'} aria-pressed={show}
              onClick={() => setShow(s => !s)}>{show ? 'Hide' : 'Show'}</button>
          </div>
          {errors.bot_token && (
            <span data-testid="telegram-bot-token-error" style={fieldError}>{errors.bot_token}</span>
          )}
        </div>
        <div>
          <label htmlFor="tg-chat-id" style={labelStyle}>Chat ID</label>
          <input
            id="tg-chat-id" data-testid="telegram-chat-id"
            style={{ ...inputStyle, ...(errors.chat_id ? invalid : null) }}
            type="text" inputMode="numeric" autoComplete="off" spellCheck={false}
            value={chat} onChange={edit('chat_id', setChat)}
          />
          {errors.chat_id && (
            <span data-testid="telegram-chat-id-error" style={fieldError}>{errors.chat_id}</span>
          )}
        </div>
        <div style={actions}>
          <button type="button" style={ghostBtn} disabled={testing || (!token.trim() && !saved)}
            aria-busy={testing} onClick={test} data-testid="telegram-test">
            {testing ? 'Testing…' : 'Test'}
          </button>
          <button type="submit" style={{ ...primary, opacity: canSave ? 1 : 0.4 }}
            disabled={!canSave} data-testid="telegram-save">Save</button>
          {saved && !dirty && (
            <button type="button" style={dangerBtn} onClick={() => setConfirmRemove(true)} data-testid="telegram-remove">
              Remove
            </button>
          )}
          <span role="status" aria-live="polite" data-testid="telegram-result"
            style={{ fontSize: 13, minWidth: 0, overflowWrap: 'anywhere',
              color: result ? (result.ok ? T.success : T.dangerText) : 'transparent' }}>
            {result?.text}
          </span>
        </div>
      </form>
      {confirmRemove && saved && !dirty && (
        <div role="dialog" aria-modal="true" className="luma-bleed" style={dialogBack}
          onClick={() => setConfirmRemove(false)}>
          <div style={dialog} onClick={e => e.stopPropagation()}>
            <p style={{ margin: '0 0 20px', fontSize: 17, fontWeight: 600 }}>Remove Telegram?</p>
            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
              <button type="button" style={ghostBtn} onClick={() => setConfirmRemove(false)}>Cancel</button>
              <button type="button" style={{ ...primary, background: '#ef4444' }}
                onClick={() => save(true)} data-testid="telegram-remove-confirm">Remove</button>
            </div>
          </div>
        </div>
      )}
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
const invalid: React.CSSProperties = { borderColor: T.dangerText };
const fieldError: React.CSSProperties = {
  display: 'block', marginTop: 6, fontSize: 12, color: T.dangerText, fontFamily: FONT,
};
const eye: React.CSSProperties = {
  position: 'absolute', right: 6, top: 6, height: 32, padding: '0 10px', borderRadius: 8, border: 'none',
  background: 'none', color: 'rgba(255,255,255,0.7)', fontSize: 13, fontFamily: FONT, cursor: 'pointer',
};
const actions: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 12, marginTop: 2, flexWrap: 'wrap' };
const primary: React.CSSProperties = {
  minHeight: 44, padding: '0 22px', borderRadius: 10, border: 'none', background: LUMA_BLUE, color: '#fff',
  fontSize: 15, fontWeight: 600, fontFamily: FONT, cursor: 'pointer',
};
const ghostBtn: React.CSSProperties = {
  minHeight: 44, padding: '0 18px', borderRadius: 10, border: '1px solid rgba(255,255,255,0.12)',
  background: 'rgba(255,255,255,0.06)', color: 'rgba(255,255,255,0.95)', fontSize: 15, fontFamily: FONT, cursor: 'pointer',
};
const dangerBtn: React.CSSProperties = {
  minHeight: 44, padding: '0 8px', border: 'none', background: 'none', color: '#ef4444', fontSize: 15,
  fontFamily: FONT, cursor: 'pointer', marginLeft: 'auto',
};
const dialogBack: React.CSSProperties = {
  position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.5)', display: 'flex',
  alignItems: 'center', justifyContent: 'center', padding: 20,
};
const dialog: React.CSSProperties = {
  width: '100%', maxWidth: 400, borderRadius: 20, padding: 24, background: '#15102a', color: '#fff',
  border: '1px solid rgba(255,255,255,0.08)', fontFamily: FONT,
};
