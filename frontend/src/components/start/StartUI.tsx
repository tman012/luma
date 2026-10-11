/**
 * Shared pieces for the full-screen Start and Unlock pages: navy backdrop,
 * centered column, pill buttons and the 4-digit keypad.
 */
import { useEffect } from 'react';

export const FONT = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

const css = `
.luma-start-fade{animation:lumaFade .35s ease both}
@keyframes lumaFade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.luma-shake{animation:lumaShake .42s cubic-bezier(.36,.07,.19,.97) both}
@keyframes lumaShake{10%,90%{transform:translateX(-2px)}20%,80%{transform:translateX(4px)}30%,50%,70%{transform:translateX(-8px)}40%,60%{transform:translateX(8px)}}
.luma-spin{animation:lumaSpin .9s linear infinite}
@keyframes lumaSpin{to{transform:rotate(360deg)}}
.luma-key:active{background:rgba(255,255,255,.28)!important}
.luma-start-btn{transition:transform .1s,opacity .2s}
.luma-start-btn:active{transform:scale(.98)}
.luma-start-btn:disabled{opacity:.6}
.luma-start-row:active{background:rgba(255,255,255,.14)!important}
`;

export function StartShell({ step, children }: { step: string; children: React.ReactNode }) {
  return (
    <div style={page} data-step={step}>
      <style>{css}</style>
      {children}
    </div>
  );
}

export function Center({ children }: { children: React.ReactNode }) {
  return <div className="luma-start-fade" style={center}>{children}</div>;
}
export function Bottom({ children }: { children: React.ReactNode }) {
  return <div style={bottom}>{children}</div>;
}
export function Title({ children }: { children: React.ReactNode }) {
  return <h1 style={title}>{children}</h1>;
}
export function Sub({ children }: { children: React.ReactNode }) {
  return <p style={sub}>{children}</p>;
}
export function ErrorLine({ text }: { text: string }) {
  return <p role="alert" style={errorLine}>{text}</p>;
}
export function Status({ text }: { text: string }) {
  return (
    <p style={statusLine}>
      <span className="luma-spin" style={spinner} /> {text}
    </p>
  );
}
export function Logo({ top = '14vh' }: { top?: string }) {
  return (
    <img
      src="/static/images/luma_text.webp"
      alt="Luma"
      style={{ width: 'clamp(240px, 64vw, 520px)', height: 'auto', userSelect: 'none', marginTop: top }}
      draggable={false}
    />
  );
}
export function PlexMark() {
  return (
    <svg width="14" height="18" viewBox="0 0 14 18" aria-hidden="true" style={{ marginRight: 10 }}>
      <path d="M0 0h5.2L14 9l-8.8 9H0l8.8-9z" fill="currentColor" />
    </svg>
  );
}
export function Check() {
  return (
    <svg width="72" height="72" viewBox="0 0 72 72" aria-hidden="true" style={{ marginTop: '18vh', marginBottom: 22 }}>
      <circle cx="36" cy="36" r="34" fill="rgba(255,255,255,0.08)" stroke="rgba(255,255,255,0.25)" strokeWidth="1.5" />
      <path d="M23 37l9 9 17-19" fill="none" stroke="#fff" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function DeleteIcon() {
  return (
    <svg width="26" height="20" viewBox="0 0 26 20" aria-hidden="true">
      <path d="M8 1h15a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H8L1 10z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M12 6l8 8M20 6l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

/** Four dots plus a phone-style keypad. Calls onComplete with 4 digits. */
export function PinPad({ value, onChange, onComplete, shake, disabled, compact }: {
  value: string;
  onChange: (v: string) => void;
  onComplete: (v: string) => void;
  shake?: boolean;
  disabled?: boolean;
  compact?: boolean;
}) {
  const press = (k: string) => {
    if (disabled) return;
    if (k === 'del') { onChange(value.slice(0, -1)); return; }
    if (value.length >= 4) return;
    const next = value + k;
    onChange(next);
    if (next.length === 4) window.setTimeout(() => onComplete(next), 150);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (/^[0-9]$/.test(e.key)) press(e.key);
      else if (e.key === 'Backspace') press('del');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const size = compact ? 62 : 78;
  const k: React.CSSProperties = { ...key, width: size, height: size, fontSize: compact ? 26 : 32 };
  return (
    <>
      <div className={shake ? 'luma-shake' : ''} style={dots} aria-label={`${value.length} of 4 digits`}>
        {[0, 1, 2, 3].map(i => (
          <span key={i} style={{ ...dot, background: i < value.length ? '#fff' : 'transparent' }} />
        ))}
      </div>
      <div style={{ ...pad, gridTemplateColumns: `repeat(3, ${size}px)` }}>
        {['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', 'del'].map((d, i) =>
          d === '' ? <span key={i} /> : (
            <button
              key={i}
              type="button"
              className="luma-key"
              style={d === 'del' ? { ...k, background: 'transparent', display: 'flex', alignItems: 'center', justifyContent: 'center' } : k}
              onClick={() => press(d)}
              aria-label={d === 'del' ? 'Delete' : d}
            >
              {d === 'del' ? <DeleteIcon /> : d}
            </button>
          ),
        )}
      </div>
    </>
  );
}

/* ── Styles ─────────────────────────────────────────────────────────────── */

const page: React.CSSProperties = {
  position: 'fixed', inset: 0, overflow: 'auto', zIndex: 100,
  background: 'radial-gradient(ellipse at 50% 30%, #262a66 0%, #15102a 55%, #0a0818 100%)',
  color: '#fff', fontFamily: FONT, WebkitFontSmoothing: 'antialiased',
};
const center: React.CSSProperties = {
  minHeight: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center',
  padding: 'env(safe-area-inset-top) 24px env(safe-area-inset-bottom)', boxSizing: 'border-box',
};
const bottom: React.CSSProperties = {
  marginTop: 'auto', width: '100%', maxWidth: 360, paddingBottom: 'max(48px, 8vh)',
  display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12,
};
const title: React.CSSProperties = { margin: 0, marginTop: 'min(18vh, 160px)', fontSize: 28, fontWeight: 600, letterSpacing: '-0.01em', textAlign: 'center' };
const sub: React.CSSProperties = { margin: '8px 0 0', fontSize: 15, color: 'rgba(255,255,255,0.55)', textAlign: 'center' };
export const primaryBtn: React.CSSProperties = {
  width: '100%', height: 50, borderRadius: 25, border: 'none', background: '#fff', color: '#15102a',
  fontSize: 17, fontWeight: 600, fontFamily: FONT, cursor: 'pointer',
};
export const plexBtn: React.CSSProperties = {
  ...primaryBtn, background: '#e5a00d', color: '#1f1f1f', display: 'flex', alignItems: 'center', justifyContent: 'center',
};
export const linkBtn: React.CSSProperties = {
  background: 'none', border: 'none', color: 'rgba(255,255,255,0.6)', fontSize: 15, fontFamily: FONT,
  padding: '10px 16px', cursor: 'pointer', textDecoration: 'none',
};
const errorLine: React.CSSProperties = { margin: 0, fontSize: 14, color: '#ff8a8a', textAlign: 'center' };
const statusLine: React.CSSProperties = { margin: 0, fontSize: 16, color: 'rgba(255,255,255,0.8)', display: 'flex', alignItems: 'center', gap: 10, height: 50 };
const spinner: React.CSSProperties = {
  width: 16, height: 16, borderRadius: '50%', border: '2px solid rgba(255,255,255,0.25)', borderTopColor: '#fff', display: 'inline-block',
};
export const list: React.CSSProperties = {
  width: '100%', maxWidth: 400, marginTop: 28, borderRadius: 14, overflow: 'hidden', background: 'rgba(255,255,255,0.07)',
};
export const row: React.CSSProperties = {
  width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 18px',
  background: 'transparent', border: 'none', borderBottom: '1px solid rgba(255,255,255,0.08)', color: '#fff',
  fontSize: 17, fontFamily: FONT, cursor: 'pointer', textAlign: 'left',
};
const dots: React.CSSProperties = { display: 'flex', gap: 22, marginTop: 34 };
const dot: React.CSSProperties = { width: 14, height: 14, borderRadius: '50%', border: '1.5px solid #fff', transition: 'background .12s' };
const pad: React.CSSProperties = { display: 'grid', gap: '16px 26px', marginTop: 36, marginBottom: 'max(40px, 6vh)' };
const key: React.CSSProperties = {
  borderRadius: '50%', border: 'none', background: 'rgba(255,255,255,0.12)', color: '#fff',
  fontWeight: 400, fontFamily: FONT, cursor: 'pointer', transition: 'background .1s',
};
