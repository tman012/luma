/** v202: Add to watchlist on the test install (the native watchlist): pick a quality profile (the default
 * one first) and add. Never Radarr. */
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { FONT } from '@/components/start/StartUI';
import { loadMovieSettings, type MovieProfile } from './movieSettingsApi';
import { addToNativeWatchlist } from '@/services/watchlistApi';

export function WatchlistAddSheet({ tmdbId, title, year, onClose, onAdded }: {
  tmdbId: number; title: string; year?: number | null; onClose: () => void; onAdded: () => void;
}) {
  const [profiles, setProfiles] = useState<MovieProfile[] | null>(null);
  const [pid, setPid] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    void loadMovieSettings().then(r => {
      const list = r.ok ? r.data.profiles : [];
      setProfiles(list);
      setPid((list.find(p => p.default) ?? list[0])?.id ?? null);
    });
  }, []);
  const add = async () => {
    if (busy) return;
    setBusy(true); setError('');
    const r = await addToNativeWatchlist({ tmdbId, title, year, profileId: pid });
    setBusy(false);
    if (!r.ok) { setError(r.error || "Couldn't add"); return; }
    onAdded();
  };
  return createPortal(
    <div className="luma-fullscreen luma-bleed" data-testid="watchlist-add-sheet" onClick={() => { if (!busy) onClose(); }}
      style={{ position: 'fixed', inset: 0, zIndex: 10090, background: 'rgba(21,16,42,0.72)', backdropFilter: 'blur(8px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: FONT }}>
      <div role="dialog" aria-modal="true" aria-label="Add to watchlist" onClick={e => e.stopPropagation()}
        style={{ width: 'min(420px, calc(100vw - 32px))', borderRadius: 20, padding: 24, color: '#fff',
          background: 'linear-gradient(rgba(255,255,255,0.06), rgba(255,255,255,0.06)), #15102a', border: '1px solid rgba(255,255,255,0.08)' }}>
        <h2 style={{ margin: '0 0 16px', fontSize: 18, fontWeight: 600, textAlign: 'center' }}>{title}</h2>
        <label htmlFor="wl-profile" style={{ display: 'block', fontSize: 13, color: 'rgba(255,255,255,0.7)', marginBottom: 6 }}>Quality profile</label>
        <select id="wl-profile" data-testid="watchlist-profile" value={pid ?? ''} disabled={!profiles} onChange={e => setPid(Number(e.target.value))}
          style={{ width: '100%', height: 44, borderRadius: 10, padding: '0 12px', fontSize: 15, color: '#fff',
            background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.14)' }}>
          {(profiles ?? []).map(p => <option key={p.id} value={p.id} style={{ color: '#000' }}>{p.name}</option>)}
        </select>
        {error && <p role="alert" style={{ margin: '12px 0 0', fontSize: 14, color: '#f87171', textAlign: 'center' }}>{error}</p>}
        <div style={{ display: 'flex', gap: 12, marginTop: 20 }}>
          <button type="button" onClick={onClose} disabled={busy}
            style={{ flex: 1, height: 48, borderRadius: 50, fontSize: 16, fontWeight: 600, color: '#fff', cursor: 'pointer',
              background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.14)' }}>Cancel</button>
          <button type="button" data-testid="watchlist-add-confirm" onClick={() => void add()} disabled={busy || !profiles}
            style={{ flex: 1, height: 48, borderRadius: 50, fontSize: 16, fontWeight: 600, color: '#fff', cursor: busy ? 'default' : 'pointer',
              background: '#3b82f6', border: 'none', opacity: busy ? 0.6 : 1 }}>{busy ? 'Adding…' : 'Add'}</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
