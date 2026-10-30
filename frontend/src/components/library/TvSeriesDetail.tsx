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
import { TYPE_DESK, TvSeriesHero, heroEditBtn } from './TvSeriesHero';
import { EditSeriesModal, Modal, glassBtn, primaryBtn } from './TvEdit';
import { MonitorFlag, monitorFlagCss } from './TvMonitorFlag';

/** v185 (Designer): the poster's bookmark chip and glyph */
export const POSTER_FLAG = 40;
export const POSTER_GLYPH = 22;
import { useMonitorSearch } from './TvMonitorSearch';
import { seasonName } from './TvBulk';
import { usePhone } from './TvCalendar';
import { useSwipeToDismiss } from '@/hooks/useSwipeToDismiss';
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
  downloaded: 'In Library', downloading: 'Downloading', missing: 'Missing', unaired: 'Unaired', no_date: 'No air date',
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
  // r2: Edit lives on the banner when one is shown; the narrow layouts keep their own Edit only without art
  const [onArt, setOnArt] = useState(false);
  const [menu, setMenu] = useState<number | null>(null);
  const [interactive, setInteractive] = useState<{ target: SearchTarget; label: string; epTitle?: string } | null>(null);
  const live = useRef(true);
  useEffect(() => () => { live.current = false; }, []);
  // TYPE-series T9: the desktop series page sits on #111318, not the app navy (#15102a, html/body token).
  useEffect(() => {
    if (!wide) return;
    const h = document.documentElement; h.dataset.lumaSurface = 'series';
    return () => { if (h.dataset.lumaSurface === 'series') delete h.dataset.lumaSurface; };
  }, [wide]);
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
  const eps = view.episodes;
  // v321 (Designer v8): the status keeps TMDB's casing ('Returning Series')
  const meta = [view.year ? String(view.year) : '', view.network || '', view.status || ''].filter(Boolean);
  const facts: [string, string, string][] = [
    ['Quality profile', view.profile_name || 'None', 'profile'],
    ['Path', view.path || '', 'path'],
    ['Size', bytes(view.size), 'size'],
    ['Episodes', `${view.have} of ${view.total}`, 'episodes'],
  ];
  return (
    <div data-testid="tv-detail" data-series-id={view.id}
      style={{ display: 'flex', flexDirection: 'column', gap: 16, fontFamily: FONT, width: '100%', maxWidth: DETAIL_MAX, margin: '0 auto', boxSizing: 'border-box' }}>
      <style>{detailCss + monitorFlagCss}</style>
      {phone ? (
        <div data-testid="tv-detail-backrow" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <button type="button" className="lp-focus" data-testid="tv-detail-back" onClick={onBack}
            style={{ ...BACK, color: '#3b82f6' }}>
            <BackChevron />Series
          </button>
          {!onArt && <button type="button" className="lp-focus tve-glass" data-testid="tv-detail-edit" onClick={() => setEditing(true)} style={{ ...glassBtn, height: 44 }}>Edit</button>}
        </div>
      ) : (
      <button type="button" className="lp-focus tvd-back" data-testid="tv-detail-back" onClick={onBack}
          style={{ ...BACK, alignSelf: 'flex-start', color: W70 }}>
          <BackChevron />Series
        </button>
      )}

      <TvSeriesHero title={view.title} meta={meta} facts={facts} bannerUrl={view.banner_url} phone={!wide} onArt={setOnArt}
        edit={<button type="button" className="lp-focus" data-testid="tv-detail-edit" onClick={() => setEditing(true)} style={wide ? { ...heroEditBtn, ...TYPE_DESK.edit } : heroEditBtn}>Edit</button>}
        flag={(
          <MonitorFlag on={view.monitored} title={view.title} testid="tv-detail-monitored" size={POSTER_FLAG} glyph={POSTER_GLYPH}
            onChange={on => void monitorShow(on)}
            style={{ borderRadius: 10, background: 'rgba(17,19,24,.72)', backdropFilter: 'blur(12px)',
              WebkitBackdropFilter: 'blur(12px)', color: view.monitored ? '#fff' : 'rgba(255,255,255,0.85)', boxShadow: '0 2px 8px rgba(0,0,0,0.45)' }} />
        )} />
      {!wide && !phone && !onArt && <HeaderActions onEdit={() => setEditing(true)} />}

      {view.seasons.map(s => {
        const list = eps.filter(e => e.season === s.season).sort((a, b) => b.episode - a.episode);
        const isOpen = !!opened?.has(s.season);
        const label = seasonName(s.season);
        return (
          <section key={s.season} data-testid="tv-detail-season" data-season={s.season} data-open={isOpen || undefined}
            style={{ borderRadius: 16, border: '1px solid rgba(255,255,255,0.08)', background: GLASS, overflow: 'hidden' }}>
            <div className="tvd-season-head" style={{ display: 'flex', alignItems: 'center', gap: wide ? 12 : 8, minHeight: wide ? TYPE_DESK.seasonHeadH : 56, padding: wide ? '0 12px 0 16px' : '0 8px 0 12px' }}>
              <MonitorFlag on={s.monitored} title={label} testid="tv-detail-season-monitored" onChange={v => void monitorSeason(s.season, v)} />
              <button type="button" className="lp-focus" data-testid="tv-detail-season-toggle" aria-expanded={isOpen}
                onClick={() => setOpen(o => { const n = new Set(o || opened || []); if (n.has(s.season)) n.delete(s.season); else n.add(s.season); return n; })}
                style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: wide ? TYPE_DESK.seasonBtnGap : 10, height: 44, padding: 0, border: 'none', background: 'none',
                  cursor: 'pointer', fontFamily: FONT, textAlign: 'left', color: W95 }}>
                <svg width={wide ? TYPE_DESK.chevron : 16} height={wide ? TYPE_DESK.chevron : 16} viewBox="0 0 24 24" fill="none" stroke={W50} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
                  style={{ flex: 'none', transform: isOpen ? 'rotate(90deg)' : undefined, transition: 'transform 150ms' }}><path d="M9 6l6 6-6 6" /></svg>
                <span style={{ ...(wide ? TYPE_DESK.seasonName : { fontSize: 16, fontWeight: 600 }), whiteSpace: 'nowrap' }}>{label}</span>
                <span data-testid="tv-detail-season-count" style={{ ...(wide ? TYPE_DESK.seasonCount : { fontSize: 13 }), color: s.total && s.have >= s.total ? W70 : W50, whiteSpace: 'nowrap' }}>
                  {`${s.have} of ${s.total}`}
                </span>
                {wide && s.size > 0 && <span style={{ ...TYPE_DESK.seasonSize, color: W50, whiteSpace: 'nowrap' }}>{bytes(s.size)}</span>}
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
                {!list.length && <div style={{ padding: '12px 16px', ...(wide ? TYPE_DESK.empty : { fontSize: 14 }), color: W50, borderTop: LINE }}>No episodes</div>}
              </div>
            )}
          </section>
        );
      })}

      {menu !== null && (() => {
        const e = eps.find(x => x.id === menu);
        if (!e) return null;
        const c = code(e.season, e.episode);
        return (
          <EpisodeSheet heading={`${view.title} \u00b7 ${c}`} title={e.title || `Episode ${e.episode}`}
            sub={[DETAIL_STATE_WORD[e.state], airET(e)].filter(Boolean).join(' \u00b7 ')} monitored={e.monitored}
            onClose={() => setMenu(null)}
            onSearch={() => { setMenu(null); void search({ episode_id: e.id }, c); }}
            onInteractive={() => { setMenu(null); setInteractive({ target: { episode_id: e.id }, label: `${view.title} \u00b7 ${c}`, epTitle: e.title || `Episode ${e.episode}` }); }}
            onMonitor={() => { setMenu(null); void monitorEpisode(e.id, !e.monitored); }} />
        );
      })()}
      {searchAsk}
      {editing && (
        <EditSeriesModal seriesId={view.id} title={view.title} onClose={() => setEditing(false)}
          onSaved={() => changed()} onDeleted={() => { onChanged?.(); onBack(); }} />
      )}
      {interactive && (
        <InteractiveModal seriesId={view.id} target={interactive.target} title={interactive.label} wide={wide} phone={phone} epTitle={interactive.epTitle}
          onClose={() => setInteractive(null)} onGrabbed={changed} />
      )}
    </div>
  );
}

/** v321 (Taylor via PM): the back link, 17px semibold with an 18px chevron, 44px tall */
export const BACK = { display: 'inline-flex', alignItems: 'center', gap: 4, minHeight: 44, padding: '0 10px 0 2px', border: 'none', background: 'none',
  fontFamily: FONT, fontSize: 17, fontWeight: 600, cursor: 'pointer', borderRadius: 8 } as const;
const BackChevron = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
    data-testid="tv-detail-back-chevron"><path d="M15 18l-6-6 6-6" /></svg>
);

function HeaderActions({ onEdit }: { onEdit: () => void }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 'none' }}>
      <button type="button" className="lp-focus tve-glass" data-testid="tv-detail-edit" onClick={onEdit} style={{ ...glassBtn, height: 36 }}>Edit</button>
    </div>
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
      style={{ position: 'relative', display: 'grid', alignItems: 'center', columnGap: wide ? 12 : 8, minHeight: wide ? TYPE_DESK.rowH : 64, borderTop: LINE,
        padding: wide ? '0 12px 0 16px' : '8px 8px 8px 12px', boxSizing: 'border-box',
        gridTemplateColumns: wide ? TYPE_DESK.rowColumns : '28px 24px minmax(0, 1fr) 36px 36px',
        background: focus ? 'rgba(255,255,255,0.05)' : undefined }}>
      <span data-testid="tv-detail-edge" aria-hidden="true" style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: EDGE_W, background: color }} />
      <MonitorFlag on={e.monitored} title={tag} testid="tv-detail-episode-monitored" onChange={onMonitor} />
      <span style={{ ...(wide ? TYPE_DESK.epNumber : { fontSize: 13 }), color: W50, fontVariantNumeric: 'tabular-nums', opacity: dim }}>{e.episode}</span>
      <span style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: wide ? TYPE_DESK.subGap : 2, opacity: dim }}>
        <span data-testid="tv-detail-episode-title" style={{ ...(wide ? TYPE_DESK.epTitle : { fontSize: 14, fontWeight: 500 }), color: W95, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{name}</span>
        {!wide && (
          <span style={{ fontSize: 12, color: W50, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            <span data-testid="tv-detail-status" style={{ color, fontWeight: 600 }}>{word}</span>
            {air && <span data-testid="tv-detail-air">{` \u00b7 ${air}`}</span>}
          </span>
        )}
        {wide && e.quality && <span style={{ ...TYPE_DESK.epSub, color: W50 }}>{e.quality}{e.size ? ` \u00b7 ${bytes(e.size)}` : ''}</span>}
      </span>
      {wide && <span data-testid="tv-detail-air" style={{ ...TYPE_DESK.air, color: W70, opacity: dim, whiteSpace: 'nowrap' }}>{air}</span>}
      {wide && <span data-testid="tv-detail-status" style={{ ...TYPE_DESK.status, color, opacity: dim, whiteSpace: 'nowrap' }}>{word}</span>}
      <IconBtn kind="search" label={`Search ${tag}`} busy={busy} onClick={onSearch} testid="tv-detail-episode-search" />
      <IconBtn kind="interactive" label={`Interactive search ${tag}`} onClick={onInteractive} testid="tv-detail-episode-interactive" />
    </div>
  );
}

type GrabState = { busy?: boolean; done?: boolean; error?: string };

export function InteractiveModal({ seriesId, target, title, wide, phone = false, epTitle, onClose, onGrabbed }: {
  seriesId: number; target: SearchTarget; title: string; wide: boolean; phone?: boolean; epTitle?: string; onClose: () => void; onGrabbed: () => void;
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
    if (out.ok && out.data.ok && out.data.grabbed !== false) { setGrabs(g => ({ ...g, [k]: { done: true } })); onGrabbed(); if (phone) onClose(); return; }
    setGrabs(g => ({ ...g, [k]: { error: out.ok ? out.data.error || 'Not grabbed' : out.error } }));
  };
  const list = res?.releases ?? [];
  const off = res ? !res.grabs_allowed : false;
  if (phone) {
    return (
      <PhoneInteractive heading={res ? `${title} \u00b7 ${list.length} ${list.length === 1 ? 'release' : 'releases'}` : title} title={epTitle || title}
        res={res} error={error} off={off} grabs={grabs} onClose={onClose}
        onGrab={(r, k) => void grab(r, k).then(() => undefined)} />
    );
  }
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

/** v187 phone: the episode row menu as a bottom sheet (scrim tap, swipe down or Escape closes). */
export function EpisodeSheet({ heading, title, sub, monitored, onClose, onSearch, onInteractive, onMonitor }: {
  heading: string; title: string; sub: string; monitored: boolean;
  onClose: () => void; onSearch: () => void; onInteractive: () => void; onMonitor: () => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const titleRef = useRef<HTMLHeadingElement | null>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const swipe = useSwipeToDismiss({ scrollRef: ref, onDismiss: () => closeRef.current() });
  // runs on open and close only: a parent re-render (a new inline onClose) must not move focus or toggle the scroll lock
  useEffect(() => {
    const back = document.activeElement as HTMLElement | null;
    const prev = document.body.style.overflow; document.body.style.overflow = 'hidden';
    titleRef.current?.focus();
    const key = (ev: KeyboardEvent) => { if (ev.key === 'Escape') closeRef.current(); };
    window.addEventListener('keydown', key);
    return () => { document.body.style.overflow = prev; window.removeEventListener('keydown', key); back?.focus?.(); };
  }, []);
  const act: React.CSSProperties = { height: 56, width: '100%', borderRadius: 14, border: '1px solid rgba(255,255,255,0.08)', background: GLASS,
    color: W95, fontFamily: FONT, fontSize: 16, fontWeight: 600, textAlign: 'left', padding: '0 16px', cursor: 'pointer' };
  return (
    <div className="luma-fullscreen luma-bleed" data-testid="tv-episode-sheet-scrim" onClick={onClose}
      style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(21,16,42,0.72)', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)',
        opacity: swipe.backdropOpacity, display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title} data-testid="tv-episode-sheet" onClick={ev => ev.stopPropagation()} {...swipe.handlers}
        style={{ margin: 8, width: '100%', maxWidth: 480, boxSizing: 'border-box', borderRadius: 24, padding: '8px 16px 22px', fontFamily: FONT,
          background: 'rgba(21,16,42,0.92)', backdropFilter: 'blur(40px)', WebkitBackdropFilter: 'blur(40px)', border: '1px solid rgba(255,255,255,0.08)',
          transform: `translateY(${swipe.translateY}px)`, transition: swipe.transition }}>
        <div aria-hidden="true" style={{ width: 36, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)', margin: '0 auto 12px' }} />
        <div style={{ fontSize: 12, color: W70 }}>{heading}</div>
        <h3 ref={titleRef} tabIndex={-1} style={{ margin: '2px 0', fontSize: 17, fontWeight: 700, color: W95, outline: 'none', overflowWrap: 'anywhere' }}>{title}</h3>
        {sub && <div style={{ fontSize: 13, color: W70 }}>{sub}</div>}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 16 }}>
          <button type="button" className="lp-focus" data-testid="tv-sheet-search" style={act} onClick={onSearch}>Search</button>
          <button type="button" className="lp-focus" data-testid="tv-sheet-interactive" style={act} onClick={onInteractive}>Interactive search</button>
          <button type="button" className="lp-focus" data-testid="tv-sheet-monitor" aria-pressed={monitored} style={act} onClick={onMonitor}>{monitored ? 'Monitored' : 'Unmonitored'}</button>
        </div>
      </div>
    </div>
  );
}

type SortKey = 'seeders' | 'size' | 'quality';
const QRANK = (r: Release) => { const q = (r.quality || '').toLowerCase(); const n = parseInt(q, 10) || 0; return n * 10 + (q.includes('bluray') || q.includes('remux') ? 2 : q.includes('web') ? 1 : 0); };

/** v196 phone: Interactive search as a full-screen sheet (section 5); the fetch and grab stay in InteractiveModal. */
function PhoneInteractive({ heading, title, res, error, off, grabs, onClose, onGrab }: {
  heading: string; title: string; res: InteractiveResult | null; error: string; off: boolean; grabs: Record<string, GrabState>;
  onClose: () => void; onGrab: (r: Release, k: string) => void;
}) {
  const [sort, setSort] = useState<SortKey>('seeders');
  const [openK, setOpenK] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const swipe = useSwipeToDismiss({ scrollRef: ref, onDismiss: () => closeRef.current() });
  useEffect(() => {
    const prev = document.body.style.overflow; document.body.style.overflow = 'hidden';
    const key = (ev: KeyboardEvent) => { if (ev.key === 'Escape') closeRef.current(); };
    window.addEventListener('keydown', key);
    return () => { document.body.style.overflow = prev; window.removeEventListener('keydown', key); };
  }, []);
  const list = [...(res?.releases ?? [])].sort((a, b) =>
    sort === 'size' ? b.size - a.size : sort === 'quality' ? QRANK(b) - QRANK(a) || b.seeders - a.seeders : b.seeders - a.seeders);
  const segBtn = (on: boolean): React.CSSProperties => ({ flex: 1, height: 36, borderRadius: 10, border: 'none', fontFamily: FONT, fontSize: 14, fontWeight: 600, cursor: 'pointer',
    background: on ? '#3b82f6' : 'transparent', color: on ? '#fff' : W70 });
  return (
    <div className="luma-fullscreen luma-bleed" data-testid="tv-interactive-scrim" onClick={onClose}
      style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(21,16,42,0.72)', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)', opacity: swipe.backdropOpacity }}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title} data-testid="tv-interactive" onClick={ev => ev.stopPropagation()} {...swipe.handlers}
        style={{ position: 'absolute', top: 47, left: 0, right: 0, bottom: 0, overflowY: 'auto', borderRadius: '20px 20px 0 0', padding: '8px 0 22px', fontFamily: FONT,
          background: 'rgba(21,16,42,0.92)', backdropFilter: 'blur(40px)', WebkitBackdropFilter: 'blur(40px)', transform: `translateY(${swipe.translateY}px)`, transition: swipe.transition }}>
        <div aria-hidden="true" style={{ width: 36, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)', margin: '0 auto 8px' }} />
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '0 8px 0 16px' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div data-testid="tv-interactive-count" style={{ fontSize: 12, color: W70 }}>{heading}</div>
            <h3 style={{ margin: '2px 0 0', fontSize: 17, fontWeight: 700, color: W95, overflowWrap: 'anywhere' }}>{title}</h3>
          </div>
          <button type="button" className="lp-focus tve-glass" data-testid="tv-interactive-done" aria-label="Close" onClick={onClose}
            style={{ ...glassBtn, width: 44, height: 44, padding: 0, fontSize: 20 }}>{'\u00d7'}</button>
        </div>
        <div role="group" aria-label="Sort" style={{ display: 'flex', gap: 4, padding: 4, margin: '12px 16px', borderRadius: 14, background: GLASS }}>
          {(['seeders', 'size', 'quality'] as const).map(k => (
            <button key={k} type="button" data-testid={`tv-interactive-sort-${k}`} aria-pressed={sort === k} onClick={() => setSort(k)} style={segBtn(sort === k)}>
              {k === 'seeders' ? 'Seeders' : k === 'size' ? 'Size' : 'Quality'}
            </button>
          ))}
        </div>
        {error && !list.length ? (
          <div role="alert" data-testid="tv-interactive-error" style={{ minHeight: 160, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, color: W70 }}>{error}</div>
        ) : !res ? (
          <div data-testid="tv-interactive-loading" aria-busy="true">
            {[0, 1, 2, 3, 4, 5, 6].map(i => <div key={i} style={{ height: 64, borderTop: i ? LINE : 'none', background: GLASS, opacity: 0.6 }} />)}
          </div>
        ) : !list.length ? (
          <div data-testid="tv-interactive-empty" style={{ minHeight: 160, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, color: W70 }}>No releases</div>
        ) : (
          <div role="list" aria-label="Releases" data-testid="tv-interactive-list">
            {list.map((r, i) => {
              const k = `${r.tracker}|${r.title}|${r.size}`;
              const g = grabs[k] || {};
              const open = openK === k;
              const can = r.grabbable && !g.done && !g.busy;
              return (
                <div key={k} role="listitem" data-testid="tv-interactive-row" data-grabbable={r.grabbable || undefined} data-open={open || undefined}
                  style={open ? { margin: '4px 8px', borderRadius: 14, background: GLASS } : { borderTop: i ? LINE : 'none' }}>
                  <button type="button" className="lp-focus" aria-expanded={open} data-testid="tv-interactive-toggle" onClick={() => setOpenK(open ? null : k)}
                    style={{ width: '100%', minHeight: 64, display: 'flex', alignItems: 'center', gap: 8, padding: '12px 8px 12px 16px', border: 'none', background: 'none',
                      textAlign: 'left', cursor: 'pointer', fontFamily: FONT, color: W95 }}>
                    <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
                      <span style={{ fontSize: 13, fontWeight: 500, wordBreak: 'break-all' }}>{r.title}</span>
                      <span style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, fontSize: 12, color: W70 }}>
                        {(r.quality_label || r.quality) && <span style={{ height: 24, display: 'inline-flex', alignItems: 'center', padding: '0 8px', borderRadius: 8, background: GLASS, fontWeight: 500, color: W95 }}>{r.quality_label || r.quality}</span>}
                        <span>{`${r.seeders} seeders`}</span>
                        {r.rejections.length > 0 && <span data-testid="tv-interactive-rejections" style={{ fontWeight: 500, color: r.grabbable ? '#f59e0b' : RED }}>{r.rejections.join(' \u00b7 ')}</span>}
                      </span>
                    </span>
                    <span aria-hidden="true" style={{ width: 44, height: 44, flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', color: W70,
                      transform: open ? 'rotate(90deg)' : undefined }}>{'\u203a'}</span>
                  </button>
                  {open && (
                    <div style={{ padding: '0 16px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
                      <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                        <div><dt style={{ fontSize: 12, color: 'rgba(255,255,255,0.55)' }}>Size</dt><dd style={{ margin: 0, fontSize: 14, fontWeight: 600, color: W95 }}>{bytes(r.size)}</dd></div>
                        <div><dt style={{ fontSize: 12, color: 'rgba(255,255,255,0.55)' }}>Tracker</dt><dd style={{ margin: 0, fontSize: 14, fontWeight: 600, color: W95 }}>{r.tracker || ''}</dd></div>
                      </dl>
                      {g.error && <span role="alert" data-testid="tv-interactive-grab-error" style={{ fontSize: 12, color: RED }}>{g.error}</span>}
                      {!off && (
                        <button type="button" className="lp-focus tve-primary" data-testid="tv-interactive-grab" disabled={!can} aria-label={`Grab ${r.title}`}
                          onClick={() => onGrab(r, k)} style={{ ...primaryBtn(can), height: 44, width: '100%' }}>
                          {g.done ? 'Grabbed' : 'Grab'}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
