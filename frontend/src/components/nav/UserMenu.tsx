/** User menu on the avatar (docs/design/settings-page-spec.md).
 * >=1024px: a dropdown (Settings for admins, divider, Sign out).
 * Phone: a right-side account panel (Plex iOS pattern): 72px avatar,
 * username, Settings (admins) and Sign out; Home shifts left 24px and dims. */
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router';
import { FONT, NAVY_BG } from '@/components/start/StartUI';
import { useKioskWidth } from '@/components/home/KioskHome';
import { GearIcon, LogOutIcon, PersonIcon } from '@/components/nav/AppNav';

interface Props {
  name: string; admin: boolean; busy: boolean; onSignOut: () => void; size?: number;
  /** v259: the phone Home header's trigger is prod 1b4ea74's user button (components/UserMenu.tsx). */
  prodTrigger?: boolean;
}

export function UserMenu(props: Props) {
  const wide = useKioskWidth();
  return wide ? <Dropdown {...props} /> : <AccountPanel {...props} />;
}

function Avatar({ name, size, ...rest }: { name: string; size: number } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const initial = (name.trim()[0] || '').toUpperCase();
  const style = { ...avatarStyle, width: size, height: size, borderRadius: size / 2 };
  const inner = initial
    ? <span style={{ fontSize: Math.round(size * 0.4), fontWeight: 600 }}>{initial}</span>
    : <PersonIcon size={Math.round(size * 0.45)} />;
  if (!rest.onClick) return <span style={style} aria-hidden="true">{inner}</span>;
  return <button type="button" {...rest} style={style}>{inner}</button>;
}

/** Prod 1b4ea74's user button (components/UserMenu.tsx): 42 px circle, white 10 % (15 % while open), person glyph. */
function ProdUserButton({ open, ...rest }: { open: boolean } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type="button" {...rest} data-testid="user-button"
      style={{
        flexShrink: 0, width: 42, height: 42, borderRadius: '50%',
        background: open ? 'rgba(255,255,255,0.15)' : 'rgba(255,255,255,0.1)', border: 'none', cursor: 'pointer',
        display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'background 0.15s', padding: 0,
      }}>
      <svg width="22" height="22" viewBox="0 0 18 18" fill="none" aria-hidden="true">
        <circle cx="9" cy="7" r="3" stroke="rgba(255,255,255,0.85)" strokeWidth="1.4" />
        <path d="M2.5 16c0-3.314 2.91-6 6.5-6s6.5 2.686 6.5 6" stroke="rgba(255,255,255,0.85)" strokeWidth="1.4"
          strokeLinecap="round" />
      </svg>
    </button>
  );
}

function Dropdown({ name, admin, busy, onSignOut, size = 48 }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', close);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', close); };
  }, [open]);
  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <style>{css}</style>
      <Avatar name={name} size={size} aria-label="Account" aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen(o => !o)} />
      {open && (
        <div role="menu" data-testid="user-menu" className="luma-user-menu" style={{ ...dropdown, top: size + 8 }}>
          {admin && (
            <>
              <button role="menuitem" type="button" className="luma-menu-item" style={dropItem}
                onClick={() => { setOpen(false); navigate('/settings'); }}>
                <span style={dropIcon}><GearIcon size={18} /></span>Settings
              </button>
              <div role="separator" style={{ height: 1, margin: '6px 0', background: 'rgba(255,255,255,0.08)' }} />
            </>
          )}
          <button role="menuitem" type="button" className="luma-menu-item" style={dropItem} disabled={busy}
            onClick={() => { setOpen(false); onSignOut(); }}>
            <span style={dropIcon}><LogOutIcon size={18} /></span>Sign out
          </button>
        </div>
      )}
    </div>
  );
}

function AccountPanel({ name, admin, busy, onSignOut, size = 40, prodTrigger }: Props) {
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [shown, setShown] = useState(false);
  const startX = useRef<number | null>(null);
  const navigate = useNavigate();
  useEffect(() => {
    if (open) {
      setMounted(true);
      const f = requestAnimationFrame(() => setShown(true));
      return () => cancelAnimationFrame(f);
    }
    setShown(false);
    const t = window.setTimeout(() => setMounted(false), 260);
    return () => window.clearTimeout(t);
  }, [open]);
  useEffect(() => {
    const html = document.documentElement;
    if (shown) html.setAttribute('data-account-panel', ''); else html.removeAttribute('data-account-panel');
    return () => html.removeAttribute('data-account-panel');
  }, [shown]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);
  return (
    <>
      <style>{css}</style>
      {prodTrigger
        ? <ProdUserButton open={open} aria-label="Account" aria-haspopup="dialog" aria-expanded={open}
            onClick={() => setOpen(true)} />
        : <Avatar name={name} size={size} aria-label="Account" aria-haspopup="dialog" aria-expanded={open}
            onClick={() => setOpen(true)} />}
      {mounted && createPortal(
        <div className="luma-fullscreen luma-bleed" style={{ position: 'fixed', top: 0, left: 0, right: 0, zIndex: 300 }}>
          <div className="luma-bleed" data-testid="account-scrim" onClick={() => setOpen(false)}
            style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.55)', opacity: shown ? 1 : 0, transition: `opacity ${MS}ms` }} />
          <div role="dialog" aria-modal="true" aria-label="Account" data-testid="account-panel"
            onPointerDown={e => { startX.current = e.clientX; }}
            onPointerUp={e => { if (startX.current !== null && e.clientX - startX.current > 60) setOpen(false); startX.current = null; }}
            style={{ ...panel, transform: shown ? 'none' : 'translateX(105%)' }}>
            <div style={{ marginTop: 32 }}><Avatar name={name} size={72} /></div>
            <div data-testid="account-name" style={panelName}>{name}</div>
            <div style={{ marginTop: 32, display: 'flex', flexDirection: 'column' }}>
              {admin && (
                <button type="button" className="luma-menu-item" style={panelRow}
                  onClick={() => { setOpen(false); navigate('/settings'); }}>
                  <span style={panelIcon}><GearIcon size={22} /></span>Settings
                </button>
              )}
              <button type="button" className="luma-menu-item" style={panelRow} disabled={busy}
                onClick={() => { setOpen(false); onSignOut(); }}>
                <span style={panelIcon}><LogOutIcon size={22} /></span>Sign out
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}

const MS = 260;
const EASE = 'cubic-bezier(.2,.8,.2,1)';
/** Pages mark their content with data-shift-root so it shifts and dims behind the panel. */
const css = `
.luma-menu-item:hover,.luma-menu-item:active{background:rgba(255,255,255,.06)}
.luma-menu-item:focus-visible{outline:2px solid #60a5fa;outline-offset:-2px}
.luma-user-menu{animation:luma-menu-in 120ms ease-out;transform-origin:top right}
@keyframes luma-menu-in{from{opacity:0;transform:scale(.98)}to{opacity:1;transform:none}}
[data-shift-root]{transition:transform ${MS}ms ${EASE},filter ${MS}ms}
html[data-account-panel] [data-shift-root]{transform:translateX(-24px);filter:brightness(.7)}
@media (prefers-reduced-motion: reduce){.luma-user-menu{animation:none}[data-shift-root],[data-testid="account-panel"]{transition:none!important}}
`;
const avatarStyle: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none', padding: 0,
  background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', cursor: 'pointer',
  fontFamily: FONT, boxSizing: 'border-box',
};
const dropdown: React.CSSProperties = {
  position: 'absolute', right: 0, minWidth: 200, padding: 6, borderRadius: 14, zIndex: 100, boxSizing: 'border-box',
  background: 'rgba(21,16,42,0.96)', border: '1px solid rgba(255,255,255,0.10)',
  boxShadow: '0 12px 32px rgba(0,0,0,0.45)', backdropFilter: 'blur(24px)', WebkitBackdropFilter: 'blur(24px)',
};
const dropItem: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 12, width: '100%', height: 44, padding: '0 12px', borderRadius: 10,
  background: 'transparent', border: 'none', color: 'rgba(255,255,255,0.9)', fontFamily: FONT, fontSize: 15,
  fontWeight: 500, cursor: 'pointer', textAlign: 'left', boxSizing: 'border-box',
};
const dropIcon: React.CSSProperties = { display: 'inline-flex', color: 'rgba(255,255,255,0.7)' };
const panel: React.CSSProperties = {
  position: 'absolute', top: 0, right: 0, width: '82vw', maxWidth: 340, boxSizing: 'border-box',
  display: 'flex', flexDirection: 'column', fontFamily: FONT, color: '#fff',
  padding: 'env(safe-area-inset-top) 24px var(--luma-end-pad)', bottom: 0,
  // App navy only (navy-audit.md): the --color-kiosk-black base with the NAVY_BG glow, plus white 4%.
  backgroundColor: 'var(--color-kiosk-black)',
  backgroundImage: `linear-gradient(rgba(255,255,255,0.04), rgba(255,255,255,0.04)), ${NAVY_BG}`,
  borderLeft: '1px solid rgba(255,255,255,0.08)', transition: `transform ${MS}ms ${EASE}`,
};
const panelName: React.CSSProperties = {
  marginTop: 12, fontSize: 22, fontWeight: 600, color: 'rgba(255,255,255,0.95)',
  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
};
const panelRow: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 16, height: 52, margin: '0 -12px', padding: '0 12px', borderRadius: 12,
  background: 'transparent', border: 'none', color: 'rgba(255,255,255,0.95)', fontFamily: FONT, fontSize: 17,
  fontWeight: 500, cursor: 'pointer', textAlign: 'left',
};
const panelIcon: React.CSSProperties = { display: 'inline-flex', color: 'rgba(255,255,255,0.8)' };
