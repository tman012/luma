/**
 * One-time "You're all set" toast shown over Home right after the owner
 * finishes Get Started. It never blocks taps and dismisses itself.
 */
import { useEffect, useState } from 'react';

export const SETUP_TOAST_KEY = 'luma-setup-finished';
export const FADE_IN_MS = 200;
export const HOLD_MS = 1800;
export const FADE_OUT_MS = 300;
export const TOTAL_MS = FADE_IN_MS + HOLD_MS + FADE_OUT_MS;

export function markSetupJustFinished(): void {
  try { window.sessionStorage.setItem(SETUP_TOAST_KEY, '1'); } catch { /* storage off */ }
}

/** Reads and clears the flag so the toast shows exactly once. */
export function takeSetupJustFinished(): boolean {
  try {
    const on = window.sessionStorage.getItem(SETUP_TOAST_KEY) === '1';
    if (on) window.sessionStorage.removeItem(SETUP_TOAST_KEY);
    return on;
  } catch {
    return false;
  }
}

export function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;
  } catch {
    return false;
  }
}

const css = `
@keyframes lumaToast{
  0%{opacity:0;transform:translate(-50%,-50%) scale(.96)}
  ${(FADE_IN_MS / TOTAL_MS * 100).toFixed(2)}%{opacity:1;transform:translate(-50%,-50%) scale(1)}
  ${((FADE_IN_MS + HOLD_MS) / TOTAL_MS * 100).toFixed(2)}%{opacity:1;transform:translate(-50%,-50%) scale(1)}
  100%{opacity:0;transform:translate(-50%,-50%) scale(1)}
}
.luma-setup-toast{animation:lumaToast ${TOTAL_MS}ms ease both}
@media (prefers-reduced-motion: reduce){.luma-setup-toast{animation:none}}
`;

export function SetupToast() {
  const [show, setShow] = useState(() => takeSetupJustFinished());
  const [reduced] = useState(() => prefersReducedMotion());

  useEffect(() => {
    if (!show) return;
    const t = window.setTimeout(() => setShow(false), reduced ? FADE_IN_MS + HOLD_MS : TOTAL_MS);
    return () => window.clearTimeout(t);
  }, [show, reduced]);

  if (!show) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      className="luma-setup-toast"
      data-reduced-motion={reduced ? 'true' : 'false'}
      style={toast}
    >
      <style>{css}</style>
      <svg width="40" height="40" viewBox="0 0 40 40" aria-hidden="true">
        <circle cx="20" cy="20" r="19" fill="rgba(59,130,246,0.18)" stroke="rgba(255,255,255,0.3)" strokeWidth="1.2" />
        <path d="M12.5 20.5l5 5 10-11" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span style={label}>You're all set</span>
    </div>
  );
}

const toast: React.CSSProperties = {
  position: 'fixed', left: '50%', top: '50%', transform: 'translate(-50%,-50%)', zIndex: 1000,
  pointerEvents: 'none', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12,
  padding: '22px 28px', borderRadius: 20, minWidth: 180, boxSizing: 'border-box',
  background: 'rgba(21,16,42,0.62)', border: '1px solid rgba(255,255,255,0.14)',
  backdropFilter: 'blur(24px) saturate(160%)', WebkitBackdropFilter: 'blur(24px) saturate(160%)',
  boxShadow: '0 12px 40px rgba(0,0,0,0.45)', color: '#fff',
};
const label: React.CSSProperties = { fontSize: 17, fontWeight: 600, letterSpacing: '-0.01em', whiteSpace: 'nowrap' };
