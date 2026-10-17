/** App navigation (docs/design/sidebar-nav-spec.md), Sonarr-style, navy.
 * Desktop Settings: a persistent 220px sidebar. Phone, and the web kiosk
 * Home (full-bleed slideshow): a drawer opened from a menu button, with a
 * scrim, Esc, swipe-left, focus trap and body scroll lock. */
import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { assetUrl } from '@/urlHelpers';
import { FONT, LOGO_SRC } from '@/components/start/StartUI';
import { LUMA_BLUE } from '@/ui/lumaButton';

export const SIDEBAR_W = 220;
const NAVY = '#15102a';
const GLASS = 'rgba(21,16,42,0.92)';

interface NavSub { to: string; label: string }
interface NavEntry { to: string; label: string; icon: 'home' | 'settings'; adminOnly?: boolean; subs?: NavSub[] }

export const NAV: NavEntry[] = [
  { to: '/', label: 'Home', icon: 'home' },
  { to: '/settings', label: 'Settings', icon: 'settings', adminOnly: true,
    subs: [{ to: '/settings/home-screen', label: 'Home screen' }] },
];

function useEntries(): NavEntry[] {
  const { user } = useAuth();
  return NAV.filter(e => !e.adminOnly || user?.is_admin);
}

// --- icons (lucide shapes, ISC) ----------------------------------------------
const svg = (size: number, children: React.ReactNode) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
);
export const HouseIcon = () => svg(20, <><path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8" /><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></>);
export const GearIcon = () => svg(20, <><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" /><circle cx="12" cy="12" r="3" /></>);
export const ChevronIcon = () => svg(16, <path d="m9 18 6-6-6-6" />);
export const MenuIcon = () => svg(24, <><path d="M4 6h16" /><path d="M4 12h16" /><path d="M4 18h16" /></>);
export const GripIcon = () => svg(20, <><circle cx="9" cy="12" r="1" /><circle cx="9" cy="5" r="1" /><circle cx="9" cy="19" r="1" /><circle cx="15" cy="12" r="1" /><circle cx="15" cy="5" r="1" /><circle cx="15" cy="19" r="1" /></>);
export const CheckIcon = () => svg(20, <path d="M20 6 9 17l-5-5" />);

// --- items -------------------------------------------------------------------
function NavList({ size, onNavigate }: { size: 'desktop' | 'mobile'; onNavigate?: () => void }) {
  const entries = useEntries();
  const { pathname } = useLocation();
  const inSettings = pathname === '/settings' || pathname.startsWith('/settings/');
  const [open, setOpen] = useState(inSettings);
  useEffect(() => { if (inSettings) setOpen(true); }, [inSettings]);
  const itemH = size === 'desktop' ? 44 : 48;
  const subH = size === 'desktop' ? 36 : 44;
  return (
    <ul style={{ listStyle: 'none', margin: 0, padding: '8px 0', display: 'flex', flexDirection: 'column', gap: 2 }}>
      {entries.map(e => {
        const active = e.subs ? false : pathname === e.to;
        const parentActive = !!e.subs && inSettings;
        const label = (
          <>
            <span style={{ color: active || parentActive ? LUMA_BLUE : 'inherit', display: 'inline-flex' }}>
              {e.icon === 'home' ? <HouseIcon /> : <GearIcon />}
            </span>
            <span style={{ flex: 1 }}>{e.label}</span>
          </>
        );
        return (
          <li key={e.to}>
            {e.subs ? (
              <button type="button" className="luma-nav-item" aria-expanded={open} onClick={() => setOpen(o => !o)}
                data-active={parentActive ? 'true' : undefined}
                style={{ ...item, height: itemH, ...(parentActive ? { color: 'rgba(255,255,255,0.95)' } : null) }}>
                {label}
                <span style={{ display: 'inline-flex', transition: 'transform 150ms', transform: open ? 'rotate(90deg)' : 'none' }}>
                  <ChevronIcon />
                </span>
              </button>
            ) : (
              <Link to={e.to} className="luma-nav-item" onClick={onNavigate} aria-current={active ? 'page' : undefined}
                style={{ ...item, height: itemH, ...(active ? itemOn : null) }}>
                {active && <span style={bar} aria-hidden="true" />}
                {label}
              </Link>
            )}
            {e.subs && open && (
              <ul style={{ listStyle: 'none', margin: '2px 0 4px', padding: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
                {e.subs.map(s => {
                  const on = pathname === s.to || (pathname === '/settings' && s === e.subs![0]);
                  return (
                    <li key={s.to}>
                      <Link to={s.to} className="luma-nav-item" onClick={onNavigate} aria-current={on ? 'page' : undefined}
                        style={{ ...sub, height: subH, ...(on ? subOn : null) }}>
                        {on && <span style={bar} aria-hidden="true" />}
                        {s.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function LogoRow({ height }: { height: number }) {
  return (
    <div style={{ height, display: 'flex', alignItems: 'center', padding: '0 20px', flex: 'none' }}>
      <img src={assetUrl(LOGO_SRC)} alt="Luma" data-testid="nav-logo" style={{ height: 40, width: 'auto' }} draggable={false} />
    </div>
  );
}

/** Desktop: the persistent 220px sidebar. */
export function Sidebar() {
  return (
    <nav aria-label="Main" data-testid="app-sidebar" style={sidebar}>
      <style>{css}</style>
      <LogoRow height={72} />
      <NavList size="desktop" />
    </nav>
  );
}

/** The menu button that opens the drawer (44px target, 24px icon). */
export function MenuButton({ onClick, style }: { onClick: () => void; style?: React.CSSProperties }) {
  return (
    <button type="button" aria-label="Menu" onClick={onClick} style={{ ...menuBtn, ...style }} data-testid="menu-button">
      <MenuIcon />
    </button>
  );
}

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Drawer: 280px (max 85vw) from the left, scrim, Esc / scrim / swipe-left /
 * selection close it, focus trapped while open, body scroll locked. */
export function NavDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const panel = useRef<HTMLDivElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);
  const startX = useRef<number | null>(null);
  const [reduced] = useState(() => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
  // Mounted only while open (and during the 220ms close), so nothing in it
  // is focusable or in the page while it's closed.
  const [mounted, setMounted] = useState(open);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (open) {
      setMounted(true);
      const f = requestAnimationFrame(() => setShown(true));
      return () => cancelAnimationFrame(f);
    }
    setShown(false);
    const t = window.setTimeout(() => setMounted(false), 220);
    return () => window.clearTimeout(t);
  }, [open]);

  useEffect(() => {
    if (!open || !mounted) return;
    returnTo.current = document.activeElement as HTMLElement | null;
    const html = document.documentElement;
    const prev = [html.style.overflow, document.body.style.overflow];
    html.style.overflow = 'hidden';
    document.body.style.overflow = 'hidden';
    const first = panel.current?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onClose(); return; }
      if (e.key !== 'Tab' || !panel.current) return;
      const els = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!els.length) return;
      const a = els[0]!, z = els[els.length - 1]!;
      if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
      else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
      else if (!panel.current.contains(document.activeElement)) { e.preventDefault(); a.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      html.style.overflow = prev[0]!;
      document.body.style.overflow = prev[1]!;
      returnTo.current?.focus?.();
    };
  }, [open, mounted, onClose]);

  if (!mounted) return null;
  const motion = reduced ? 'opacity 220ms' : 'transform 220ms cubic-bezier(.2,.8,.2,1)';
  return (
    <div data-testid="nav-drawer-root" style={{ position: 'fixed', inset: 0, zIndex: 200, pointerEvents: open ? 'auto' : 'none' }}>
      <style>{css}</style>
      <div data-testid="nav-scrim" onClick={onClose}
        style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.5)', opacity: shown ? 1 : 0, transition: 'opacity 220ms' }} />
      <div ref={panel} role="dialog" aria-modal="true" aria-label="Menu" data-testid="nav-drawer"
        onPointerDown={e => { startX.current = e.clientX; }}
        onPointerUp={e => { if (startX.current !== null && startX.current - e.clientX > 60) onClose(); startX.current = null; }}
        style={{
          ...drawer,
          transition: motion,
          ...(reduced ? { opacity: shown ? 1 : 0 } : { transform: shown ? 'none' : 'translateX(-105%)' }),
        }}>
        <LogoRow height={56} />
        <NavList size="mobile" onNavigate={onClose} />
      </div>
    </div>
  );
}

// --- styles ------------------------------------------------------------------
const css = `
.luma-nav-item:hover{background:rgba(255,255,255,.06);color:rgba(255,255,255,.95)}
.luma-nav-item[aria-current="page"]:not([data-sub]):hover{background:rgba(59,130,246,.14)}
.luma-nav-item:focus-visible{outline:2px solid #60a5fa;outline-offset:2px}
`;
const sidebar: React.CSSProperties = {
  position: 'fixed', top: 0, left: 0, bottom: 0, width: SIDEBAR_W, zIndex: 60, display: 'flex', flexDirection: 'column',
  background: GLASS, backdropFilter: 'blur(24px)', WebkitBackdropFilter: 'blur(24px)',
  borderRight: '1px solid rgba(255,255,255,0.08)', fontFamily: FONT, color: '#fff', boxSizing: 'border-box',
};
const drawer: React.CSSProperties = {
  position: 'absolute', top: 0, left: 0, bottom: 0, width: 280, maxWidth: '85vw', display: 'flex', flexDirection: 'column',
  background: NAVY, backgroundImage: `linear-gradient(${GLASS}, ${GLASS})`, borderRight: '1px solid rgba(255,255,255,0.08)',
  paddingTop: 'env(safe-area-inset-top)', paddingBottom: 'env(safe-area-inset-bottom)', boxSizing: 'border-box',
  fontFamily: FONT, color: '#fff', boxShadow: '0 0 40px rgba(0,0,0,0.5)',
};
const item: React.CSSProperties = {
  position: 'relative', display: 'flex', alignItems: 'center', gap: 12, margin: '0 8px', padding: '0 12px',
  width: 'calc(100% - 16px)', boxSizing: 'border-box', borderRadius: 10, border: 'none', background: 'transparent',
  color: 'rgba(255,255,255,0.7)', fontSize: 15, fontWeight: 500, fontFamily: FONT, textDecoration: 'none',
  cursor: 'pointer', textAlign: 'left',
};
const itemOn: React.CSSProperties = { background: 'rgba(59,130,246,0.14)', color: 'rgba(255,255,255,0.95)' };
const sub: React.CSSProperties = {
  ...item, gap: 0, paddingLeft: 44, fontSize: 14, fontWeight: 400, color: 'rgba(255,255,255,0.6)',
};
const subOn: React.CSSProperties = { color: 'rgba(255,255,255,0.95)', background: 'transparent' };
const bar: React.CSSProperties = {
  position: 'absolute', left: 0, top: 8, bottom: 8, width: 3, borderRadius: 2, background: LUMA_BLUE,
};
const menuBtn: React.CSSProperties = {
  width: 44, height: 44, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', borderRadius: 12,
  border: 'none', background: 'transparent', color: '#fff', cursor: 'pointer', padding: 0, flex: 'none',
};
