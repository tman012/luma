/** Settings > Libraries: which Plex libraries get a Home rail, and in what order. */
import { useEffect, useRef, useState } from 'react';
import { Link, Navigate } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { FONT } from '@/components/start/StartUI';
import { LUMA_BLUE } from '@/ui/lumaButton';
import { railCss } from '@/components/home/Rails';
import { fetchLibraries, move, saveLibraries, type Library } from '@/components/home/homeApi';
import { homeBar, homePage, iconBtn } from '@/pages/MinimalHomePage';

export function LibrariesPage() {
  const { authenticated, loading } = useAuth();
  if (loading) return null;
  if (!authenticated) return <Navigate to="/login" replace />;
  return <LibrariesEditor />;
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
    <div style={homePage} data-step="libraries">
      <style>{railCss}</style>
      <header style={homeBar}>
        <Link to="/" aria-label="Back" style={iconBtn}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M15 18l-6-6 6-6" /></svg>
        </Link>
        <h1 style={title}>Libraries</h1>
        <span style={{ width: 40 }} />
      </header>
      <main style={{ padding: '24px 20px calc(env(safe-area-inset-bottom) + 32px)', maxWidth: 560, margin: '0 auto' }}>
        {error && <p role="alert" style={{ color: '#ff8a8a', fontSize: 14, margin: '0 0 12px' }}>{error}</p>}
        {libs === null ? (
          <div data-testid="libraries-skeleton" style={list}>
            {[0, 1, 2].map(i => <div key={i} className="luma-shimmer" style={{ height: 60 }} />)}
          </div>
        ) : (
          <ul style={list} data-testid="libraries">
            {libs.map((l, i) => (
              <li key={l.id} style={row}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <button type="button" aria-label={`Move ${l.title} up`} disabled={i === 0}
                    onClick={() => commit(move(libs, i, i - 1))} style={arrow}>▲</button>
                  <button type="button" aria-label={`Move ${l.title} down`} disabled={i === libs.length - 1}
                    onClick={() => commit(move(libs, i, i + 1))} style={arrow}>▼</button>
                </div>
                <span style={name}>{l.title}</span>
                <button
                  type="button" role="switch" aria-checked={l.enabled} aria-label={l.title}
                  onClick={() => commit(libs.map(x => (x.id === l.id ? { ...x, enabled: !x.enabled } : x)))}
                  style={{ ...toggle, background: l.enabled ? LUMA_BLUE : 'rgba(255,255,255,0.18)' }}
                >
                  <span style={{ ...knob, transform: l.enabled ? 'translateX(20px)' : 'none' }} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </main>
    </div>
  );
}

const title: React.CSSProperties = { margin: 0, fontSize: 17, fontWeight: 600, fontFamily: FONT };
const list: React.CSSProperties = {
  listStyle: 'none', margin: 0, padding: 0, borderRadius: 16, overflow: 'hidden',
  background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)',
  backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)', display: 'flex', flexDirection: 'column', gap: 1,
};
const row: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 14, padding: '10px 16px', minHeight: 60, boxSizing: 'border-box',
  borderBottom: '1px solid rgba(255,255,255,0.06)',
};
const name: React.CSSProperties = { flex: 1, fontSize: 16, fontFamily: FONT, color: '#fff' };
const arrow: React.CSSProperties = {
  width: 28, height: 20, border: 'none', borderRadius: 6, background: 'rgba(255,255,255,0.08)',
  color: 'rgba(255,255,255,0.8)', fontSize: 9, cursor: 'pointer', padding: 0,
};
const toggle: React.CSSProperties = {
  position: 'relative', width: 46, height: 26, borderRadius: 13, border: 'none', cursor: 'pointer', padding: 0,
  transition: 'background .2s', flex: 'none',
};
const knob: React.CSSProperties = {
  position: 'absolute', top: 3, left: 3, width: 20, height: 20, borderRadius: 10, background: '#fff',
  transition: 'transform .2s', boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
};
