/**
 * SecurityTab — when Luma asks for a PIN (Sonarr/Radarr v4 style
 * "Authentication Required" choice), the PIN itself, trusted proxies, host
 * names and account requests.
 */
import { useEffect, useState } from 'react';
import { ACCESS_CHOICES, type AuthRequired } from '@/pages/StartPage';
import { cn } from '@/utils/cn';
import { apiFetch } from '../../apiClient';
import { INPUT_CLASS, PRIMARY_BTN, SECONDARY_BTN } from './components/legacyTokens';
import { couldntLoad } from '@/utils/messages';
import { fetchRadarrProfiles, fetchSonarrProfiles, type QualityProfile } from '@/services/settingsApi';
import {
  describeRequest, recentRequests, requestLink, requestTime, type RequestLogEntry,
} from './requestPortalLog';

type Mode = AuthRequired;

interface SecurityState {
  auth_required: Mode;
  pin_set: boolean;
  single_account: boolean;
  local_request: boolean;
  trusted_proxies: string;
  hostnames: string;
  allow_signup: boolean;
}

const digits = (v: string) => v.replace(/\D/g, '').slice(0, 4);

/** Profile names from Radarr or Sonarr; [] when it's unreachable or answers something unexpected. */
async function loadProfileNames(fetcher: () => Promise<QualityProfile[]>): Promise<string[]> {
  try {
    const list = await fetcher();
    if (!Array.isArray(list)) return [];
    return [...new Set(list.map(p => (p && typeof p.name === 'string' ? p.name : '')).filter(Boolean))];
  } catch {
    return [];
  }
}

/** A quality profile picked by name. The saved value stays selectable when Radarr/Sonarr doesn't list it
 * (or can't be reached); '' is the app's default. */
export function ProfileSelect({ value, profiles, onChange, label }: {
  value: string; profiles: string[] | null; onChange: (v: string) => void; label: string;
}) {
  const names = profiles ?? [];
  const options = value && !names.includes(value) ? [value, ...names] : names;
  return (
    <select aria-label={label} value={value} onChange={e => onChange(e.target.value)}
      className={cn(INPUT_CLASS, 'cursor-pointer')}>
      <option value="" className="bg-kiosk-black text-white">Default</option>
      {options.map(n => <option key={n} value={n} className="bg-kiosk-black text-white">{n}</option>)}
    </select>
  );
}

function RequestPortalCard() {
  const [enabled, setEnabled] = useState(false);
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [hostname, setHostname] = useState('');
  const [movieProfile, setMovieProfile] = useState('');
  const [tvProfile, setTvProfile] = useState('');
  const [excludeLibraries, setExcludeLibraries] = useState('');
  // v176 (Designer): the profiles come from Radarr and Sonarr (names, as saved); null while unknown
  const [radarrProfiles, setRadarrProfiles] = useState<string[] | null>(null);
  const [sonarrProfiles, setSonarrProfiles] = useState<string[] | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [publicUrl, setPublicUrl] = useState('');
  const [log, setLog] = useState<RequestLogEntry[]>([]);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    apiFetch('/api/request/admin/settings')
      .then(r => r.json())
      .then(data => {
        setPublicUrl(data.public_url || '');
        setEnabled(data.enabled || false);
        setHostname(data.hostname || '');
        setMovieProfile(data.movie_profile || '');
        setTvProfile(data.tv_profile || '');
        const libs = Array.isArray(data.exclude_libraries) ? data.exclude_libraries : [];
        setExcludeLibraries(libs.join(', '));
      })
      .catch(() => {});
    loadProfileNames(fetchRadarrProfiles).then(setRadarrProfiles);
    loadProfileNames(fetchSonarrProfiles).then(setSonarrProfiles);
    apiFetch('/api/request/admin/log')
      .then(r => (r.ok ? r.json() : []))
      .then(data => setLog(recentRequests(data)))
      .catch(() => {});
  }, []);

  const link = requestLink(publicUrl, window.location.origin);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard unavailable */ }
  };

  const save = async () => {
    setMsg(null);
    if (enabled && password) {
      if (password.length < 8) {
        setMsg({ ok: false, text: 'Password must be at least 8 characters' });
        return;
      }
      if (password !== confirmPassword) {
        setMsg({ ok: false, text: "Passwords don't match" });
        return;
      }
    }
    setBusy(true);
    try {
      const body: Record<string, any> = {
        enabled,
        hostname,
        movie_profile: movieProfile,
        tv_profile: tvProfile,
        exclude_libraries: excludeLibraries.split(',').map(s => s.trim()).filter(Boolean),
      };
      if (password) body.password = password;
      
      const res = await apiFetch('/api/request/admin/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setMsg({ ok: false, text: data.error || 'Save failed' });
        return;
      }
      setPassword('');
      setConfirmPassword('');
      setDirty(false);
      setMsg({ ok: true, text: 'Saved' });
    } finally {
      setBusy(false);
    }
  };


  return (
    <section className="rounded-[16px] border border-white/[0.08] bg-white/[0.06] p-5 space-y-5">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-[15px] font-semibold text-white/90">Request portal</h3>
        <label className="relative inline-flex cursor-pointer items-center">
          <input
            type="checkbox"
            checked={enabled}
            onChange={e => { setEnabled(e.target.checked); setDirty(true); }}
            className="peer sr-only"
          />
          <div className="peer h-6 w-11 rounded-full bg-white/10 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all peer-checked:bg-[#3b82f6] peer-checked:after:translate-x-5" />
        </label>
      </div>

      {enabled && (
        <>
          <div className="grid gap-3 max-w-md">
            <div className="space-y-1.5">
              <span className="block text-[13px] font-medium text-white/70">Link</span>
              <div className="flex gap-2">
                <input
                  type="text"
                  readOnly
                  value={link}
                  onFocus={e => e.currentTarget.select()}
                  className="w-full rounded-lg border border-white/10 bg-white/[0.05] px-3 py-2 font-mono text-[13px] text-white/70 outline-none"
                />
                <button
                  type="button"
                  onClick={copy}
                  className="shrink-0 rounded-lg border border-white/10 bg-white/[0.06] px-4 text-[13px] font-semibold text-white/70 hover:bg-white/10"
                >
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </div>
            </div>
            <label className="space-y-1.5">
              <span className="block text-[13px] font-medium text-white/70">Password</span>
              <input
                type="password"
                value={password}
                onChange={e => { setPassword(e.target.value); setDirty(true); }}
                className={INPUT_CLASS}
              />
            </label>
            <label className="space-y-1.5">
              <span className="block text-[13px] font-medium text-white/70">Confirm password</span>
              <input
                type="password"
                value={confirmPassword}
                onChange={e => { setConfirmPassword(e.target.value); setDirty(true); }}
                className={INPUT_CLASS}
              />
            </label>
            <label className="space-y-1.5">
              <span className="block text-[13px] font-medium text-white/70">Hostname</span>
              <input
                type="text"
                value={hostname}
                onChange={e => { setHostname(e.target.value); setDirty(true); }}
                placeholder="requests.example.com"
                spellCheck={false}
                className={INPUT_CLASS}
              />
            </label>
            <label className="space-y-1.5">
              <span className="block text-[13px] font-medium text-white/70">Movie profile</span>
              <ProfileSelect value={movieProfile} profiles={radarrProfiles} label="Movie profile"
                onChange={v => { setMovieProfile(v); setDirty(true); }} />
            </label>
            <label className="space-y-1.5">
              <span className="block text-[13px] font-medium text-white/70">TV profile</span>
              <ProfileSelect value={tvProfile} profiles={sonarrProfiles} label="TV profile"
                onChange={v => { setTvProfile(v); setDirty(true); }} />
            </label>
            <label className="space-y-1.5">
              <span className="block text-[13px] font-medium text-white/70">Exclude libraries</span>
              <input
                type="text"
                value={excludeLibraries}
                onChange={e => { setExcludeLibraries(e.target.value); setDirty(true); }}
                spellCheck={false}
                className={INPUT_CLASS}
              />
            </label>
          </div>
        </>
      )}

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={save}
          disabled={busy || !dirty}
          className={PRIMARY_BTN}
        >
          Save
        </button>
        {msg && <span className={cn('text-[13px]', msg.ok ? 'text-emerald-300' : 'text-red-300')}>{msg.text}</span>}
      </div>

      {log.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-[13px] font-semibold text-white/70">Recent requests</h4>
          <div className="divide-y divide-white/[0.06]">
            {log.map(e => (
              <div key={e.id} className="flex items-center justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <div className="truncate text-[14px] text-white/85">{describeRequest(e)}</div>
                  <div className="text-[12px] text-white/40">{e.who}</div>
                </div>
                <div className="shrink-0 text-[12px] text-white/40">{requestTime(e.ts)}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

export function SecurityTab() {
  const [state, setState] = useState<SecurityState | null>(null);
  const [mode, setMode] = useState<Mode>('disabled_local');
  const [pin, setPin] = useState('');
  const [confirm, setConfirm] = useState('');
  const [proxies, setProxies] = useState('');
  const [hostnames, setHostnames] = useState('');
  const [signup, setSignup] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  // v175 (Designer must-check): a failed load used to leave the whole tab blank (return null, the
  // Request portal card included). Now the portal card always shows and the PIN card says it
  // couldn't load, with Try again.
  const [loadFailed, setLoadFailed] = useState(false);
  const load = async () => {
    setLoadFailed(false);
    try {
      const res = await apiFetch('/api/security');
      if (!res.ok) { setLoadFailed(true); return; }
      const data: SecurityState = await res.json();
      setState(data);
      setMode(data.auth_required);
      setProxies(data.trusted_proxies || '');
      setHostnames(data.hostnames || '');
      setSignup(!!data.allow_signup);
    } catch {
      setLoadFailed(true);
    }
  };
  useEffect(() => { load(); }, []);

  if (!state) {
    return (
      <div className="space-y-6" data-testid="security-tab">
      <div className="settings-tab-header">
        <h2 className="text-xl font-semibold text-white/90">Security</h2>
      </div>
        <RequestPortalCard />
        {loadFailed && (
          <section data-testid="security-load-error" className="rounded-[16px] border border-white/[0.08] bg-white/[0.06] p-5">
            <h3 className="text-[15px] font-semibold text-white/95">Require PIN</h3>
            <p className="mt-3 text-[13px] text-white/50">{couldntLoad('security settings')}</p>
            <button type="button" className={`${SECONDARY_BTN} mt-4`} onClick={() => { void load(); }}>Try again</button>
          </section>
        )}
      </div>
    );
  }

  const pinNeeded = !state.pin_set;
  const dirty = mode !== state.auth_required || pin !== '' || proxies !== (state.trusted_proxies || '')
    || hostnames !== (state.hostnames || '') || signup !== !!state.allow_signup;

  const save = async () => {
    setMsg(null);
    if (pin || confirm || pinNeeded) {
      if (pin.length !== 4) { setMsg({ ok: false, text: 'Enter 4 digits' }); return; }
      if (pin !== confirm) { setMsg({ ok: false, text: "PINs don't match" }); return; }
    }
    setBusy(true);
    try {
      const body: Record<string, string | boolean> = {
        auth_required: mode, trusted_proxies: proxies, hostnames, allow_signup: signup,
      };
      if (pin) { body.pin = pin; body.confirm = confirm; }
      const res = await apiFetch('/api/security', {
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
    <div className="space-y-6" data-testid="security-tab">
      <div className="settings-tab-header">
        <h2 className="text-xl font-semibold text-white/90">Security</h2>
      </div>
      <RequestPortalCard />
      
      <section className="rounded-[16px] border border-white/[0.08] bg-white/[0.06] p-5 space-y-5">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-[15px] font-semibold text-white/90">Require PIN</h3>
          <span className={cn(
            'rounded-full px-2.5 py-0.5 text-[12px] font-semibold',
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
                c.value === 'disabled_local' ? 'flex-[2.2]' : 'flex-1',
                mode === c.value ? 'bg-white text-[#15102a] font-semibold shadow' : 'text-white/80 hover:text-white',
              )}
            >
              {c.label}
            </button>
          ))}
        </div>

        {(
          <div className="grid grid-cols-2 gap-3 max-w-sm">
            <label className="space-y-1.5">
              <span className="block text-[13px] font-medium text-white/70">{state.pin_set ? 'New PIN' : 'PIN'}</span>
              <input
                type="password" inputMode="numeric" autoComplete="new-password" maxLength={4}
                value={pin} onChange={e => setPin(digits(e.target.value))} placeholder="••••"
                className={`${INPUT_CLASS} tracking-[0.4em]`}
              />
            </label>
            <label className="space-y-1.5">
              <span className="block text-[13px] font-medium text-white/70">Confirm</span>
              <input
                type="password" inputMode="numeric" autoComplete="new-password" maxLength={4}
                value={confirm} onChange={e => setConfirm(digits(e.target.value))} placeholder="••••"
                className={`${INPUT_CLASS} tracking-[0.4em]`}
              />
            </label>
          </div>
        )}

        <label className="block space-y-1.5">
          <span className="block text-[13px] font-medium text-white/70">Trusted proxies</span>
          <input
            type="text" value={proxies} onChange={e => setProxies(e.target.value)} placeholder="Addresses or ranges"
            spellCheck={false}
            className={`${INPUT_CLASS} max-w-md`}
          />
        </label>

        <label className="block space-y-1.5">
          <span className="block text-[13px] font-medium text-white/70">Host names</span>
          <input
            type="text" value={hostnames} onChange={e => setHostnames(e.target.value)} placeholder="Names"
            spellCheck={false} autoCapitalize="off"
            className={`${INPUT_CLASS} max-w-md`}
          />
        </label>

        <div className="flex items-center justify-between gap-3 max-w-md">
          <span className="text-[14px] font-medium text-white/70">Allow sign-up from anywhere</span>
          <button
            type="button" role="switch" aria-checked={signup} aria-label="Allow sign-up from anywhere"
            onClick={() => { setSignup(v => !v); setMsg(null); }}
            className={cn(
              'relative h-[28px] w-[50px] shrink-0 rounded-full transition-colors duration-200',
              signup ? 'bg-[#3b82f6]' : 'bg-white/20',
            )}
          >
            <span className={cn(
              'absolute top-[4px] left-[4px] h-[20px] w-[20px] rounded-full bg-white shadow-sm transition-transform duration-200',
              signup && 'translate-x-[22px]',
            )} />
          </button>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button" onClick={save} disabled={busy || !dirty}
            className={PRIMARY_BTN}
          >
            Save
          </button>
          {msg && <span className={cn('text-[13px]', msg.ok ? 'text-emerald-300' : 'text-red-300')}>{msg.text}</span>}
        </div>
      </section>
    </div>
  );
}
