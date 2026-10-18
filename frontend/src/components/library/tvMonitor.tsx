/** v158a: Luma's own TV monitoring on the show detail (series / season / episode toggles), when the show
 * is in a Luma TV library (owner, luma-test). Off (nothing rendered) anywhere else. Bookmark = monitored;
 * no labels or helper text, aria only. */
import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { isMinimal } from '@/minimal';
import { getSeriesByTmdb, searchOutcome, searchShow, setEpisodeMonitored, setSeasonMonitored, setSeriesMonitored, type SeriesView } from './tvCalendarApi';

export interface LumaMonitor {
  view: SeriesView;
  series: (on: boolean) => void;
  season: (n: number, on: boolean) => void;
  episode: (season: number, episode: number, on: boolean) => void;
}
export const LumaMonitorContext = createContext<LumaMonitor | null>(null);

export function useLumaSeries(tmdbId: number | null | undefined): LumaMonitor | null {
  const [view, setView] = useState<SeriesView | null>(null);
  useEffect(() => {
    setView(null);
    if (!tmdbId || !isMinimal()) return undefined;
    let live = true;
    void getSeriesByTmdb(tmdbId).then(r => { if (live && r.ok) setView(r.data); });
    return () => { live = false; };
  }, [tmdbId]);
  const settle = useCallback((before: SeriesView, p: Promise<{ ok: boolean; data?: SeriesView }>) => {
    void p.then(r => setView(r.ok && r.data ? r.data : before));
  }, []);
  if (!view) return null;
  return {
    view,
    series: on => { const b = view; setView({ ...view, monitored: on }); settle(b, setSeriesMonitored(view.id, on) as never); },
    season: (n, on) => {
      const b = view;
      setView({ ...view, monitored: on || view.monitored, seasons: view.seasons.map(s => (s.season === n ? { ...s, monitored: on } : s)),
        episodes: view.episodes.map(e => (e.season === n ? { ...e, monitored: on } : e)) });
      settle(b, setSeasonMonitored(view.id, n, on) as never);
    },
    episode: (s, n, on) => {
      const ep = view.episodes.find(e => e.season === s && e.episode === n);
      if (!ep) return;
      const b = view;
      const episodes = view.episodes.map(e => (e.id === ep.id ? { ...e, monitored: on } : e));
      setView({ ...view, monitored: on || view.monitored, episodes,
        seasons: view.seasons.map(x => (x.season === s ? { ...x, monitored: episodes.some(e => e.season === s && e.monitored) } : x)) });
      settle(b, setEpisodeMonitored(ep.id, on) as never);
    },
  };
}

export function MonitorMark({ on, onToggle, label, size = 32, testid }: {
  on: boolean; onToggle: (on: boolean) => void; label: string; size?: number; testid: string;
}) {
  return (
    <button type="button" data-testid={testid} aria-pressed={on} aria-label={label} className="lp-focus"
      onClick={e => { e.stopPropagation(); e.preventDefault(); onToggle(!on); }}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation(); }}
      style={{ width: size, height: size, padding: 0, borderRadius: size / 4, flex: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        background: on ? '#3b82f6' : 'rgba(255,255,255,0.10)', border: on ? '1px solid #3b82f6' : '1px solid rgba(255,255,255,0.08)',
        backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)', color: '#fff', cursor: 'pointer' }}>
      <svg width={size * 0.45} height={size * 0.45} viewBox="0 0 24 24" fill={on ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
        <path d="M6 3h12v18l-6-4.5L6 21z" />
      </svg>
    </button>
  );
}

/** The series toggle (hero action row). */
export function SeriesMark({ size = 44 }: { size?: number }) {
  const m = useContext(LumaMonitorContext);
  if (!m) return null;
  return <MonitorMark testid="tv-mon-series" on={m.view.monitored} onToggle={m.series} label={`Monitored: ${m.view.title}`} size={size} />;
}

/** A season's toggle (next to the season tabs). */
export function SeasonMark({ season }: { season: number }) {
  const m = useContext(LumaMonitorContext);
  const s = m?.view.seasons.find(x => x.season === season);
  if (!m || !s) return null;
  return <MonitorMark testid="tv-mon-season" on={s.monitored} onToggle={on => m.season(season, on)} label={`Monitored: season ${season}`} size={36} />;
}

/** An episode's toggle (top right of its thumbnail). */
export function EpisodeMark({ season, episode }: { season: number; episode: number }) {
  const m = useContext(LumaMonitorContext);
  const e = m?.view.episodes.find(x => x.season === season && x.episode === episode);
  if (!m || !e) return null;
  return (
    <span style={{ position: 'absolute', top: 8, right: 8, zIndex: 2, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <SearchMark season={season} episode={episode} />
      <MonitorMark testid="tv-mon-episode" on={e.monitored} onToggle={on => m.episode(season, episode, on)}
        label={`Monitored: S${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}`} size={30} />
    </span>
  );
}

/** v158b: Search for this episode or season (magnifier chip, glass .10). The outcome shows as a short
 * status next to it for a few seconds; aria-live for screen readers. */
export function SearchMark({ season, episode, size = 30 }: { season: number; episode?: number; size?: number }) {
  const m = useContext(LumaMonitorContext);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    if (!note) return undefined;
    const t = setTimeout(() => setNote(null), 4000);
    return () => clearTimeout(t);
  }, [note]);
  const ep = episode == null ? null : m?.view.episodes.find(x => x.season === season && x.episode === episode);
  if (!m || (episode != null && !ep)) return null;
  const label = episode == null ? `Search: season ${season}` : `Search: S${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}`;
  const go = (e: React.SyntheticEvent) => {
    e.stopPropagation(); e.preventDefault();
    if (busy) return;
    setBusy(true);
    void searchShow(m.view.id, ep ? { episode_id: ep.id } : { season })
      .then(r => setNote(searchOutcome(r))).finally(() => setBusy(false));
  };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      {note && <span role="status" data-testid="tv-search-note" style={{ fontSize: 12, fontWeight: 600, color: 'rgba(255,255,255,0.9)',
        background: 'rgba(255,255,255,0.10)', backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)',
        border: '1px solid rgba(255,255,255,0.08)', borderRadius: 8, padding: '4px 8px', whiteSpace: 'nowrap' }}>{note}</span>}
      <button type="button" data-testid={episode == null ? 'tv-search-season' : 'tv-search-episode'} aria-label={label} aria-busy={busy}
        className="lp-focus" onClick={go} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation(); }}
        style={{ width: size, height: size, padding: 0, borderRadius: size / 4, flex: 'none', display: 'inline-flex', alignItems: 'center',
          justifyContent: 'center', background: 'rgba(255,255,255,0.10)', border: '1px solid rgba(255,255,255,0.08)',
          backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)', color: '#fff', cursor: busy ? 'default' : 'pointer',
          opacity: busy ? 0.6 : 1 }}>
        <svg width={size * 0.45} height={size * 0.45} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
          strokeLinecap="round" aria-hidden="true"
          style={busy ? { animation: 'tvSearchPulse 0.9s ease-in-out infinite alternate' } : undefined}>
          <circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" />
        </svg>
        <style>{'@keyframes tvSearchPulse{from{opacity:1}to{opacity:.35}}'}</style>
      </button>
    </span>
  );
}
