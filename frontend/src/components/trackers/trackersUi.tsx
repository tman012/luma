/** Shared pieces for Settings > Trackers: tokens, icons, the switch, the
 * phone header, dialogs and the top toast (docs: settings-page-spec.md,
 * "Settings > Trackers"). Self-contained so the section drops into any
 * Settings shell. */
import { useEffect, useRef, useState } from 'react';
import type React from 'react';
import { FONT } from '@/components/start/StartUI';
import { T } from '@/theme/tokens';

export const NAVY = T.navy;
export const BLUE = T.accent;
export const BLUE_HOVER = T.accentHover;
export const DANGER = T.danger;
export const DANGER_TEXT = T.dangerText;
export const SUCCESS = T.success;
export const TEXT = T.text;
export const TEXT_2 = T.text2;
export const TEXT_3 = T.text3;
export const GLASS = T.glass;
export const GLASS_BORDER = `1px solid ${T.glass8}`;
export const DIVIDER = T.glass8;
export { FONT };

// ---------------------------------------------------------------- icons

const svg = (size: number, children: React.ReactNode) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{children}</svg>
);
export const PlusIcon = ({ size = 18 }: { size?: number }) => svg(size, <path d="M12 5v14M5 12h14" />);
export const ChevronRight = ({ size = 18 }: { size?: number }) => svg(size, <path d="M9 6l6 6-6 6" />);
export const ChevronLeft = ({ size = 18 }: { size?: number }) => svg(size, <path d="M15 6l-6 6 6 6" />);
export const CloseIcon = ({ size = 18 }: { size?: number }) => svg(size, <path d="M6 6l12 12M18 6L6 18" />);
export const CheckMark = ({ size = 16 }: { size?: number }) => svg(size, <path d="M5 12.5l4.5 4.5L19 7.5" />);
export const SearchIcon = ({ size = 18 }: { size?: number }) => svg(size, <><circle cx="11" cy="11" r="7" /><path d="M20 20l-4-4" /></>);
export const EyeIcon = ({ size = 20 }: { size?: number }) => svg(size, <><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></>);
export const EyeOffIcon = ({ size = 20 }: { size?: number }) => svg(size, <><path d="M10.6 5.1A10.4 10.4 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-2.6 3.4M6.6 6.6C3.7 8.4 2 12 2 12s3.6 7 10 7a9.7 9.7 0 0 0 5.4-1.6" /><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" /><path d="M3 3l18 18" /></>);

// ---------------------------------------------------------------- css

export const trackersCss = `
.lt-btn{transition:background .15s,opacity .15s}
.lt-primary:hover:not(:disabled){background:${BLUE_HOVER}!important}
.lt-ghost:hover:not(:disabled),.lt-row:hover,.lt-pick:hover{background:rgba(255,255,255,.06)}
.lt-row:active,.lt-pick:active{background:rgba(255,255,255,.06)}
.lt-btn:disabled{opacity:.5;cursor:default}
.lt-primary:disabled{opacity:.4}
.lt-focus:focus-visible,.lt-btn:focus-visible,.lt-row:focus-visible,.lt-pick:focus-visible,.lt-chip:focus-visible{outline:2px solid ${BLUE_HOVER};outline-offset:-2px}
.lt-input{width:100%;box-sizing:border-box;height:44px;padding:0 14px;border-radius:10px;background:${GLASS};
  border:1px solid rgba(255,255,255,.12);color:${TEXT};font-size:15px;font-family:${FONT};outline:none;
  transition:border-color .15s,box-shadow .15s;-moz-appearance:textfield}
.lt-input::placeholder{color:rgba(255,255,255,.35)}
.lt-input:focus{border-color:${BLUE};box-shadow:0 0 0 3px rgba(59,130,246,.25)}
.lt-input[aria-invalid="true"]{border-color:${DANGER}}
.lt-input[aria-invalid="true"]:focus{box-shadow:0 0 0 3px rgba(239,68,68,.25)}
.lt-input::-webkit-outer-spin-button,.lt-input::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
.lt-search{height:40px;padding-left:40px}
.lt-spin{width:18px;height:18px;border-radius:50%;border:2px solid rgba(255,255,255,.25);border-top-color:${T.white};animation:lt-spin .8s linear infinite}
.lt-fade{animation:lt-fade 160ms ease both}
.lt-pop{animation:lt-pop 160ms cubic-bezier(.2,.8,.2,1) both}
.lt-up{animation:lt-up 260ms cubic-bezier(.2,.8,.2,1) both}
.lt-toast{animation:lt-toast 2300ms ease both}
@keyframes lt-spin{to{transform:rotate(360deg)}}
@keyframes lt-fade{from{opacity:0}to{opacity:1}}
@keyframes lt-pop{from{opacity:0;transform:scale(.98)}to{opacity:1;transform:none}}
@keyframes lt-up{from{transform:translateY(100%)}to{transform:none}}
@keyframes lt-toast{0%{opacity:0;transform:translate(-50%,-8px)}9%,87%{opacity:1;transform:translate(-50%,0)}100%{opacity:0;transform:translate(-50%,0)}}
@media (max-width: 1023px){.lt-input{font-size:16px}}
@media (prefers-reduced-motion: reduce){.lt-pop,.lt-up,.lt-toast{animation:lt-fade 160ms ease both}.lt-spin{animation-duration:2s}}
`;

// ---------------------------------------------------------------- buttons

export const primaryBtn: React.CSSProperties = {
  height: 44, padding: '0 22px', borderRadius: 10, border: 'none', background: BLUE, color: T.white,
  fontSize: 15, fontWeight: 600, fontFamily: FONT, cursor: 'pointer', display: 'inline-flex',
  alignItems: 'center', justifyContent: 'center', gap: 8, boxSizing: 'border-box',
};
export const secondaryBtn: React.CSSProperties = {
  ...primaryBtn, background: GLASS, border: `1px solid ${T.glass12}`, color: TEXT, fontWeight: 500,
};
export const dangerTextBtn: React.CSSProperties = {
  ...primaryBtn, background: 'none', color: DANGER, padding: '0 8px', fontWeight: 500,
};
export const circleHit: React.CSSProperties = {
  width: 44, height: 44, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: 0,
  border: 'none', background: 'none', cursor: 'pointer', borderRadius: 22, color: 'rgba(255,255,255,0.9)', flex: 'none',
};
export const circle: React.CSSProperties = {
  width: 36, height: 36, borderRadius: 18, background: 'rgba(255,255,255,0.10)',
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
};

export function Spinner() {
  return <span className="lt-spin" aria-hidden="true" data-testid="tracker-spinner" />;
}

// ---------------------------------------------------------------- switch

/** iOS-style switch: 51x31 on phone, 44x26 on desktop; on = Luma blue. */
export function Switch({ on, label, onChange, small, disabled }: {
  on: boolean; label: string; onChange: (on: boolean) => void; small?: boolean; disabled?: boolean;
}) {
  const w = small ? 44 : 51;
  const h = small ? 26 : 31;
  const k = h - 4;
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled}
      className="lt-focus" data-testid="tracker-switch"
      onClick={e => { e.stopPropagation(); onChange(!on); }}
      style={{ width: w + 8, height: 44, display: 'inline-flex', alignItems: 'center', justifyContent: 'flex-end',
        border: 'none', background: 'none', cursor: disabled ? 'default' : 'pointer', padding: 0, flex: 'none', borderRadius: 10 }}>
      <span data-track style={{ position: 'relative', display: 'block', width: w, height: h, borderRadius: h / 2,
        background: on ? BLUE : 'rgba(255,255,255,0.18)', transition: 'background .2s' }}>
        <span style={{ position: 'absolute', top: 2, left: 2, width: k, height: k, borderRadius: k / 2, background: T.white,
          transition: 'transform .2s', transform: on ? `translateX(${w - h}px)` : 'none', boxShadow: '0 1px 3px rgba(0,0,0,0.3)' }} />
      </span>
    </button>
  );
}

// ---------------------------------------------------------------- phone header

/** The sheet's 56px header: circular back left, centered title, optional
 * circular action right (36px circle, 44px target). */
export function PhoneHeader({ title, onBack, action }: {
  title: string; onBack: () => void; action?: { label: string; icon: React.ReactNode; onClick: () => void };
}) {
  return (
    <header style={{ position: 'relative', height: 56, display: 'flex', alignItems: 'center',
      justifyContent: 'space-between', padding: '0 12px' }} data-testid="trackers-phone-header">
      <button type="button" aria-label="Back" onClick={onBack} style={{ ...circleHit, position: 'relative', zIndex: 1 }} className="lt-focus">
        <span style={circle}><ChevronLeft size={18} /></span>
      </button>
      <h1 style={{ position: 'absolute', left: 64, right: 64, margin: 0, textAlign: 'center', fontSize: 17, fontWeight: 600,
        fontFamily: FONT, color: TEXT, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{title}</h1>
      {action && (
        <button type="button" aria-label={action.label} onClick={action.onClick} style={{ ...circleHit, position: 'relative', zIndex: 1 }} className="lt-focus">
          <span style={circle}>{action.icon}</span>
        </button>
      )}
    </header>
  );
}

// ---------------------------------------------------------------- dialogs

function useEscape(onEscape: () => void) {
  const ref = useRef(onEscape);
  ref.current = onEscape;
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); ref.current(); } };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, []);
}

/** A modal surface: centered at `width` on desktop, a bottom sheet on phone.
 * Esc and a scrim tap close it. */
export function Modal({ wide, width, label, onClose, children, testId }: {
  wide: boolean; width: number; label: string; onClose: () => void; children: React.ReactNode; testId?: string;
}) {
  useEscape(onClose);
  return (
    <div className="lt-fade" style={{ position: 'fixed', inset: 0, zIndex: 300, background: 'rgba(0,0,0,0.55)', display: 'flex',
      alignItems: wide ? 'center' : 'flex-end', justifyContent: 'center', padding: wide ? 20 : 0 }}
      onClick={onClose} data-testid={testId ? `${testId}-scrim` : undefined}>
      <div role="dialog" aria-modal="true" aria-label={label} data-testid={testId}
        className={wide ? 'lt-pop' : 'lt-up'} onClick={e => e.stopPropagation()}
        style={{ width: '100%', maxWidth: wide ? width : 'none', boxSizing: 'border-box', fontFamily: FONT, color: TEXT,
          backgroundColor: NAVY, backgroundImage: `linear-gradient(${GLASS}, ${GLASS})`, border: GLASS_BORDER,
          borderRadius: wide ? 20 : '20px 20px 0 0', boxShadow: '0 24px 64px rgba(0,0,0,0.5)',
          paddingBottom: wide ? 0 : 'env(safe-area-inset-bottom)', maxHeight: wide ? 'calc(100vh - 40px)' : '90vh',
          display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {children}
      </div>
    </div>
  );
}

/** "Delete X?" / "Discard changes?": a title and two 44px buttons, the red one last. */
export function ConfirmDialog({ wide, title, confirm, cancel, onConfirm, onCancel, busy }: {
  wide: boolean; title: string; confirm: string; cancel: string; onConfirm: () => void; onCancel: () => void; busy?: boolean;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { cancelRef.current?.focus(); }, []);
  return (
    <Modal wide={wide} width={400} label={title} onClose={onCancel} testId="tracker-confirm">
      <div style={{ padding: 24 }}>
        <p style={{ margin: '0 0 20px', fontSize: 17, fontWeight: 600, color: TEXT, overflowWrap: 'anywhere' }}>{title}</p>
        <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', flexDirection: wide ? 'row' : 'column-reverse' }}>
          <button ref={cancelRef} type="button" className="lt-btn lt-ghost" style={secondaryBtn} onClick={onCancel}>{cancel}</button>
          <button type="button" className="lt-btn" style={{ ...primaryBtn, background: DANGER }} disabled={busy} onClick={onConfirm}>{confirm}</button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- toast

/** The brief top-center glass toast ("Saved"). Calls onDone when it has faded. */
export function TopToast({ text, onDone }: { text: string; onDone: () => void }) {
  const done = useRef(onDone);
  done.current = onDone;
  useEffect(() => {
    const t = window.setTimeout(() => done.current(), 2300);
    return () => window.clearTimeout(t);
  }, []);
  return (
    <div role="status" aria-live="polite" className="lt-toast" data-testid="trackers-toast"
      style={{ position: 'fixed', left: '50%', top: 'calc(env(safe-area-inset-top, 0px) + 24px)', transform: 'translate(-50%,0)',
        zIndex: 1000, pointerEvents: 'none', padding: '10px 18px', borderRadius: 999, fontFamily: FONT,
        background: 'rgba(21,16,42,0.62)', border: '1px solid rgba(255,255,255,0.14)',
        backdropFilter: 'blur(24px) saturate(160%)', WebkitBackdropFilter: 'blur(24px) saturate(160%)',
        boxShadow: '0 12px 40px rgba(0,0,0,0.45)', color: T.white, fontSize: 15, fontWeight: 600, whiteSpace: 'nowrap' }}>
      {text}
    </div>
  );
}

/** A transient value that clears itself after `ms` (the 4 s Test status). */
export function useTransient<T>(ms: number): [T | null, (v: T | null) => void] {
  const [value, setValue] = useState<T | null>(null);
  useEffect(() => {
    if (value === null) return;
    const t = window.setTimeout(() => setValue(null), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return [value, setValue];
}

export const srOnly: React.CSSProperties = {
  position: 'absolute', width: 1, height: 1, padding: 0, margin: -1, overflow: 'hidden', clip: 'rect(0,0,0,0)',
  whiteSpace: 'nowrap', border: 0,
};

// ---------------------------------------------------------------- category chip

/** A toggle chip (Trackers categories, Library paths conditions): 44px on
 * phones, 36px on desktop, fully rounded, 14px/500. */
export function CategoryChip({ on, label, wide, onToggle, testId }: {
  on: boolean; label: string; wide: boolean; onToggle: () => void; testId?: string;
}) {
  return (
    <button type="button" className="lt-chip lt-btn" aria-pressed={on} onClick={onToggle} data-testid={testId}
      style={{
        height: wide ? 36 : 44, padding: '0 14px', borderRadius: 999, fontFamily: FONT, fontSize: 14, fontWeight: 500,
        cursor: 'pointer', boxSizing: 'border-box',
        border: on ? `1px solid ${BLUE}` : '1px solid rgba(255,255,255,0.10)',
        background: on ? 'rgba(59,130,246,0.18)' : 'rgba(255,255,255,0.06)',
        color: on ? T.white : 'rgba(255,255,255,0.70)',
      }}>
      {label}
    </button>
  );
}
