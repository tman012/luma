/** v182 (Taylor): the Sonarr-style series detail page (Luma's own code; Sonarr is a behavior reference only).
 * Opened from Series, the calendar and search results (Settings > TV > Series, ?show=<id>).
 * Header: poster, title, year, network, status, quality profile, path, size, have/total monitored episodes,
 * the show's monitor switch and Edit. Seasons: collapsible, newest first, each with its monitor switch, have/total,
 * Search and Interactive search. Episodes: number, title, air date (ET), status (the 4px status edge and a word;
 * no checkmarks), the monitored bookmark, Search and Interactive search (v184: no checkboxes; the bulk editor is the Series list's).
 * Search and Interactive search are the owner's own: their grabs may go past the 24 h floor; an episode that
 * has a file is still refused, and monitor switches never search. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { FONT } from '@/components/start/StartUI';
import { Poster } from './TvImport';
import { EditSeriesModal, Modal, glassBtn, primaryBtn } from './TvEdit';
import { MonitorFlag, monitorFlagCss } from './TvMonitorFlag';

/** v185 (Designer): the poster's bookmark chip and glyph */
export const POSTER_FLAG = 40;
export const POSTER_GLYPH = 22;
import { useMonitorSearch } from './TvMonitorSearch';
import { seasonName } from './TvBulk';
import { usePhone } from './TvCalendar';
import {
  getDetail, grabRelease, interactiveSearch, searchOutcome, searchShow, setEpisodeMonitored,
  setSeasonMonitored, setSeriesMonitored,
  type DetailEpisode, type DetailState, type DetailView, type InteractiveResult, type Release, type SearchTarget,
} from './tvCalendarApi';

const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';
const RED = '#ef4444';
const LINE = '1px solid rgba(255,255,255,0.06)';
const GLASS = 'rgba(255,255,255,0.04)';
const EDGE_W = 4;
const ET = 'America/New_York';

export const DETAIL_STATE_WORD: Record<DetailState, string> = {
  downloaded: 'Downloaded', downloading: 'Downloading', missing: 'Missing', unaired: 'Unaired', no_date: 'No air date',
};
export const DETAIL_STATE_COLOR: Record<DetailState, string> = {
  downloaded: 'var(--tv-state-downloaded, #22c55e)', downloading: 'var(--tv-state-downloading, #3b82f6)', missing: 'var(--tv-state-missing, #ef4444)',
  unaired: 'var(--tv-state-unaired, #3b82f6)', no_date: 'rgba(255,255,255,0.3)',
};
export const detailCss = `
.tvd-row:hover{background:rgba(255,255,255,.03)}
.tvd-icon:hover{background:rgba(255,255,255,.10)!important;color:#fff!important}
.tvd-back:hover{color:#fff!important}
.tvd-season-head:hover{background:rgba(255,255,255,.06)!important}
`;

/** v185 (Designer): the app's date format, "Nov 1, 9:00 PM" (the airstamp, in Eastern time), the year only when
 * it isn't this year ("Jan 17, 2025, 9:00 PM"), no zone; a date only ("Nov 1") without an airstamp. */
export function airET(e: { airstamp?: string | null; air_date?: string | null }, now: Date = new Date()): string {
  const yearOf = (d: Date, tz: string) => new Intl.DateTimeFormat('en-US', { timeZone: tz, year: 'numeric' }).format(d);
  const thisYear = yearOf(now, ET);
  if (e.airstamp) {
    const d = new Date(e.airstamp);
    if (!Number.isNaN(d.getTime())) {
      const year = yearOf(d, ET) === thisYear ? {} : { year: 'numeric' as const };
      return new Intl.DateTimeFormat('en-US', { timeZone: ET, month: 'short', day: 'numeric', ...year, hour: 'numeric', minute: '2-digit' }).format(d);
    }
  }
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(e.air_date || '');
  if (!m) return '';
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  const year = m[1] === thisYear ? {} : { year: 'numeric' as const };
  return new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric', ...year }).format(d);
}
/** 2.1 GB / 850 MB / 0 B. */
export function bytes(n: number | null | undefined): string {
  const v = Number(n || 0);
  if (v >= 1024 ** 4) return `${(v / 1024 ** 4).toFixed(1)} TB`;
  if (v >= 1024 ** 3) return `${(v / 1024 ** 3).toFixed(1)} GB`;
  if (v >= 1024 ** 2) return `${Math.round(v / 1024 ** 2)} MB`;
  if (v >= 1024) return `${Math.round(v / 1024)} KB`;
  return `${v} B`;
}
/** TMDB's "Returning Series" in sentence case. */
export const sentence = (s?: string | null) => (s ? s.charAt(0).toUpperCase() + s.slice(1).toLowerCase() : '');
export const code = (s: number, e: number) => `S${String(s).padStart(2, '0')}E${String(e).padStart(2, '0')}`;

/** v184 (Taylor): the page's content width, centered */
export const DETAIL_MAX = 1200;

export function TvSeriesDetail({ seriesId, episodeId, wide, onBack, onChanged, say }: {
  seriesId: number; episodeId?: number | null; wide: boolean; onBack: () => void; onChanged?: () => void; say: (msg: string) => void;
}) {
  const [view, setView] = useState<DetailView | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<Set<number> | null>(null);
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState(false);
  const phoneQ = usePhone();
  const [menu, setMenu] = useState<number | null>(null);
  const [interactive, setInteractive] = useState<{ target: SearchTarget; label: string } | null>(null);
  const live = useRef(true);
  useEffect(() => () => { live.current = false; }, []);
  const [onSearch, searchAsk] = useMonitorSearch(say);

  const load = useCallback(() => getDetail(seriesId).then(r => {
    if (!live.current) return;
    if (r.ok) { setView(r.data); setFailed(false); } else setFailed(true);
  }), [seriesId]);
  useEffect(() => { setView(null); setOpen(null); void load(); }, [load]);
  // the newest season starts open (or the season of the episode the page was opened at); derived in the
  // render, so the first paint with the data already has it open
  const startOpen = (v: DetailView): Set<number> => {
    const at = episodeId ? v.episodes.find(e => e.id === episodeId)?.season : undefined;
    const first = v.seasons.find(x => x.season > 0)?.season ?? v.seasons[0]?.season;
    return new Set([at ?? first].filter((n): n is number => n !== undefined));
  };
  const opened = open ?? (view ? startOpen(view) : null);
  const scrolled = useRef(false);
  useEffect(() => {
    if (!view || !episodeId || scrolled.current) return;
    scrolled.current = true;
    document.querySelector(`[data-episode-id="${episodeId}"]`)?.scrollIntoView?.({ block: 'center' });
  }, [view, episodeId]);

  const changed = () => { void load(); onChanged?.(); };
  const toggleBusy = (k: string, on: boolean) => setBusy(b => { const n = new Set(b); if (on) n.add(k); else n.delete(k); return n; });

  // monitor switches: tv.db only, never a search (the server answer wins)
  const monitorShow = async (on: boolean) => {
    setView(v => (v ? { ...v, monitored: on } : v));
    let r: Awaited<ReturnType<typeof setSeriesMonitored>>;
    try { r = await setSeriesMonitored(seriesId, on); } catch { setView(v => (v ? { ...v, monitored: !on } : v)); say("Couldn't save"); return; }
    if (!r.ok) say(r.error); else onSearch(seriesId, null, r.data.search);
    changed();
  };
  const monitorSeason = async (n: number, on: boolean) => {
    setView(v => (v ? { ...v, seasons: v.seasons.map(s => (s.season === n ? { ...s, monitored: on } : s)),
      episodes: v.episodes.map(e => (e.season === n ? { ...e, monitored: on } : e)) } : v));
    const r = await setSeasonMonitored(seriesId, n, on);
    if (!r.ok) say(r.error); else onSearch(seriesId, n, r.data.search);
    changed();
  };
  const monitorEpisode = async (id: number, on: boolean) => {
    setView(v => (v ? { ...v, episodes: v.episodes.map(e => (e.id === id ? { ...e, monitored: on } : e)) } : v));
    const r = await setEpisodeMonitored(id, on);
    if (!r.ok) say(r.error); else onSearch(seriesId, null, r.data.search);
    changed();
  };
  const search = async (target: SearchTarget, label: string) => {
    const k = 'season' in target ? `s${target.season}` : `e${target.episode_id}`;
    if (busy.has(k)) return;
    toggleBusy(k, true);
    const r = await searchShow(seriesId, target);
    if (!live.current) return;
    toggleBusy(k, false);
    say(`${label}: ${searchOutcome(r)}`);
    changed();
  };

  if (failed) return <div role="alert" data-testid="tv-detail-failed" style={{ fontSize: 15, color: W70, fontFamily: FONT }}>{"Couldn't load"}</div>;
  if (!view) return <div data-testid="tv-detail-loading" aria-busy="true" style={{ height: 320, borderRadius: 16, background: GLASS }} />;

  const phone = phoneQ && !wide;
  const PW = phone ? 120 : wide ? 128 : 88, PH = PW * 1.5;
  const eps = view.episodes;
  const meta = [view.year ? String(view.year) : '', view.network || '', sentence(view.status)].filter(Boolean);
  const facts: [string, string, string][] = [
    ['Quality profile', view.profile_name || 'None', 'profile'],
    ['Path', view.path || '', 'path'],
    ['Size', bytes(view.size), 'size'],
    ['Episodes', `${view.have} of ${view.total}`, 'episodes'],
  ];
  return (
    <div data-testid="tv-detail" data-series-id={view.id} data-menu={menu ?? undefined}
      style={{ display: 'flex', flexDirection: 'column', gap: 16, fontFamily: FONT, width: '100%', maxWidth: DETAIL_MAX, margin: '0 auto', boxSizing: 'border-box' }}>
      <style>{detailCss + monitorFlagCss}</style>
      {phone ? (
        <div data-testid="tv-detail-backrow" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <button type="button" className="lp-focus" data-testid="tv-detail-back" onClick={onBack}
            style={{ height: 44, padding: '0 8px 0 0', border: 'none', background: 'none', color: '#3b82f6', fontFamily: FONT, fontSize: 15, fontWeight: 600, cursor: 'pointer' }}>
            {'\u2039 Series'}
          </button>
          <button type="button" className="lp-focus tve-glass" data-testid="tv-detail-edit" onClick={() => setEditing(true)} style={{ ...glassBtn, height: 44 }}>Edit</button>
        </div>
      ) : (
      <button type="button" className="lp-focus tvd-back" data-testid="tv-detail-back" onClick={onBack}
          style={{ alignSelf: 'flex-start', display: 'inline-flex', alignItems: 'center', gap: 6, height: 32, padding: '0 8px 0 4px', border: 'none',
            background: 'none', color: W70, fontFamily: FONT, fontSize: 14, fontWeight: 600, cursor: 'pointer', borderRadius: 8 }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M15 18l-6-6 6-6" /></svg>
          Series
        </button>
      )}

      <section data-testid="tv-detail-header" aria-label={view.title}
        style={{ display: 'grid', gridTemplateColumns: `${PW}px minmax(0, 1fr)`, columnGap: wide ? 24 : phone ? 14 : 16, rowGap: 16,
          padding: wide ? 20 : 16, borderRadius: 16, background: GLASS, border: '1px solid rgba(255,255,255,0.08)' }}>
        <div data-testid="tv-detail-poster" style={{ position: 'relative', width: PW, height: PH }}>
          <Poster path={view.poster_path} w={PW} h={PH} tmdbLogo />
          {/* v184 (Taylor): the show's bookmark sits on the poster's top-left corner, Sonarr style; v185 (Designer):
              a 40px dark glass chip with a 22px glyph, 8px inside the corner, filled white when monitored */}
          <MonitorFlag on={view.monitored} title={view.title} testid="tv-detail-monitored" size={POSTER_FLAG} glyph={POSTER_GLYPH}
            onChange={on => void monitorShow(on)}
            style={{ position: 'absolute', top: 8, left: 8, borderRadius: 10, background: 'rgba(21,16,42,0.72)', backdropFilter: 'blur(12px)',
              WebkitBackdropFilter: 'blur(12px)', color: view.monitored ? '#fff' : 'rgba(255,255,255,0.85)', boxShadow: '0 2px 8px rgba(0,0,0,0.45)' }} />
        </div>
        <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
            <h2 data-testid="tv-detail-title" style={{ margin: 0, flex: 1, minWidth: 0, fontSize: wide ? 24 : 20, lineHeight: wide ? '30px' : '26px', fontWeight: 700, color: W95, overflowWrap: 'anywhere' }}>
              {view.title}
            </h2>
            {wide && <HeaderActions onEdit={() => setEditing(true)} />}
          </div>
          {meta.length > 0 && <div data-testid="tv-detail-meta" style={{ fontSize: 14, color: W70 }}>{meta.join(' \u00b7 ')}</div>}
          {wide && <Facts facts={facts} wide />}
        </div>
        {!wide && (
          <div style={{ gridColumn: '1 / -1', display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Facts facts={facts} wide={false} />
            {!phone && <HeaderActions onEdit={() => setEditing(true)} />}
          </div>
        )}
      </section>

      {view.seasons.map(s => {
        const list = eps.filter(e => e.season === s.season).sort((a, b) => b.episode - a.episode);
        const isOpen = !!opened?.has(s.season);
        const label = seasonName(s.season);
        return (
          <section key={s.season} data-testid="tv-detail-season" data-season={s.season} data-open={isOpen || undefined}
            style={{ borderRadius: 16, border: '1px solid rgba(255,255,255,0.08)', background: GLASS, overflow: 'hidden' }}>
            <div className="tvd-season-head" style={{ display: 'flex', alignItems: 'center', gap: wide ? 12 : 8, minHeight: 56, padding: wide ? '0 12px 0 16px' : '0 8px 0 12px' }}>
              <MonitorFlag on={s.monitored} title={label} testid="tv-detail-season-monitored" onChange={v => void monitorSeason(s.season, v)} />
              <button type="button" className="lp-focus" data-testid="tv-detail-season-toggle" aria-expanded={isOpen}
                onClick={() => setOpen(o => { const n = new Set(o || opened || []); if (n.has(s.season)) n.delete(s.season); else n.add(s.season); return n; })}
                style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 10, height: 44, padding: 0, border: 'none', background: 'none',
                  cursor: 'pointer', fontFamily: FONT, textAlign: 'left', color: W95 }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={W50} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
                  style={{ flex: 'none', transform: isOpen ? 'rotate(90deg)' : undefined, transition: 'transform 150ms' }}><path d="M9 6l6 6-6 6" /></svg>
                <span style={{ fontSize: 16, fontWeight: 600, whiteSpace: 'nowrap' }}>{label}</span>
                <span data-testid="tv-detail-season-count" style={{ fontSize: 13, color: s.total && s.have >= s.total ? W70 : W50, whiteSpace: 'nowrap' }}>
                  {`${s.have} of ${s.total}`}
                </span>
                {wide && s.size > 0 && <span style={{ fontSize: 12, color: W50, whiteSpace: 'nowrap' }}>{bytes(s.size)}</span>}
              </button>
              <IconBtn kind="search" label={`Search ${label}`} busy={busy.has(`s${s.season}`)} testid="tv-detail-season-search"
                onClick={() => void search({ season: s.season }, label)} />
              <IconBtn kind="interactive" label={`Interactive search ${label}`} testid="tv-detail-season-interactive"
                onClick={() => setInteractive({ target: { season: s.season }, label: `${view.title} \u00b7 ${label}` })} />
            </div>
            {isOpen && (
              <div role="list" aria-label={label}>
                {list.map(e => (
                  <EpisodeRow key={e.id} e={e} wide={wide} phone={phone} onMenu={() => setMenu(e.id)} focus={e.id === episodeId}
                    busy={busy.has(`e${e.id}`)}
                    onMonitor={v => void monitorEpisode(e.id, v)}
                    onSearch={() => void search({ episode_id: e.id }, code(e.season, e.episode))}
                    onInteractive={() => setInteractive({ target: { episode_id: e.id }, label: `${view.title} \u00b7 ${code(e.season, e.episode)}` })} />
                ))}
                {!list.length && <div style={{ padding: '12px 16px', fontSize: 14, color: W50, borderTop: LINE }}>No episodes</div>}
              </div>
            )}
          </section>
        );
      })}

      {searchAsk}
      {editing && (
        <EditSeriesModal seriesId={view.id} title={view.title} onClose={() => setEditing(false)}
          onSaved={() => changed()} onDeleted={() => { onChanged?.(); onBack(); }} />
      )}
      {interactive && (
        <InteractiveModal seriesId={view.id} target={interactive.target} title={interactive.label} wide={wide}
          onClose={() => setInteractive(null)} onGrabbed={changed} />
      )}
    </div>
  );
}

function HeaderActions({ onEdit }: { onEdit: () => void }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 'none' }}>
      <button type="button" className="lp-focus tve-glass" data-testid="tv-detail-edit" onClick={onEdit} style={{ ...glassBtn, height: 36 }}>Edit</button>
    </div>
  );
}

function Facts({ facts, wide }: { facts: [string, string, string][]; wide: boolean }) {
  return (
    <dl data-testid="tv-detail-facts" style={{ margin: wide ? '8px 0 0' : 0, display: 'grid', gridTemplateColumns: wide ? 'repeat(2, minmax(0, 1fr))' : 'minmax(0, 1fr)',
      columnGap: 24, rowGap: wide ? 12 : 8 }}>
      {facts.map(([k, v, id]) => (
        <div key={k} data-testid={`tv-detail-fact-${id}`} style={{ minWidth: 0, display: wide ? 'flex' : 'grid', flexDirection: 'column', gap: wide ? 2 : 12,
          gridTemplateColumns: wide ? undefined : '120px minmax(0, 1fr)', gridColumn: id === 'path' && wide ? '1 / -1' : undefined }}>
          <dt style={{ fontSize: 12, fontWeight: 600, color: W50 }}>{k}</dt>
          <dd style={{ margin: 0, fontSize: 14, color: W95, overflowWrap: 'anywhere', fontVariantNumeric: 'tabular-nums' }}>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function IconBtn({ kind, label, onClick, busy = false, testid }: {
  kind: 'search' | 'interactive'; label: string; onClick: () => void; busy?: boolean; testid: string;
}) {
  return (
    <button type="button" className="lp-focus tvd-icon" data-testid={testid} aria-label={label} title={label} aria-busy={busy || undefined}
      disabled={busy} onClick={e => { e.stopPropagation(); onClick(); }}
      style={{ width: 36, height: 36, flex: 'none', padding: 0, borderRadius: 10, border: 'none', background: 'transparent', color: W70,
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.5 : 1 }}>
      {kind === 'search' ? (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
        </svg>
      ) : (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="12" cy="8" r="4" /><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7" />
        </svg>
      )}
    </button>
  );
}

function EpisodeRow({ e, wide, phone = false, onMenu, focus, busy, onMonitor, onSearch, onInteractive }: {
  e: DetailEpisode; wide: boolean; phone?: boolean; onMenu?: () => void; focus: boolean; busy: boolean;
  onMonitor: (on: boolean) => void; onSearch: () => void; onInteractive: () => void;
}) {
  const air = airET(e);
  const word = DETAIL_STATE_WORD[e.state];
  const color = DETAIL_STATE_COLOR[e.state];
  const dim = e.monitored ? 1 : 0.5;
  const name = e.title || `Episode ${e.episode}`;
  const tag = `${code(e.season, e.episode)} ${name}`;
  if (phone) return (
    <div role="listitem" className="tvd-row" data-testid="tv-detail-episode" data-episode-id={e.id} data-state={e.state}
      data-monitored={e.monitored ? 'true' : 'false'} data-focus={focus || undefined}
      style={{ display: 'grid', gridTemplateColumns: '44px minmax(0, 1fr) 44px', alignItems: 'center', columnGap: 4, minHeight: 60, padding: '8px 4px 8px 8px',
        boxShadow: `inset ${EDGE_W}px 0 0 ${color}`, borderTop: LINE, boxSizing: 'border-box', background: focus ? 'rgba(255,255,255,0.05)' : undefined }}>
      <span data-testid="tv-detail-edge" aria-hidden="true" style={{ display: 'none', background: color }} />
      <span style={{ display: 'flex', justifyContent: 'center' }}><MonitorFlag on={e.monitored} title={tag} testid="tv-detail-episode-monitored" onChange={onMonitor} /></span>
      <span style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2, opacity: dim }}>
        <span data-testid="tv-detail-episode-title" style={{ fontSize: 14, fontWeight: 600, color: W95, overflowWrap: 'anywhere' }}>{`${e.episode} \u00b7 ${name}`}</span>
        <span style={{ fontSize: 12, color: W70 }}>
          {air && <span data-testid="tv-detail-air">{`${air} \u00b7 `}</span>}
          <span data-testid="tv-detail-status" style={{ color, fontWeight: 600 }}>{word}</span>
          {e.quality ? ` \u00b7 ${e.quality}` : ''}
        </span>
      </span>
      <button type="button" className="lp-focus tvd-icon" data-testid="tv-detail-episode-menu" aria-label={`More ${tag}`} aria-haspopup="dialog" onClick={onMenu}
        style={{ width: 44, height: 44, padding: 0, border: 'none', borderRadius: 10, background: 'transparent', color: W70, cursor: 'pointer', fontSize: 20, lineHeight: 1 }}>
        {'\u22ef'}
      </button>
    </div>
  );
  return (
    <div role="listitem" className="tvd-row" data-testid="tv-detail-episode" data-episode-id={e.id} data-state={e.state}
      data-monitored={e.monitored ? 'true' : 'false'} data-focus={focus || undefined}
      style={{ position: 'relative', display: 'grid', alignItems: 'center', columnGap: wide ? 12 : 8, minHeight: wide ? 52 : 64, borderTop: LINE,
        padding: wide ? '0 12px 0 16px' : '8px 8px 8px 12px', boxSizing: 'border-box',
        gridTemplateColumns: wide ? '28px 32px minmax(0, 1fr) 190px 100px 36px 36px' : '28px 24px minmax(0, 1fr) 36px 36px',
        background: focus ? 'rgba(255,255,255,0.05)' : undefined }}>
      <span data-testid="tv-detail-edge" aria-hidden="true" style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: EDGE_W, background: color }} />
      <MonitorFlag on={e.monitored} title={tag} testid="tv-detail-episode-monitored" onChange={onMonitor} />
      <span style={{ fontSize: 13, color: W50, fontVariantNumeric: 'tabular-nums', opacity: dim }}>{e.episode}</span>
      <span style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2, opacity: dim }}>
        <span data-testid="tv-detail-episode-title" style={{ fontSize: 14, fontWeight: 500, color: W95, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{name}</span>
        {!wide && (
          <span style={{ fontSize: 12, color: W50, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            <span data-testid="tv-detail-status" style={{ color, fontWeight: 600 }}>{word}</span>
            {air && <span data-testid="tv-detail-air">{` \u00b7 ${air}`}</span>}
          </span>
        )}
        {wide && e.quality && <span style={{ fontSize: 12, color: W50 }}>{e.quality}{e.size ? ` \u00b7 ${bytes(e.size)}` : ''}</span>}
      </span>
      {wide && <span data-testid="tv-detail-air" style={{ fontSize: 13, color: W70, opacity: dim, whiteSpace: 'nowrap' }}>{air}</span>}
      {wide && <span data-testid="tv-detail-status" style={{ fontSize: 13, fontWeight: 600, color, opacity: dim, whiteSpace: 'nowrap' }}>{word}</span>}
      <IconBtn kind="search" label={`Search ${tag}`} busy={busy} onClick={onSearch} testid="tv-detail-episode-search" />
      <IconBtn kind="interactive" label={`Interactive search ${tag}`} onClick={onInteractive} testid="tv-detail-episode-interactive" />
    </div>
  );
}

type GrabState = { busy?: boolean; done?: boolean; error?: string };

export function InteractiveModal({ seriesId, target, title, wide, onClose, onGrabbed }: {
  seriesId: number; target: SearchTarget; title: string; wide: boolean; onClose: () => void; onGrabbed: () => void;
}) {
  const [res, setRes] = useState<InteractiveResult | null>(null);
  const [error, setError] = useState('');
  const [grabs, setGrabs] = useState<Record<string, GrabState>>({});
  const key = JSON.stringify(target);
  useEffect(() => {
    let live = true;
    void interactiveSearch(seriesId, target).then(r => {
      if (!live) return;
      if (r.ok) { setRes(r.data); setError(r.data.error || ''); } else setError(r.error);
    });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seriesId, key]);
  const grab = async (r: Release, k: string) => {
    setGrabs(g => ({ ...g, [k]: { busy: true } }));
    const out = await grabRelease(seriesId, r);
    if (out.ok && out.data.ok) { setGrabs(g => ({ ...g, [k]: { done: true } })); onGrabbed(); return; }
    setGrabs(g => ({ ...g, [k]: { error: out.ok ? out.data.error || 'Not grabbed' : out.error } }));
  };
  const list = res?.releases ?? [];
  const off = res ? !res.grabs_allowed : false;
  return (
    <Modal title={title} testid="tv-interactive" width={wide ? 1000 : 560} onClose={onClose} footer={(
      <>
        <span data-testid="tv-interactive-count" style={{ flex: 1, fontSize: 13, color: W50 }}>
          {res ? `${list.length} ${list.length === 1 ? 'release' : 'releases'}` : ''}
        </span>
        <button type="button" className="lp-focus tve-glass" data-testid="tv-interactive-done" onClick={onClose} style={glassBtn}>Close</button>
      </>
    )}>
      {error && !list.length ? (
        <div role="alert" data-testid="tv-interactive-error" style={{ minHeight: 160, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, color: W70 }}>{error}</div>
      ) : !res ? (
        <div data-testid="tv-interactive-loading" aria-busy="true" style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          {[0, 1, 2, 3].map(i => <div key={i} style={{ height: 52, background: GLASS, borderRadius: i === 0 ? '10px 10px 0 0' : i === 3 ? '0 0 10px 10px' : 0 }} />)}
        </div>
      ) : !list.length ? (
        <div data-testid="tv-interactive-empty" style={{ minHeight: 160, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, color: W70 }}>No results</div>
      ) : (
        <div role="table" aria-label="Releases" data-testid="tv-interactive-list" style={{ borderRadius: 12, border: LINE, overflow: 'hidden' }}>
          {wide && (
            <div role="row" style={{ ...relGrid, minHeight: 36, background: GLASS }}>
              {['Release', 'Tracker', 'Size', 'Seeders', 'Quality', ''].map((h, i) => (
                <span key={i} role="columnheader" style={{ fontSize: 12, fontWeight: 600, color: W50, textAlign: i >= 2 && i <= 3 ? 'right' : 'left' }}>{h}</span>
              ))}
            </div>
          )}
          {list.map((r, i) => {
            const k = `${r.tracker}|${r.title}|${r.size}`;
            const g = grabs[k] || {};
            const can = r.grabbable && !g.done;
            // v185 (Designer): grabs off (test instance): no Grab button, like the search chips; no notice line
            const btn = off ? null : (
              <button type="button" className="lp-focus tve-primary" data-testid="tv-interactive-grab" disabled={!can || g.busy}
                aria-label={`Grab ${r.title}`} onClick={() => void grab(r, k)}
                style={{ ...primaryBtn(can && !g.busy), height: 32, padding: '0 14px', fontSize: 13 }}>
                {g.done ? 'Grabbed' : 'Grab'}
              </button>
            );
            const rej = r.rejections.length > 0 && (
              <span data-testid="tv-interactive-rejections" style={{ fontSize: 12, color: r.grabbable ? '#f59e0b' : RED }}>{r.rejections.join(' \u00b7 ')}</span>
            );
            const err = g.error && <span role="alert" data-testid="tv-interactive-grab-error" style={{ fontSize: 12, color: RED }}>{g.error}</span>;
            return wide ? (
              <div key={k} role="row" data-testid="tv-interactive-row" data-grabbable={r.grabbable || undefined}
                style={{ ...relGrid, minHeight: 52, borderTop: i || wide ? LINE : 'none', padding: '8px 12px' }}>
                <span role="cell" style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <span style={{ fontSize: 13, color: W95, overflowWrap: 'anywhere' }}>{r.title}</span>
                  {rej}{err}
                </span>
                <span role="cell" style={cellText}>{r.tracker || ''}</span>
                <span role="cell" style={{ ...cellText, textAlign: 'right' }}>{bytes(r.size)}</span>
                <span role="cell" style={{ ...cellText, textAlign: 'right' }}>{r.seeders}</span>
                <span role="cell" style={cellText}>{r.quality_label || r.quality || ''}</span>
                <span role="cell" style={{ display: 'flex', justifyContent: 'flex-end' }}>{btn}</span>
              </div>
            ) : (
              <div key={k} role="row" data-testid="tv-interactive-row" data-grabbable={r.grabbable || undefined}
                style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: 12, borderTop: i ? LINE : 'none' }}>
                <span style={{ fontSize: 13, color: W95, overflowWrap: 'anywhere' }}>{r.title}</span>
                <span style={{ fontSize: 12, color: W50 }}>{[r.tracker, bytes(r.size), `${r.seeders} seeders`, r.quality_label || r.quality].filter(Boolean).join(' \u00b7 ')}</span>
                {rej}{err}
                {btn && <span style={{ display: 'flex', justifyContent: 'flex-end' }}>{btn}</span>}
              </div>
            );
          })}
        </div>
      )}
    </Modal>
  );
}

const relGrid: React.CSSProperties = {
  display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 110px 80px 64px 96px 84px', alignItems: 'center', columnGap: 12, padding: '0 12px', boxSizing: 'border-box',
};
const cellText: React.CSSProperties = { fontSize: 13, color: W70, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' };
