/** Settings (/settings/<section>), docs/design/sidebar-nav-spec.md and
 * docs/design/mobile-settings-spec.md. Desktop: the persistent sidebar and
 * the section in the main pane. Phone: the 56px app header (hamburger,
 * title, avatar) and the drawer. Home screen: Libraries (drag to reorder,
 * switch on/off, instant save) and, on desktop for owner/admin, Kiosk >
 * Backdrop library. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { FONT } from '@/components/start/StartUI';
import { LUMA_BLUE } from '@/ui/lumaButton';
import {
  fetchKioskLibrary, fetchLibraries, move, saveKioskLibrary, saveLibraries,
  type KioskLibraries, type Library,
} from '@/components/home/homeApi';
import { AccountMenu, homePage, phoneBar } from '@/pages/MinimalHomePage';
import { useBodyScroll } from '@/hooks/useBodyScroll';
import { useKioskWidth } from '@/components/home/KioskHome';
import { CheckIcon, ChevronIcon, GripIcon, MenuButton, NavDrawer, SIDEBAR_W, Sidebar } from '@/components/nav/AppNav';
import { NoticeToast } from '@/components/start/SetupToast';

export const SETTINGS_SECTIONS = [{ slug: 'home-screen', label: 'Home screen' }] as const;
const DEFAULT_SECTION = SETTINGS_SECTIONS[0].slug;
export const SAVE_FAILED = "Couldn't save";

/** /settings/libraries is now /settings/home-screen. */
export function LibrariesRedirect() {
  return <Navigate to={`/settings/${DEFAULT_SECTION}`} replace />;
}

export function SettingsPage() {
  const { authenticated, loading, user, implicit, logout } = useAuth();
  const { section } = useParams();
  const wide = useKioskWidth();
  const navigate = useNavigate();
  const [drawer, setDrawer] = useState(false);
  const [busy, setBusy] = useState(false);
  const closeDrawer = useCallback(() => setDrawer(false), []);
  useBodyScroll();
  if (loading) return null;
  if (!authenticated) return <Navigate to="/login" replace />;
  const known = SETTINGS_SECTIONS.find(s => s.slug === section);
  if (!known) return <Navigate to={`/settings/${DEFAULT_SECTION}`} replace />;
  const admin = !!user?.is_admin;
  const signOut = async () => { setBusy(true); await logout(); navigate('/login', { replace: true }); };
  const avatar = (size: number) => !implicit
    ? <AccountMenu name={user?.name || ''} busy={busy} onSignOut={signOut} size={size} />
    : <span style={{ width: size }} />;
  const content = <HomeScreenSection admin={admin} wide={wide} />;

  if (wide) {
    return (
      <div style={homePage} data-step="settings" data-layout="sidebar">
        <Sidebar />
        <div style={{ marginLeft: SIDEBAR_W, minHeight: '100dvh' }}>
          <header style={deskBar}>
            <h1 style={pageTitle}>{known.label}</h1>
            {avatar(48)}
          </header>
          <main style={{ padding: '8px 40px 48px', maxWidth: 720 }}>{content}</main>
        </div>
      </div>
    );
  }
  return (
    <div style={homePage} data-step="settings" data-layout="phone">
      <header style={phoneBar}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, minWidth: 0 }}>
          <MenuButton onClick={() => setDrawer(true)} />
          <h1 style={pageTitle}>{known.label}</h1>
        </div>
        {avatar(40)}
      </header>
      <NavDrawer open={drawer} onClose={closeDrawer} />
      <main style={{ padding: '0 16px calc(env(safe-area-inset-bottom) + 24px)' }}>{content}</main>
    </div>
  );
}

function HomeScreenSection({ admin, wide }: { admin: boolean; wide: boolean }) {
  return (
    <>
      <LibrariesEditor />
      {admin && wide && <KioskLibraryPicker />}
    </>
  );
}

/** Desktop, owner/admin: Kiosk > Backdrop library (the web slideshow's movie library). */
export function KioskLibraryPicker() {
  const [data, setData] = useState<KioskLibraries | null>(null);
  const [open, setOpen] = useState(false);
  const [toast, setToast] = useState(0);
  const seq = useRef(0);
  useEffect(() => { fetchKioskLibrary().then(setData).catch(() => setData(null)); }, []);
  if (!data || data.libraries.length === 0) return null;
  const current = data.libraries.find(l => l.id === data.id);
  const choose = (id: string) => {
    setOpen(false);
    if (id === data.id) return;
    const before = data;
    setData({ ...data, id });
    const n = ++seq.current;
    saveKioskLibrary(id)
      .then(d => { if (n === seq.current) setData(d); })
      .catch(() => { if (n === seq.current) { setData(before); setToast(t => t + 1); } });
  };
  return (
    <section data-testid="kiosk-library">
      {toast > 0 && <NoticeToast key={toast} text={SAVE_FAILED} onDone={() => setToast(0)} />}
      <h2 style={sectionTitle}>Kiosk</h2>
      <div style={card}>
        <button type="button" style={pickerRow} aria-expanded={open} onClick={() => setOpen(o => !o)}>
          <span style={{ ...nameStyle, flex: 1 }}>Backdrop library</span>
          <span style={{ color: 'rgba(255,255,255,0.6)', fontSize: 15 }} data-testid="backdrop-value">{current?.title || ''}</span>
          <span style={{ display: 'inline-flex', color: 'rgba(255,255,255,0.5)', transition: 'transform 150ms', transform: open ? 'rotate(90deg)' : 'none' }}>
            <ChevronIcon />
          </span>
        </button>
        {open && (
          <div role="radiogroup" aria-label="Backdrop library">
            {data.libraries.map(l => {
              const on = l.id === data.id;
              return (
                <button key={l.id} type="button" role="radio" aria-checked={on} onClick={() => choose(l.id)}
                  style={{ ...pickerRow, borderTop: '1px solid rgba(255,255,255,0.06)', paddingLeft: 28 }}>
                  <span style={{ ...nameStyle, flex: 1 }}>{l.title}</span>
                  <span style={{ display: 'inline-flex', color: LUMA_BLUE, width: 20 }}>{on && <CheckIcon />}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}

const ROW_H = 56;

export function LibrariesEditor() {
  const [libs, setLibs] = useState<Library[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [toast, setToast] = useState(0);
  const [drag, setDrag] = useState<{ id: string; from: number; startY: number; dy: number } | null>(null);
  const seq = useRef(0);
  const handles = useRef(new Map<string, HTMLButtonElement>());
  const refocus = useRef<string | null>(null);

  useEffect(() => {
    fetchLibraries().then(setLibs).catch(() => setLoadFailed(true));
  }, []);
  useEffect(() => {
    if (refocus.current) { handles.current.get(refocus.current)?.focus(); refocus.current = null; }
  });

  const commit = (next: Library[]) => {
    const before = libs;
    setLibs(next);
    const n = ++seq.current;
    saveLibraries(next)
      .then(saved => { if (n === seq.current) setLibs(saved); })
      .catch(() => { if (n === seq.current) { setLibs(before); setToast(t => t + 1); } });
  };

  const target = (d: NonNullable<typeof drag>, count: number) =>
    Math.max(0, Math.min(count - 1, d.from + Math.round(d.dy / ROW_H)));

  const shiftFor = (i: number, count: number) => {
    if (!drag) return 0;
    const t = target(drag, count);
    if (drag.from < t && i > drag.from && i <= t) return -ROW_H;
    if (drag.from > t && i >= t && i < drag.from) return ROW_H;
    return 0;
  };

  return (
    <section data-testid="home-screen-section">
      {toast > 0 && <NoticeToast key={toast} text={SAVE_FAILED} onDone={() => setToast(0)} />}
      <h2 style={sectionTitle}>Libraries</h2>
      {libs === null ? (
        loadFailed ? (
          <div style={{ ...card, ...emptyRow }} role="alert"><span style={muted}>{"Couldn't load libraries"}</span></div>
        ) : (
          <div data-testid="libraries-skeleton" style={card}>
            {[0, 1, 2].map(i => <div key={i} className="luma-shimmer" style={{ height: ROW_H }} />)}
          </div>
        )
      ) : libs.length === 0 ? (
        <div data-testid="libraries-empty" style={{ ...card, ...emptyRow }}><span style={muted}>No libraries</span></div>
      ) : (
        <ul style={{ ...card, listStyle: 'none', margin: 0, padding: 0, position: 'relative' }} data-testid="libraries">
          {libs.map((l, i) => {
            const lifted = drag?.id === l.id;
            const y = lifted ? drag!.dy : shiftFor(i, libs.length);
            return (
              <li key={l.id} data-testid="library-row" data-lifted={lifted ? 'true' : undefined}
                style={{
                  ...row,
                  transform: `translateY(${y}px)${lifted ? ' scale(1.02)' : ''}`,
                  transition: lifted ? 'none' : 'transform 150ms',
                  ...(lifted ? liftedRow : null),
                }}>
                <button
                  type="button" aria-label={`Reorder ${l.title}`} style={handle}
                  ref={el => { if (el) handles.current.set(l.id, el); else handles.current.delete(l.id); }}
                  onPointerDown={e => {
                    e.currentTarget.setPointerCapture?.(e.pointerId);
                    setDrag({ id: l.id, from: i, startY: e.clientY, dy: 0 });
                    try { navigator.vibrate?.(10); } catch { /* unsupported */ }
                  }}
                  onPointerMove={e => { if (drag?.id === l.id) setDrag({ ...drag, dy: e.clientY - drag.startY }); }}
                  onPointerUp={() => {
                    if (drag?.id !== l.id) return;
                    const t = target(drag, libs.length);
                    setDrag(null);
                    if (t !== drag.from) commit(move(libs, drag.from, t));
                  }}
                  onPointerCancel={() => setDrag(null)}
                  onKeyDown={e => {
                    const to = e.key === 'ArrowUp' ? i - 1 : e.key === 'ArrowDown' ? i + 1 : null;
                    if (to === null) return;
                    e.preventDefault();
                    if (to < 0 || to >= libs.length) return;
                    refocus.current = l.id;
                    commit(move(libs, i, to));
                  }}
                >
                  <GripIcon />
                </button>
                <div style={{ ...rowBody, borderBottom: i < libs.length - 1 ? '1px solid rgba(255,255,255,0.06)' : 'none' }}>
                  <span style={{ ...nameStyle, color: l.enabled ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.5)' }}>{l.title}</span>
                  <button
                    type="button" role="switch" aria-checked={l.enabled} aria-label={l.title}
                    onClick={() => commit(libs.map(x => (x.id === l.id ? { ...x, enabled: !x.enabled } : x)))}
                    style={switchHit}
                  >
                    <span data-track style={{ ...track, background: l.enabled ? LUMA_BLUE : 'rgba(255,255,255,0.2)' }}>
                      <span style={{ ...knob, transform: l.enabled ? 'translateX(20px)' : 'none' }} />
                    </span>
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

const deskBar: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: 72, padding: '0 40px',
};
const pageTitle: React.CSSProperties = {
  margin: 0, fontSize: 17, fontWeight: 600, fontFamily: FONT, color: 'rgba(255,255,255,0.95)',
  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
};
const sectionTitle: React.CSSProperties = {
  margin: '24px 4px 8px', fontSize: 13, fontWeight: 600, color: 'rgba(255,255,255,0.5)', fontFamily: FONT,
};
const card: React.CSSProperties = {
  borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)',
  overflow: 'hidden',
};
const emptyRow: React.CSSProperties = { height: ROW_H, display: 'flex', alignItems: 'center', justifyContent: 'center' };
const muted: React.CSSProperties = { color: 'rgba(255,255,255,0.5)', fontSize: 15, fontFamily: FONT };
const row: React.CSSProperties = {
  position: 'relative', display: 'flex', alignItems: 'center', height: ROW_H, padding: '0 12px 0 4px',
  boxSizing: 'border-box', background: 'transparent', touchAction: 'pan-y',
};
const liftedRow: React.CSSProperties = {
  background: 'rgba(59,130,246,0.14)', boxShadow: '0 8px 24px rgba(0,0,0,0.4)', zIndex: 2, borderRadius: 12,
};
const rowBody: React.CSSProperties = {
  flex: 1, minWidth: 0, height: '100%', display: 'flex', alignItems: 'center', gap: 12,
};
const nameStyle: React.CSSProperties = {
  flex: 1, minWidth: 0, fontSize: 16, fontWeight: 500, fontFamily: FONT, color: 'rgba(255,255,255,0.95)',
  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', textAlign: 'left',
};
const handle: React.CSSProperties = {
  width: 44, height: 44, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none',
  border: 'none', background: 'none', color: 'rgba(255,255,255,0.5)', cursor: 'grab', padding: 0,
  touchAction: 'none', borderRadius: 10,
};
const switchHit: React.CSSProperties = {
  width: 59, height: 44, display: 'inline-flex', alignItems: 'center', justifyContent: 'flex-end',
  border: 'none', background: 'none', cursor: 'pointer', padding: 0, flex: 'none',
};
const track: React.CSSProperties = {
  position: 'relative', display: 'block', width: 51, height: 31, borderRadius: 16, transition: 'background .2s',
};
const knob: React.CSSProperties = {
  position: 'absolute', top: 2, left: 2, width: 27, height: 27, borderRadius: 14, background: '#fff',
  transition: 'transform .2s', boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
};
const pickerRow: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 12, width: '100%', height: ROW_H, padding: '0 16px', border: 'none',
  background: 'none', cursor: 'pointer', fontFamily: FONT, boxSizing: 'border-box',
};

/** @deprecated name kept for imports: the page is Settings now. */
export const LibrariesPage = SettingsPage;
