/** Settings (/settings/<section>), docs/design/settings-page-spec.md and
 * docs/design/mobile-settings-spec.md (row behavior).
 * Desktop (>=1024px): a full page under the app header (logo back to Home,
 * avatar): a left section list and the section on the right. Home screen
 * shows only Kiosk > Backdrop library, Hero source and count (owner/admin).
 * Phone: a full-screen sheet (X close, section list); a section pushes in
 * with a back chevron. Home screen shows only Libraries (drag to reorder,
 * switch on/off, instant save). */
import { useEffect, useRef, useState } from 'react';
import { Link, Navigate, useLocation, useNavigate, useParams, useSearchParams } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { FONT, GlowLayer, LOGO_SRC, NAVY_BG, glowPage } from '@/components/start/StartUI';
import { assetUrl } from '@/urlHelpers';
import { LUMA_BLUE } from '@/ui/lumaButton';
import {
  fetchKioskLibrary, fetchLibraries, move, saveKioskLibrary, saveLibraries,
  type KioskLibraries, type Library,
} from '@/components/home/homeApi';
import { homePage } from '@/pages/MinimalHomePage';
import { useBodyScroll } from '@/hooks/useBodyScroll';
import { useKioskWidth } from '@/components/home/KioskHome';
import { CheckIcon, ChevronIcon, ChevronLeftIcon, ChevronUpIcon, GripIcon, XIcon } from '@/components/nav/AppNav';
import { UserMenu } from '@/components/nav/UserMenu';
import { NoticeToast } from '@/components/start/SetupToast';
import { usePlexOwner } from '@/components/home/PlexSettings';
import { ConnectorsSettings } from '@/components/home/ConnectorsSettings';
import { NotificationsSettings } from '@/components/home/NotificationsSettings';
import { TrackersSettings, useTrackersAccess } from '@/components/trackers/TrackersSettings';
import { LibraryPathsSettings } from '@/components/library/LibraryPathsSettings';
import { SectionTrackersList } from '@/components/settings/SectionTrackersList';
import { TvLibrarySettings } from '@/components/library/TvLibrarySettings';
import { MoviesSettings } from '@/components/movies/MoviesSettings';
import { GenreRouletteSettings } from '@/components/automation/GenreRouletteSettings';
import { AnticipatedSettings } from '@/components/automation/AnticipatedSettings';
import { WhatsMissingSettings } from '@/components/automation/WhatsMissingSettings';
import { DownloadsGuardSettings } from '@/components/automation/DownloadsGuardSettings';
import { TrendingTvSettings } from '@/components/automation/TrendingTvSettings';
import { RoomsSettings } from '@/components/rooms/RoomsSettings';
import { isMinimal } from '@/minimal';
import { HeroSettingsRows } from '@/components/home/HeroSettings';
import { SAVE_FAILED } from '@/utils/messages';

export const SETTINGS_SECTIONS = [
  { slug: 'home-screen', label: 'Home screen' },
  { slug: 'rooms', label: 'Rooms' },   // v224: owner only (Option A)
  { slug: 'connectors', label: 'Connectors' },   // owner only
  { slug: 'notifications', label: 'Notifications' },   // v280: owner only, Web Push (prod Services)
  { slug: 'trackers', label: 'Trackers' },   // owner with a session only
  { slug: 'library-paths', label: 'Library paths' },   // owner only
  { slug: 'movies', label: 'Movies' },   // v200: owner only, luma-test builds
  { slug: 'tv', label: 'TV' },   // owner only, luma-test builds
  { slug: 'automation', label: 'Automation' },   // v252: owner only, luma-test builds (Genre Roulette)
] as const;
const DEFAULT_SECTION = SETTINGS_SECTIONS[0].slug;
export { SAVE_FAILED };

/** /settings/libraries is now /settings/home-screen. */
export function LibrariesRedirect() {
  return <Navigate to={`/settings/${DEFAULT_SECTION}`} replace />;
}

export function SettingsPage() {
  const { authenticated, loading, user, implicit, logout } = useAuth();
  const { section } = useParams();
  const wide = useKioskWidth();
  const navigate = useNavigate();
  const { hash } = useLocation();
  const [query] = useSearchParams();
  const [busy, setBusy] = useState(false);
  const owner = usePlexOwner(!!user?.is_admin);
  // Trackers: the API serves only the owner signed in with a session (never the implicit LAN owner).
  const trackersOk = useTrackersAccess(!!user?.is_admin && !implicit && authenticated);
  useBodyScroll();
  if (loading) return null;
  if (!authenticated) return <Navigate to="/login" replace />;
  // Premium pass: Plex lives under Connectors (Plex sign-in still returns to /settings/plex).
  if (section === 'plex') return <Navigate to="/settings/connectors#plex" replace />;
  const known = SETTINGS_SECTIONS.find(s => s.slug === section);
  if (section && !known) return <Navigate to={`/settings/${DEFAULT_SECTION}`} replace />;
  const ownerOnly = (slug: string) => slug === 'connectors' || slug === 'notifications' || slug === 'library-paths' || slug === 'tv' || slug === 'movies' || slug === 'rooms' || slug === 'automation';
  if (known && ownerOnly(known.slug) && owner === false) return <Navigate to={`/settings/${DEFAULT_SECTION}`} replace />;
  if (known?.slug === 'trackers' && trackersOk === false) return <Navigate to={`/settings/${DEFAULT_SECTION}`} replace />;
  // Library paths: the API exists only on minimal (luma-test) builds.
  if ((known?.slug === 'library-paths' || known?.slug === 'tv' || known?.slug === 'movies' || known?.slug === 'automation') && !isMinimal()) return <Navigate to={`/settings/${DEFAULT_SECTION}`} replace />;
  const admin = !!user?.is_admin;
  const sections = SETTINGS_SECTIONS.filter(s => (s.slug === 'trackers' ? trackersOk
    : s.slug === 'library-paths' || s.slug === 'tv' || s.slug === 'movies' || s.slug === 'automation' ? owner && isMinimal() : !ownerOnly(s.slug) || owner));
  const content = (slug: string) => slug === 'connectors'
      ? (owner ? <ConnectorsSettings initial={hash === '#plex' ? 'plex' : undefined} onInitialClosed={() => navigate('/settings/connectors', { replace: true })} /> : null)
      : slug === 'notifications'
        ? (owner ? <NotificationsSettings /> : null)
      : slug === 'trackers'
        ? (trackersOk ? <TrackersSettings wide={wide} onExit={() => navigate('/settings')} /> : null)
        : slug === 'library-paths'
          ? (owner ? <LibraryPathsSettings wide={wide} /> : null)
          : slug === 'movies'
            ? (owner ? <MoviesSettings wide={wide} /> : null)
          : slug === 'automation'
            ? (owner ? <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}><GenreRouletteSettings wide={wide} /><AnticipatedSettings wide={wide} /><TrendingTvSettings wide={wide} /><WhatsMissingSettings wide={wide} /><DownloadsGuardSettings wide={wide} /></div> : null)
          : slug === 'rooms'
            ? (owner ? <RoomsSettings wide={wide} onExit={() => navigate('/settings')} /> : null)
          : slug === 'tv'
            ? (owner ? <TvLibrarySettings wide={wide} options={<SectionTrackersList section="tv" label="Trackers" wide={wide} />} /> : null)
            : <HomeScreenSection admin={admin} wide={wide} />;
  // Trackers draws its own title row (desktop) and header (phone: Plus, pushed pages); TV its title row (desktop).
  const ownChrome = known?.slug === 'trackers' || known?.slug === 'rooms' || (wide && known?.slug === 'tv');

  if (wide) {
    if (!known) return <Navigate to={`/settings/${DEFAULT_SECTION}`} replace />;
    const signOut = async () => { setBusy(true); await logout(); navigate('/login', { replace: true }); };
    // v325 (Taylor): TV Calendar is its own full-width view, no Settings nav; the up arrow (top-left, in the
    // logo's place) closes it to Home. Another TV tab (?view=...) or a show page is the normal Settings layout.
    const calendar = known.slug === 'tv' && (query.get('view') ?? 'calendar') === 'calendar' && !query.get('show');
    return (
      <div style={settingsPage} data-step="settings" data-layout="desktop">
        <GlowLayer />
        <style>{css}</style>
        <header style={deskHeader}>
          {calendar ? (
            <span style={{ display: 'inline-flex', alignItems: 'center', height: 64 }}>
              <style>{'.luma-icon-btn:hover>span{background:rgba(255,255,255,0.16)}'}</style>
              <button type="button" aria-label="Close calendar" data-testid="tv-calendar-close" onClick={() => navigate('/')}
                style={{ ...circleHit, width: 48, height: 48, borderRadius: 24, marginLeft: -6 }} className="luma-focus luma-icon-btn">
                <span style={{ ...circle, width: 40, height: 40, borderRadius: 20 }}><ChevronUpIcon size={20} /></span>
              </button>
            </span>
          ) : (
            <Link to="/" aria-label="Home" style={{ display: 'inline-flex', borderRadius: 12 }} className="luma-focus">
              <img src={assetUrl(LOGO_SRC)} alt="Luma" data-testid="settings-logo" style={{ height: 64, width: 'auto' }} draggable={false} />
            </Link>
          )}
          {!implicit
            ? <UserMenu name={user?.name || ''} admin={admin} busy={busy} onSignOut={signOut} size={48} />
            : <span style={{ width: 48 }} />}
        </header>
        <div style={deskPage} data-view={calendar ? 'tv-calendar' : undefined}>
          {!calendar && <nav aria-label="Settings" data-testid="settings-nav" style={{ width: NAV_W, flex: 'none' }}>
            <h1 style={navHeading}>Settings</h1>
            <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
              {sections.map(s => {
                const on = s.slug === known.slug;
                return (
                  <li key={s.slug}>
                    <Link to={`/settings/${s.slug}`} className="luma-section-item" aria-current={on ? 'page' : undefined}
                      style={{ ...navItem, ...(on ? navItemOn : null) }}>
                      {on && <span style={bar} aria-hidden="true" />}
                      {s.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>}
          <main data-testid="settings-main" style={{ flex: 1, minWidth: 0, maxWidth: known.slug === 'tv' || known.slug === 'rooms' ? undefined : known.slug === 'automation' ? 1120 : 720 }}>
            {!ownChrome && <h2 style={deskTitle}>{known.label}</h2>}
            {content(known.slug)}
          </main>
        </div>
      </div>
    );
  }

  // Phone: the Settings sheet; /settings lists the sections, a section pushes in.
  // v276 (Taylor): match Home's full-bleed bottom — in-flow minHeight 100dvh + document
  // scroll (useBodyScroll). Fixed overflow-y:auto sheets clip at the iOS home indicator
  // (see useBodyScroll.ts); v266 luma-fullscreen height mocks passed, real phone did not.
  return (
    <div style={sheetPage} data-step="settings" data-layout="phone">
      <style>{css}</style>
      <div role="dialog" aria-modal="true" aria-label="Settings" data-testid="settings-sheet" className="luma-sheet" style={sheet}>
        {!known ? (
          <div key="list" data-scroll-content style={{ paddingBottom: SHEET_SCROLL_PAD }}>
            <SheetHeader title="Settings" onClose={() => navigate('/')} />
            <ul style={{ listStyle: 'none', margin: '16px 0 0', padding: 0 }} data-testid="settings-list">
              {sections.map((s, i) => (
                <li key={s.slug}>
                  <Link to={`/settings/${s.slug}`} className="luma-sheet-row" style={sheetRow}>
                    <span style={{ flex: 1 }}>{s.label}</span>
                    <span style={{ display: 'inline-flex', color: 'rgba(255,255,255,0.3)' }}><ChevronIcon size={18} /></span>
                    {i < sections.length - 1 && <span style={divider} aria-hidden="true" />}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <div key={known.slug} className="luma-push" data-testid="settings-section">
            {!ownChrome && <SheetHeader title={known.label} onBack={() => navigate('/settings')} onClose={() => navigate('/')} />}
            <div data-scroll-content style={{ paddingBottom: SHEET_SCROLL_PAD }}>{content(known.slug)}</div>
          </div>
        )}
      </div>
    </div>
  );
}

/** 56px sheet header: a circular X (or back chevron) left, centered title.
 * Swipe down on the header closes the sheet. */
function SheetHeader({ title, onBack, onClose }: { title: string; onBack?: () => void; onClose: () => void }) {
  const startY = useRef<number | null>(null);
  return (
    <header style={sheetHeader}
      onPointerDown={e => { startY.current = e.clientY; }}
      onPointerUp={e => { if (startY.current !== null && e.clientY - startY.current > 80) onClose(); startY.current = null; }}>
      <button type="button" aria-label={onBack ? 'Back' : 'Close'} onClick={onBack || onClose} style={circleHit} className="luma-focus">
        <span style={circle}>{onBack ? <ChevronLeftIcon size={18} /> : <XIcon size={18} />}</span>
      </button>
      <h1 style={sheetTitle}>{title}</h1>
    </header>
  );
}

/** Desktop (>=1024px): only the kiosk's Backdrop library (owner/admin).
 * Phone: only the Libraries list. */
function HomeScreenSection({ admin, wide }: { admin: boolean; wide: boolean }) {
  if (wide) return admin ? <KioskLibraryPicker /> : null;
  return <LibrariesEditor />;
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
        <div style={{ borderTop: '1px solid rgba(255,255,255,0.06)' }}><HeroSettingsRows /></div>
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
      <h2 style={groupHeader}>Libraries</h2>
      {libs === null ? (
        loadFailed ? (
          <div style={emptyRow} role="alert"><span style={muted}>{"Couldn't load libraries"}</span></div>
        ) : (
          <div data-testid="libraries-skeleton">
            {[0, 1, 2].map(i => <div key={i} className="luma-shimmer" style={{ height: ROW_H, margin: '0 20px 1px', borderRadius: 8 }} />)}
          </div>
        )
      ) : libs.length === 0 ? (
        <div data-testid="libraries-empty" style={emptyRow}><span style={muted}>No libraries</span></div>
      ) : (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, position: 'relative' }} data-testid="libraries">
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
                {i < libs.length - 1 && !lifted && <span style={divider} aria-hidden="true" />}
                <div style={rowBody}>
                  <span style={{ ...libName, color: l.enabled ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.5)' }}>{l.title}</span>
                  <button
                    type="button" role="switch" aria-checked={l.enabled} aria-label={l.title}
                    onClick={() => commit(libs.map(x => (x.id === l.id ? { ...x, enabled: !x.enabled } : x)))}
                    style={switchHit}
                  >
                    <span data-track style={{ ...track, background: l.enabled ? LUMA_BLUE : 'rgba(255,255,255,0.18)' }}>
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

const css = `
.luma-section-item:hover{background:rgba(255,255,255,.06)}
.luma-section-item[aria-current="page"]:hover{background:rgba(59,130,246,.14)}
.luma-sheet-row:active{background:rgba(255,255,255,.06)}
.luma-section-item:focus-visible,.luma-sheet-row:focus-visible,.luma-focus:focus-visible{outline:2px solid #60a5fa;outline-offset:-2px}
.luma-sheet{animation:luma-sheet-up 300ms cubic-bezier(.2,.8,.2,1)}
.luma-push{animation:luma-push-in 260ms cubic-bezier(.2,.8,.2,1)}
@keyframes luma-sheet-up{from{transform:translateY(100%)}to{transform:none}}
@keyframes luma-push-in{from{transform:translateX(100%)}to{transform:none}}
@keyframes luma-fade-in{from{opacity:0}to{opacity:1}}
@media (prefers-reduced-motion: reduce){.luma-sheet,.luma-push{animation:luma-fade-in 200ms}}
`;
/** Desktop Settings (Designer): the 'Settings' heading and the nav column sit
 * directly under the Luma logo, on its left edge and HEADING_GAP below its
 * bottom; the content column follows at the same nav-to-content gap and takes
 * the freed width (TV uses all of it). */
export const LOGO_LEFT = 44;
export const HEADING_GAP = 16;
export const NAV_W = 220;
export const NAV_GAP = 40;
const deskHeader: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: `20px 60px 0 ${LOGO_LEFT}px`,
};
const deskPage: React.CSSProperties = {
  display: 'flex', gap: NAV_GAP, padding: `${HEADING_GAP}px 60px 32px ${LOGO_LEFT}px`, boxSizing: 'border-box',
};
const navHeading: React.CSSProperties = {
  margin: '0 0 20px', fontSize: 24, fontWeight: 600, fontFamily: FONT, color: 'rgba(255,255,255,0.95)',
};
const navItem: React.CSSProperties = {
  position: 'relative', display: 'flex', alignItems: 'center', height: 40, padding: '0 12px', borderRadius: 10,
  fontSize: 15, fontWeight: 500, fontFamily: FONT, color: 'rgba(255,255,255,0.7)', textDecoration: 'none',
};
const navItemOn: React.CSSProperties = { background: 'rgba(59,130,246,0.14)', color: 'rgba(255,255,255,0.95)' };
const bar: React.CSSProperties = {
  position: 'absolute', left: 0, top: 8, bottom: 8, width: 3, borderRadius: 2, background: LUMA_BLUE,
};
const deskTitle: React.CSSProperties = {
  margin: '0 0 8px', fontSize: 20, fontWeight: 600, fontFamily: FONT, color: 'rgba(255,255,255,0.95)', lineHeight: '40px',
};
/** v276 (Taylor): copy Home's phone shell (MinimalHomePage `page`) — in-flow minHeight
 * 100dvh, flat kiosk-black, document scroll. Do not use position:fixed or overflowY:auto
 * on the page/sheet (iOS clips fixed overflow at the home indicator → dark band).
 * Do not spread homePage (backgroundAttachment:fixed seam). SHEET_SCROLL_PAD stays in
 * scroll content only; never on the wrapper. */
const sheetPage: React.CSSProperties = {
  minHeight: '100dvh',
  position: 'relative',
  background: 'var(--color-kiosk-black)',
  color: '#fff',
  fontFamily: FONT,
  WebkitFontSmoothing: 'antialiased',
};
/** Bottom padding of the sheet's scroll content: the reach past the edge, the home indicator inset, and 24px. */
export const SHEET_SCROLL_PAD = 'calc(var(--luma-end-pad) + 24px)';
const sheet: React.CSSProperties = {
  // In-flow under the status bar; background runs to the page bottom (Home match).
  marginTop: 'calc(env(safe-area-inset-top) + 8px)',
  minHeight: 'calc(100dvh - env(safe-area-inset-top) - 8px)',
  // App navy token (#15102a = --color-kiosk-black) + white 4% + the StartUI navy glow at top.
  backgroundColor: 'var(--color-kiosk-black)',
  backgroundImage: `linear-gradient(rgba(255,255,255,0.04), rgba(255,255,255,0.04)), ${NAVY_BG}`,
  borderRadius: '20px 20px 0 0',
  fontFamily: FONT, color: '#fff',
};
const sheetHeader: React.CSSProperties = {
  position: 'relative', height: 56, display: 'flex', alignItems: 'center', padding: '0 12px', touchAction: 'pan-x',
};
const circleHit: React.CSSProperties = {
  width: 44, height: 44, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: 0,
  border: 'none', background: 'none', cursor: 'pointer', borderRadius: 22, position: 'relative', zIndex: 1, color: 'rgba(255,255,255,0.9)',
};
const circle: React.CSSProperties = {
  width: 36, height: 36, borderRadius: 18, background: 'rgba(255,255,255,0.10)',
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
};
const sheetTitle: React.CSSProperties = {
  position: 'absolute', left: 64, right: 64, margin: 0, textAlign: 'center', fontSize: 17, fontWeight: 600,
  fontFamily: FONT, color: 'rgba(255,255,255,0.95)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
};
const sheetRow: React.CSSProperties = {
  position: 'relative', display: 'flex', alignItems: 'center', height: 56, padding: '0 20px', boxSizing: 'border-box',
  fontSize: 17, fontWeight: 400, fontFamily: FONT, color: 'rgba(255,255,255,0.95)', textDecoration: 'none',
};
const divider: React.CSSProperties = {
  position: 'absolute', left: 20, right: 20, bottom: 0, height: 1, background: 'rgba(255,255,255,0.08)',
};
const groupHeader: React.CSSProperties = {
  margin: 0, padding: '24px 20px 8px', fontSize: 20, fontWeight: 700, fontFamily: FONT, color: 'rgba(255,255,255,0.95)',
};
const libName: React.CSSProperties = {
  flex: 1, minWidth: 0, fontSize: 17, fontWeight: 400, fontFamily: FONT,
  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', textAlign: 'left',
};
const sectionTitle: React.CSSProperties = {
  margin: '24px 4px 8px', fontSize: 13, fontWeight: 600, color: 'rgba(255,255,255,0.5)', fontFamily: FONT,
};
const card: React.CSSProperties = {
  borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)',
  overflow: 'hidden',
};
const emptyRow: React.CSSProperties = { height: ROW_H, display: 'flex', alignItems: 'center', padding: '0 20px' };
const muted: React.CSSProperties = { color: 'rgba(255,255,255,0.5)', fontSize: 17, fontFamily: FONT };
const row: React.CSSProperties = {
  position: 'relative', display: 'flex', alignItems: 'center', height: ROW_H, padding: '0 20px 0 8px',
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
  border: 'none', background: 'none', color: 'rgba(255,255,255,0.4)', cursor: 'grab', padding: 0,
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

/** Desktop settings: flat navy under one fixed glow layer (no seam on long
 * sections such as Library paths and TV). */
export const settingsPage: React.CSSProperties = { ...homePage, backgroundAttachment: undefined, ...glowPage };
