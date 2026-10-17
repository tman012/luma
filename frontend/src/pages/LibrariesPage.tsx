/** Settings (/settings/<section>). Web: a left nav beside the selected
 * section (Home screen by default). Phone: a section list that drills into a
 * section, with a back control. Home screen: which Plex libraries get a Home
 * rail and in what order, plus (owner/admin) the web kiosk's library. */
import { useEffect, useRef, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { FONT } from '@/components/start/StartUI';
import { LUMA_BLUE } from '@/ui/lumaButton';
import { railCss } from '@/components/home/Rails';
import {
  fetchKioskLibrary, fetchLibraries, move, saveKioskLibrary, saveLibraries,
  type KioskLibraries, type Library,
} from '@/components/home/homeApi';
import { homeBar, homePage, iconBtn } from '@/pages/MinimalHomePage';
import { useBodyScroll } from '@/hooks/useBodyScroll';
import { useKioskWidth } from '@/components/home/KioskHome';

export const SETTINGS_SECTIONS = [{ slug: 'home-screen', label: 'Home screen' }] as const;
const DEFAULT_SECTION = SETTINGS_SECTIONS[0].slug;

/** /settings/libraries is now /settings/home-screen. */
export function LibrariesRedirect() {
  return <Navigate to={`/settings/${DEFAULT_SECTION}`} replace />;
}

export function SettingsPage() {
  const { authenticated, loading, user } = useAuth();
  const { section } = useParams();
  const wide = useKioskWidth();
  useBodyScroll();
  if (loading) return null;
  if (!authenticated) return <Navigate to="/login" replace />;
  const known = SETTINGS_SECTIONS.find(s => s.slug === section);
  if (section && !known) return <Navigate to="/settings" replace />;
  // Web opens on Home screen; phones show the section list first.
  if (!section && wide) return <Navigate to={`/settings/${DEFAULT_SECTION}`} replace />;
  const admin = !!user?.is_admin;

  if (!wide) {
    if (!known) {
      return (
        <Shell back="/" title="Settings">
          <ul style={list} data-testid="settings-sections">
            {SETTINGS_SECTIONS.map(s => (
              <li key={s.slug}>
                <Link to={`/settings/${s.slug}`} style={navRow}>
                  <span style={name}>{s.label}</span>
                  <Chevron />
                </Link>
              </li>
            ))}
          </ul>
        </Shell>
      );
    }
    return (
      <Shell back="/settings" backLabel="Settings" title={known.label}>
        <HomeScreenSection admin={admin} />
      </Shell>
    );
  }

  return (
    <Shell back="/" title="Settings" wide>
      <div style={{ display: 'flex', gap: 32, alignItems: 'flex-start' }}>
        <nav aria-label="Settings" style={sideNav} data-testid="settings-nav">
          {SETTINGS_SECTIONS.map(s => (
            <Link key={s.slug} to={`/settings/${s.slug}`} aria-current={s.slug === known?.slug ? 'page' : undefined}
              style={{ ...navItem, ...(s.slug === known?.slug ? navItemOn : null) }}>
              {s.label}
            </Link>
          ))}
        </nav>
        <div style={{ flex: 1, minWidth: 0, maxWidth: 560 }}>
          <HomeScreenSection admin={admin} />
        </div>
      </div>
    </Shell>
  );
}

function Shell({ back, backLabel = 'Back', title, wide, children }: {
  back: string; backLabel?: string; title: string; wide?: boolean; children: React.ReactNode;
}) {
  return (
    <div style={homePage} data-step="settings">
      <style>{railCss}</style>
      <header style={homeBar}>
        <Link to={back} aria-label={backLabel} style={iconBtn}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M15 18l-6-6 6-6" /></svg>
        </Link>
        <h1 style={title_}>{title}</h1>
        <span style={{ width: 40 }} />
      </header>
      <main style={{ padding: '24px 20px calc(env(safe-area-inset-bottom) + 32px)', maxWidth: wide ? 880 : 560, margin: '0 auto' }}>
        {children}
      </main>
    </div>
  );
}

function Chevron() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.5)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>
  );
}

function HomeScreenSection({ admin }: { admin: boolean }) {
  return (
    <>
      <LibrariesEditor />
      {admin && <KioskLibraryPicker />}
    </>
  );
}

/** Owner/admin: the movie library the web kiosk slideshow shows. */
export function KioskLibraryPicker() {
  const [data, setData] = useState<KioskLibraries | null>(null);
  const [error, setError] = useState('');
  const seq = useRef(0);
  useEffect(() => {
    fetchKioskLibrary().then(setData).catch(() => setError("Couldn't load libraries"));
  }, []);
  const choose = (id: string) => {
    if (!data || id === data.id) return;
    const before = data;
    setData({ ...data, id });
    setError('');
    const n = ++seq.current;
    saveKioskLibrary(id)
      .then(d => { if (n === seq.current) setData(d); })
      .catch(() => { if (n === seq.current) { setData(before); setError("Couldn't save"); } });
  };
  if (!error && data && data.libraries.length === 0) return null;
  return (
    <section style={{ marginTop: 32 }} data-testid="kiosk-library">
      <h2 style={sectionTitle} id="kiosk-library-title">Kiosk</h2>
      {error && <p role="alert" style={{ color: '#ff8a8a', fontSize: 14, margin: '0 0 12px' }}>{error}</p>}
      {data === null ? (
        !error && <div style={list}><div className="luma-shimmer" style={{ height: 60 }} /></div>
      ) : (
        <div role="radiogroup" aria-labelledby="kiosk-library-title" style={list}>
          {data.libraries.map(l => {
            const on = l.id === data.id;
            return (
              <button key={l.id} type="button" role="radio" aria-checked={on} onClick={() => choose(l.id)} style={pickRow}>
                <span style={name}>{l.title}</span>
                <span aria-hidden="true" style={{ ...radio, ...(on ? radioOn : null) }}>
                  {on && <span style={radioDot} />}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

export function LibrariesEditor() {
  const [libs, setLibs] = useState<Library[] | null>(null);
  const [error, setError] = useState('');
  const seq = useRef(0);

  useEffect(() => {
    fetchLibraries().then(setLibs).catch(() => setError("Couldn't load libraries"));
  }, []);

  const commit = (next: Library[]) => {
    setLibs(next);
    setError('');
    const n = ++seq.current;
    saveLibraries(next)
      .then(saved => { if (n === seq.current) setLibs(saved); })
      .catch(() => { if (n === seq.current) setError("Couldn't save"); });
  };

  return (
    <section data-testid="home-screen-section">
        <h2 style={sectionTitle}>Libraries</h2>
        {error && <p role="alert" style={{ color: '#ff8a8a', fontSize: 14, margin: '0 0 12px' }}>{error}</p>}
        {libs === null ? (
          <div data-testid="libraries-skeleton" style={list}>
            {[0, 1, 2].map(i => <div key={i} className="luma-shimmer" style={{ height: 60 }} />)}
          </div>
        ) : libs.length === 0 ? (
          <div data-testid="libraries-empty" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 'calc(100dvh - 200px)' }}>
            <p style={{ margin: 0, color: 'rgba(255,255,255,0.6)', fontSize: 15 }}>Nothing here yet</p>
          </div>
        ) : (
          <ul style={list} data-testid="libraries">
            {libs.map((l, i) => (
              <li key={l.id} style={row}>
                <div style={{ display: 'flex', gap: 4, flex: 'none' }}>
                  <button type="button" aria-label={`Move ${l.title} up`} disabled={i === 0}
                    onClick={() => commit(move(libs, i, i - 1))} style={arrow}>▲</button>
                  <button type="button" aria-label={`Move ${l.title} down`} disabled={i === libs.length - 1}
                    onClick={() => commit(move(libs, i, i + 1))} style={arrow}>▼</button>
                </div>
                <span style={name}>{l.title}</span>
                <button
                  type="button" role="switch" aria-checked={l.enabled} aria-label={l.title}
                  onClick={() => commit(libs.map(x => (x.id === l.id ? { ...x, enabled: !x.enabled } : x)))}
                  style={toggleHit}
                >
                  <span data-track style={{ ...toggle, background: l.enabled ? LUMA_BLUE : 'rgba(255,255,255,0.18)' }}>
                    <span style={{ ...knob, transform: l.enabled ? 'translateX(20px)' : 'none' }} />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
    </section>
  );
}

const title_: React.CSSProperties = { margin: 0, fontSize: 17, fontWeight: 600, fontFamily: FONT };
const list: React.CSSProperties = {
  listStyle: 'none', margin: 0, padding: 0, borderRadius: 16, overflow: 'hidden',
  background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)',
  backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)', display: 'flex', flexDirection: 'column', gap: 1,
};
const row: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 12, padding: '8px 12px', minHeight: 60, boxSizing: 'border-box',
  borderBottom: '1px solid rgba(255,255,255,0.06)',
};
const name: React.CSSProperties = { flex: 1, fontSize: 16, fontFamily: FONT, color: '#fff' };
// Every control is at least 44 x 44 px to tap.
const arrow: React.CSSProperties = {
  width: 44, height: 44, border: 'none', borderRadius: 12, background: 'rgba(255,255,255,0.08)',
  color: 'rgba(255,255,255,0.85)', fontSize: 12, cursor: 'pointer', padding: 0, flex: 'none',
};
const toggleHit: React.CSSProperties = {
  width: 56, height: 44, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
  border: 'none', background: 'none', cursor: 'pointer', padding: 0, flex: 'none',
};
const toggle: React.CSSProperties = {
  position: 'relative', display: 'block', width: 46, height: 26, borderRadius: 13,
  transition: 'background .2s', flex: 'none',
};
const sectionTitle: React.CSSProperties = {
  margin: '0 4px 12px', fontSize: 13, fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase',
  color: 'rgba(255,255,255,0.6)', fontFamily: FONT,
};
const navRow: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 12, padding: '0 16px', minHeight: 56, color: '#fff', textDecoration: 'none',
};
const sideNav: React.CSSProperties = { width: 220, flex: 'none', display: 'flex', flexDirection: 'column', gap: 4 };
const navItem: React.CSSProperties = {
  display: 'flex', alignItems: 'center', minHeight: 44, padding: '0 14px', borderRadius: 12, fontSize: 15,
  fontFamily: FONT, color: 'rgba(255,255,255,0.75)', textDecoration: 'none',
};
const navItemOn: React.CSSProperties = { background: 'rgba(255,255,255,0.10)', color: '#fff', fontWeight: 600 };
const pickRow: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 12, width: '100%', minHeight: 56, padding: '0 16px', border: 'none',
  borderBottom: '1px solid rgba(255,255,255,0.06)', background: 'none', cursor: 'pointer', textAlign: 'left',
};
const radio: React.CSSProperties = {
  width: 22, height: 22, borderRadius: 11, border: '2px solid rgba(255,255,255,0.35)', boxSizing: 'border-box',
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none',
};
const radioOn: React.CSSProperties = { borderColor: LUMA_BLUE };
const radioDot: React.CSSProperties = { width: 10, height: 10, borderRadius: 5, background: LUMA_BLUE };
const knob: React.CSSProperties = {
  position: 'absolute', top: 3, left: 3, width: 20, height: 20, borderRadius: 10, background: '#fff',
  transition: 'transform .2s', boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
};

/** @deprecated name kept for imports: the page is Settings now. */
export const LibrariesPage = SettingsPage;
