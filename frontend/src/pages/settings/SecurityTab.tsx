/**
 * SecurityTab — when Luma asks for a PIN (Sonarr/Radarr v4 style
 * "Authentication Required" choice), the PIN itself, and trusted proxies.
 */
import { useEffect, useState } from 'react';
import { ACCESS_CHOICES } from '@/pages/StartPage';
import { cn } from '@/utils/cn';

type Mode = 'pin_remote' | 'pin' | 'open';

interface SecurityState {
  mode: Mode;
  pin_set: boolean;
  single_account: boolean;
  local_request: boolean;
  trusted_proxies: string;
}

const digits = (v: string) => v.replace(/\D/g, '').slice(0, 4);

export function SecurityTab() {
  const [state, setState] = useState<SecurityState | null>(null);
  const [mode, setMode] = useState<Mode>('pin_remote');
  const [pin, setPin] = useState('');
  const [confirm, setConfirm] = useState('');
  const [proxies, setProxies] = useState('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const res = await fetch('/api/security');
    if (!res.ok) return;
    const data: SecurityState = await res.json();
    setState(data);
    setMode(data.mode);
    setProxies(data.trusted_proxies || '');
  };
  useEffect(() => { load(); }, []);

  if (!state) return null;

  const pinNeeded = mode !== 'open' && !state.pin_set;
  const dirty = mode !== state.mode || pin !== '' || proxies !== (state.trusted_proxies || '');

  const save = async () => {
    setMsg(null);
    if (pin || confirm || pinNeeded) {
      if (pin.length !== 4) { setMsg({ ok: false, text: 'Enter 4 digits' }); return; }
      if (pin !== confirm) { setMsg({ ok: false, text: "PINs don't match" }); return; }
    }
    setBusy(true);
    try {
      const body: Record<string, string> = { mode, trusted_proxies: proxies };
      if (pin) { body.pin = pin; body.confirm = confirm; }
      const res = await fetch('/api/security', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setMsg({ ok: false, text: data.error || 'Save failed' }); return; }
      setPin(''); setConfirm('');
      setMsg({ ok: true, text: 'Saved' });
      await load();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5 p-4 md:p-6">
      <section className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-5 space-y-5">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-[15px] font-semibold text-white/90">Require PIN</h3>
          <span className={cn(
            'rounded-full px-2.5 py-0.5 text-[11px] font-semibold',
            state.local_request ? 'bg-emerald-500/15 text-emerald-300' : 'bg-amber-500/15 text-amber-300',
          )}>
            This device: {state.local_request ? 'Local' : 'Remote'}
          </span>
        </div>

        <div role="radiogroup" aria-label="Require PIN" className="flex gap-0.5 rounded-xl bg-white/[0.08] p-[3px]">
          {ACCESS_CHOICES.map(c => (
            <button
              key={c.value}
              type="button"
              role="radio"
              aria-checked={mode === c.value}
              onClick={() => { setMode(c.value); setMsg(null); }}
              className={cn(
                'min-h-[34px] rounded-lg px-2 py-1.5 text-[13px] font-medium leading-tight transition-colors',
                c.value === 'pin_remote' ? 'flex-[2.2]' : 'flex-1',
                mode === c.value ? 'bg-white text-[#15102a] font-semibold shadow' : 'text-white/80 hover:text-white',
              )}
            >
              {c.label}
            </button>
          ))}
        </div>

        {mode !== 'open' && (
          <div className="grid grid-cols-2 gap-3 max-w-sm">
            <label className="space-y-1.5">
              <span className="text-[12px] font-medium text-white/50">{state.pin_set ? 'New PIN' : 'PIN'}</span>
              <input
                type="password" inputMode="numeric" autoComplete="new-password" maxLength={4}
                value={pin} onChange={e => setPin(digits(e.target.value))} placeholder="••••"
                className="w-full rounded-lg border border-white/10 bg-white/[0.05] px-3 py-2 text-[15px] tracking-[0.4em] text-white outline-none focus:border-white/30"
              />
            </label>
            <label className="space-y-1.5">
              <span className="text-[12px] font-medium text-white/50">Confirm</span>
              <input
                type="password" inputMode="numeric" autoComplete="new-password" maxLength={4}
                value={confirm} onChange={e => setConfirm(digits(e.target.value))} placeholder="••••"
                className="w-full rounded-lg border border-white/10 bg-white/[0.05] px-3 py-2 text-[15px] tracking-[0.4em] text-white outline-none focus:border-white/30"
              />
            </label>
          </div>
        )}

        <label className="block space-y-1.5">
          <span className="text-[12px] font-medium text-white/50">Trusted proxies</span>
          <input
            type="text" value={proxies} onChange={e => setProxies(e.target.value)} placeholder="Addresses or ranges"
            spellCheck={false}
            className="w-full max-w-md rounded-lg border border-white/10 bg-white/[0.05] px-3 py-2 text-[14px] text-white outline-none focus:border-white/30"
          />
        </label>

        <div className="flex items-center gap-3">
          <button
            type="button" onClick={save} disabled={busy || !dirty}
            className="rounded-full bg-white px-5 py-2 text-[14px] font-semibold text-[#15102a] disabled:opacity-40"
          >
            Save
          </button>
          {msg && <span className={cn('text-[13px]', msg.ok ? 'text-emerald-300' : 'text-red-300')}>{msg.text}</span>}
        </div>
      </section>
    </div>
  );
}
