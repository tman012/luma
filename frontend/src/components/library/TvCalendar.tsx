/** Settings > TV > Calendar (v1, desktop web): the week's episodes by TMDB air
 * date as an agenda (day headers, 72px rows), capped at 720px and centered.
 * A card opens the show's detail at that episode. The week toolbar: ‹ › (and Today) on the left, the range centered. */
import { useContext, useEffect, useState } from 'react';
import { ModalHostContext } from '@/contexts/ModalHostContext';
import { FONT } from '@/components/start/StartUI';
import { ChevronIcon, ChevronLeftIcon } from '@/components/nav/AppNav';
import { Poster } from './TvImport';
import { posterUrl, tvdbImg } from '@/utils/image';
import {
  STATE_COLOR, STATE_WORD, addDays, agendaLabel, byDay, code, sortDay, episodeState, loadCalendar, setEpisodeMonitored, shortDate, timeRange, weekLabel, weekStart,
  type CalEpisode, type CalendarData, type Day,
} from './tvCalendarApi';

export const CAL_MAX_W = 720;
export const REFRESH_RELOAD_MS = 6000;
export const POLL_MS = 60_000;
const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';

export function TvCalendar({ today, week, onWeek, reload = 0, toolbar = true }: {
  today: Day; week: Day; onWeek: (start: Day) => void; reload?: number;
  /** false: the page shows WeekControls in its top row instead */
  toolbar?: boolean;
}) {
  const [data, setData] = useState<{ key: string; cal: CalendarData } | null>(null);
  const [failed, setFailed] = useState('');
  const key = `${week}/${reload}`;
  useEffect(() => {
    let live = true;
    let timer = 0;
    setFailed('');
    void loadCalendar(week, 7).then(r => {
      if (!live) return;
      if (r.ok) setData({ key, cal: r.data }); else setFailed(key);
      // episode images / air times are being fetched for this week: ask once more, quietly
      if (r.ok && r.data.refreshing) {
        timer = window.setTimeout(() => {
          void loadCalendar(week, 7).then(r2 => { if (live && r2.ok) setData(prev => keepIfSame(prev, key, r2.data)); });
        }, REFRESH_RELOAD_MS);
      }
    });
    return () => { live = false; window.clearTimeout(timer); };
  }, [week, key]);
  // a file that lands shows as downloaded without a reload: ask again quietly on window focus and every
  // POLL_MS while the page is visible (the server links new files within about a minute)
  useEffect(() => {
    let live = true;
    let busy = false;
    const quiet = () => {
      if (busy || document.visibilityState !== 'visible') return;
      busy = true;
      void loadCalendar(week, 7).then(r => { busy = false; if (live && r.ok) setData(prev => keepIfSame(prev, key, r.data)); });
    };
    const onVis = () => { if (document.visibilityState === 'visible') quiet(); };
    const id = window.setInterval(quiet, POLL_MS);
    window.addEventListener('focus', quiet);
    document.addEventListener('visibilitychange', onVis);
    return () => {
      live = false; window.clearInterval(id);
      window.removeEventListener('focus', quiet); document.removeEventListener('visibilitychange', onVis);
    };
  }, [week, key]);
  const loading = data?.key !== key && failed !== key;
  const end = addDays(week, 7);
  const eps = data?.key === key ? data.cal.episodes.filter(e => e.air_date >= week && e.air_date < end) : [];

  return (
    <div data-testid="tv-calendar" style={{ fontFamily: FONT, width: '100%', containerType: 'inline-size', containerName: 'tvcal' }}>
      {toolbar && <WeekControls today={today} week={week} onWeek={onWeek} />}
      <div style={{ marginTop: toolbar ? 16 : 0 }}>
        {failed === key ? (
          <div role="alert" style={emptyCard}>{"Couldn't load calendar"}</div>
        ) : loading ? (
          <Placeholder />
        ) : !eps.length ? (
          <div data-testid="tv-cal-empty" style={emptyCard}>No episodes this week</div>
        ) : (
          <WeekGrid eps={eps} week={week} today={today} />
        )}
      </div>
    </div>
  );
}

/** The page's top row center: ‹ range › (prev, the week, next). */
export function WeekCenter({ week, onWeek }: { week: Day; onWeek: (start: Day) => void }) {
  return (
    <div data-testid="tv-cal-center" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
      <button type="button" aria-label="Previous week" data-testid="tv-cal-prev" className="lp-focus tvc-glass" onClick={() => onWeek(addDays(week, -7))} style={iconBtn}>
        <ChevronLeftIcon size={18} />
      </button>
      <span data-testid="tv-cal-label" aria-live="polite" style={{ textAlign: 'center', fontSize: 15, fontWeight: 600, color: W95, whiteSpace: 'nowrap' }}>
        {weekLabel(week)}
      </span>
      <button type="button" aria-label="Next week" data-testid="tv-cal-next" className="lp-focus tvc-glass" onClick={() => onWeek(addDays(week, 7))} style={iconBtn}>
        <ChevronIcon size={18} />
      </button>
    </div>
  );
}

/** Today, when another week is shown (the top row's right side). */
export function TodayButton({ today, week, onWeek }: { today: Day; week: Day; onWeek: (start: Day) => void }) {
  if (week === weekStart(today)) return null;
  return <button type="button" data-testid="tv-cal-today" className="lp-focus tvc-glass" onClick={() => onWeek(weekStart(today))} style={pill}>Today</button>;
}

/** A quiet re-ask changes nothing on screen unless an episode changed (no idle re-render). */
export function keepIfSame<T extends { key: string; cal: CalendarData }>(prev: T | null, key: string, cal: CalendarData): T | { key: string; cal: CalendarData } {
  if (prev && prev.key === key && JSON.stringify(prev.cal.episodes) === JSON.stringify(cal.episodes)) return prev;
  return { key, cal };
}

/** ‹ › (and Today) on the left, the week range centered. */
export function WeekControls({ today, week, onWeek }: { today: Day; week: Day; onWeek: (start: Day) => void }) {
  return (
    <Toolbar label={weekLabel(week)} showToday={week !== weekStart(today)}
      onPrev={() => onWeek(addDays(week, -7))} onNext={() => onWeek(addDays(week, 7))} onToday={() => onWeek(weekStart(today))} />
  );
}

function Toolbar({ label, showToday, onPrev, onNext, onToday }: {
  label: string; showToday: boolean; onPrev: () => void; onNext: () => void; onToday: () => void;
}) {
  return (
    <div data-testid="tv-cal-toolbar" style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center', gap: 8 }}>
      <div data-testid="tv-cal-nav" style={{ display: 'flex', alignItems: 'center', gap: 8, justifySelf: 'start' }}>
        <button type="button" aria-label="Previous week" data-testid="tv-cal-prev" className="lp-focus tvc-glass" onClick={onPrev} style={iconBtn}>
          <ChevronLeftIcon size={18} />
        </button>
        <button type="button" aria-label="Next week" data-testid="tv-cal-next" className="lp-focus tvc-glass" onClick={onNext} style={iconBtn}>
          <ChevronIcon size={18} />
        </button>
        {showToday && <button type="button" data-testid="tv-cal-today" className="lp-focus tvc-glass" onClick={onToday} style={pill}>Today</button>}
      </div>
      <span data-testid="tv-cal-label" aria-live="polite" style={{ textAlign: 'center', fontSize: 15, fontWeight: 600, color: W95, whiteSpace: 'nowrap' }}>
        {label}
      </span>
      <span aria-hidden="true" />
    </div>
  );
}

/** The week grid (default): 7 columns, Sunday first, one glass card per episode with a 3px state bar. */
export function WeekGrid({ eps, week, today }: { eps: CalEpisode[]; week: Day; today: Day }) {
  const days = Array.from({ length: 7 }, (_, i) => addDays(week, i));
  return (
    <div data-testid="tv-cal-grid" style={gridWrap}>
      {days.map(d => {
        const list = sortDay(eps.filter(e => e.air_date === d));
        const isToday = d === today;
        return (
          <section key={d} data-testid="tv-grid-day" data-day={d} style={gridCol}>
            <h4 style={{ ...gridHead, background: isToday ? 'rgba(255,255,255,0.10)' : 'rgba(255,255,255,0.04)', color: isToday ? '#3b82f6' : W50 }}>
              {dayHeader(d)}
            </h4>
            {list.map(e => <GridCard key={e.episode_id} e={e} today={today} />)}
          </section>
        );
      })}
    </div>
  );
}

const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const dayHeader = (d: Day) => {
  const [y = 1970, m = 1, dd = 1] = d.split('-').map(Number);
  return `${DOW[new Date(y, m - 1, dd).getDay()]} ${m}/${dd}`;
};

/** Click or Enter/Space: the show's detail, at that season and episode (no-op without a TMDB id or a modal host). */
function useOpenEpisode(e: CalEpisode) {
  const host = useContext(ModalHostContext);
  if (!host || !e.tmdb_id) return {};
  const open = () => host.openTv({ tmdbId: e.tmdb_id as number, origin: 'tv_calendar',
    episodeContext: { seasonNumber: e.season, episodeNumber: e.episode } });
  return {
    role: 'button' as const, tabIndex: 0, 'data-clickable': 'true', className: 'lp-focus', onClick: open,
    onKeyDown: (k: React.KeyboardEvent) => { if (k.key === 'Enter' || k.key === ' ') { k.preventDefault(); open(); } },
  };
}

/** v158a: the card's own monitored state (the bookmark flips it at once; the server answer wins). */
function useMonitored(e: CalEpisode): [boolean, (on: boolean) => void] {
  const [mon, setMon] = useState(e.monitored !== false);
  useEffect(() => { setMon(e.monitored !== false); }, [e.monitored, e.episode_id]);
  const flip = (on: boolean) => {
    setMon(on);
    void setEpisodeMonitored(e.episode_id, on).then(r => { if (!r.ok) setMon(!on); });
  };
  return [mon, flip];
}

/** Hover bookmark (filled = monitored): toggles this episode only; never opens the detail. No label; aria only. */
export function Bookmark({ e, on, onToggle }: { e: CalEpisode; on: boolean; onToggle: (on: boolean) => void }) {
  const stop = (k: React.SyntheticEvent) => { k.stopPropagation(); };
  return (
    <button type="button" data-testid="tv-card-bookmark" className="tvc-bm lp-focus" aria-pressed={on}
      aria-label={`Monitored: ${e.show} ${code(e.season, e.episode)}`}
      onClick={k => { k.stopPropagation(); k.preventDefault(); onToggle(!on); }}
      onKeyDown={k => { if (k.key === 'Enter' || k.key === ' ') stop(k); }}
      style={{ position: 'absolute', top: 6, right: 6, zIndex: 2, width: 28, height: 28, padding: 0, borderRadius: 8, display: 'inline-flex',
        alignItems: 'center', justifyContent: 'center', background: 'rgba(21,16,42,.72)', border: '1px solid rgba(255,255,255,0.12)',
        backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)', color: W95, cursor: 'pointer' }}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill={on ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
        <path d="M6 3h12v18l-6-4.5L6 21z" />
      </svg>
    </button>
  );
}

/** Unmonitored cards render at ~0.5 opacity; the state edge stays full strength. */
export const DIM = 0.5;

function GridCard({ e, today }: { e: CalEpisode; today: Day }) {
  const st = episodeState(e, today);
  const open = useOpenEpisode(e);
  const [mon, flip] = useMonitored(e);
  return (
    <div data-testid="tv-agenda-row" data-state={st} data-monitored={mon ? 'true' : 'false'} aria-label={`${STATE_WORD[st]}: ${e.show} ${code(e.season, e.episode)}`} {...open}
      className={`tvc-card ${open.className || ''}`.trim()}
      style={{ cursor: open.onClick ? 'pointer' : undefined, position: 'relative', paddingLeft: EDGE_W, borderRadius: 8, background: 'rgba(255,255,255,0.06)',
        border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden' }}>
      <Stripe color={STATE_COLOR[st]} w={EDGE_W} />
      <Bookmark e={e} on={mon} onToggle={flip} />
      <div data-testid="tv-card-body" style={{ opacity: mon ? 1 : DIM, transition: 'opacity 150ms' }}>
        <Still e={e} />
        <div style={{ padding: 8, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span data-testid="tv-card-show" title={e.show} style={{ ...clamp2, fontSize: 13, fontWeight: 600, color: W95 }}>{e.show}</span>
          <span data-testid="tv-card-title" title={epLine(e)} style={{ ...one, fontSize: 12, color: W70 }}>{epLine(e)}</span>
          <span data-testid="tv-air" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, color: W50 }}>
            {timeRange(e.airstamp, e.runtime) || e.air_time || shortDate(e.air_date)}
          </span>
        </div>
      </div>
    </div>
  );
}

/** Still sources in order: TheTVDB image (key set; through Luma's image proxy), TMDB episode still, show backdrop. */
export const stillChain = (e: CalEpisode): string[] =>
  [tvdbImg(e.tvdb_image), posterUrl(e.still_path, 'w780'), posterUrl(e.backdrop_path, 'w780')].filter((u): u is string => !!u);

/** A 16:9 box reserved before load; an error moves to the next source, none left is a glass .04 tile. */
export function Still({ e }: { e: CalEpisode }) {
  const chain = stillChain(e);
  const [i, setI] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const src = chain[i];
  return (
    <div data-testid="tv-still" style={{ position: 'relative', width: '100%', aspectRatio: '16 / 9', background: 'rgba(255,255,255,0.04)', overflow: 'hidden' }}>
      {src && (
        <img key={src} src={src} alt={`${e.show} ${code(e.season, e.episode)}`} loading="lazy"
          onLoad={() => setLoaded(true)} onError={() => { setLoaded(false); setI(n => n + 1); }}
          style={{ display: 'block', width: '100%', aspectRatio: '16 / 9', objectFit: 'cover', opacity: loaded ? 1 : 0, transition: 'opacity 150ms' }} />
      )}
    </div>
  );
}

/** The agenda: a day header and one glass card per day; empty days are left out. */
export function Agenda({ eps, today }: { eps: CalEpisode[]; today: Day }) {
  return (
    <div data-testid="tv-cal-agenda" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {byDay(eps).map(([d, list]) => (
        <section key={d} data-testid="tv-agenda-day" data-day={d}>
          <h4 style={dayHead}>{agendaLabel(d, today)}</h4>
          <div style={glassCard}>
            {list.map((e, i) => <AgendaRow key={e.episode_id} e={e} today={today} first={i === 0} />)}
          </div>
        </section>
      ))}
    </div>
  );
}

function AgendaRow({ e, today, first }: { e: CalEpisode; today: Day; first: boolean }) {
  const st = episodeState(e, today);
  const open = useOpenEpisode(e);
  const [mon] = useMonitored(e);
  return (
    <div data-testid="tv-agenda-row" data-state={st} data-monitored={mon ? 'true' : 'false'} aria-label={`${STATE_WORD[st]}: ${e.show} ${code(e.season, e.episode)}`} {...open}
      style={{ cursor: open.onClick ? 'pointer' : undefined, position: 'relative', display: 'flex', alignItems: 'center', height: 72, padding: '0 16px 0 15px', gap: 12, boxSizing: 'border-box',
        borderTop: first ? 'none' : '1px solid rgba(255,255,255,0.06)' }}>
      <Stripe color={STATE_COLOR[st]} />
      <span style={{ display: 'inline-flex', opacity: mon ? 1 : DIM }}><Poster path={e.poster_path} w={32} h={48} tmdbLogo /></span>
      <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2, opacity: mon ? 1 : DIM }}>
        <span style={{ ...one, fontSize: 14, fontWeight: 600, color: W95 }}>{e.show}</span>
        <span style={{ ...one, fontSize: 13, color: W70 }}>{epLine(e)}</span>
      </span>
      <span style={{ flex: 'none', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2 }}>
        <span data-testid="tv-air" style={{ fontSize: 13, color: W50 }}>{e.air_time ? `${shortDate(e.air_date)}, ${e.air_time}` : shortDate(e.air_date)}</span>
        <span data-testid="tv-state-word" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, fontWeight: 600, color: STATE_COLOR[st] }}>
          {st === 'downloaded' && <Check />}
          {STATE_WORD[st]}
        </span>
      </span>
    </div>
  );
}

const epLine = (e: CalEpisode) => (e.title ? `${code(e.season, e.episode)} \u00b7 ${e.title}` : code(e.season, e.episode));

export const Stripe = ({ color, w = 3 }: { color: string; w?: number }) => (
  <span aria-hidden="true" data-testid="tv-stripe" style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: w, background: color, zIndex: 1 }} />
);
/** The week-grid card's state edge: full card height, beside the still and the text. */
const EDGE_W = 4;
const Check = () => (
  <svg data-testid="tv-check" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" style={{ flex: 'none' }}>
    <path d="M2.5 6.2l2.3 2.3 4.7-5" fill="none" stroke="#22c55e" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

/** Quiet loading that mirrors the week grid: 7 day-header strips and 2–3 glass .04
 * cards per column at a card's height (16:9 still + text); no spinner, no motion. */
const LOADING_CARDS = [3, 2, 3, 2, 3, 2, 2];
function Placeholder() {
  return (
    <div data-testid="tv-cal-loading" aria-busy="true" style={gridWrap}>
      {LOADING_CARDS.map((n, c) => (
        <div key={c} data-testid="tv-cal-loading-day" style={gridCol}>
          <span aria-hidden="true" style={{ ...gridHead, display: 'block', background: 'rgba(255,255,255,0.04)' }}>{'\u00a0'}</span>
          {Array.from({ length: n }, (_, i) => (
            <span key={i} data-testid="tv-cal-loading-card" style={{ display: 'block', borderRadius: 8, overflow: 'hidden',
              border: '1px solid rgba(255,255,255,0.04)', background: 'rgba(255,255,255,0.04)' }}>
              <span style={{ display: 'block', aspectRatio: '16 / 9', background: 'rgba(255,255,255,0.02)' }} />
              <span style={{ display: 'block', height: CARD_TEXT_H }} />
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}

export const tvCalendarCss = `
:root{--tv-state-downloaded:#22c55e;--tv-state-missing:#ef4444;--tv-state-unaired:#3b82f6}
.tvc-row:hover{background:rgba(255,255,255,.04)!important}
.tvc-glass:hover{background:rgba(255,255,255,.10)!important}
.tvc-card .tvc-bm{opacity:0;transition:opacity 120ms}
.tvc-card:hover .tvc-bm,.tvc-card:focus-within .tvc-bm,.tvc-bm:focus-visible{opacity:1}
.tvc-bm:hover{background:rgba(59,130,246,.35)!important}
@media (hover:none){.tvc-card .tvc-bm{opacity:1}}
@container tvcal (max-width: 999px){.tvc-card .tvc-bm{display:none!important}}
`;

const gridWrap: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', gap: 8 };
const gridCol: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 };
const gridHead: React.CSSProperties = { margin: 0, padding: '6px 8px', borderRadius: 8, fontSize: 12, fontWeight: 600, lineHeight: '16px' };
/** A card's text block: 8 + name 17 + 2 + episode 16 + 2 + time 16 + 8 (one-line name). */
const CARD_TEXT_H = 69;
const one: React.CSSProperties = { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' };
/** At most 2 lines, then an ellipsis (week-grid show names). */
const clamp2: React.CSSProperties = {
  display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden', overflowWrap: 'anywhere', lineHeight: '17px',
};
export const glassCard: React.CSSProperties = {
  borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden',
};
const emptyCard: React.CSSProperties = {
  ...glassCard, height: 160, display: 'flex', alignItems: 'center', justifyContent: 'center',
  fontSize: 15, fontWeight: 500, color: W70, fontFamily: FONT,
};
const dayHead: React.CSSProperties = { margin: '0 4px 8px', fontSize: 13, fontWeight: 600, color: W70, lineHeight: '21px' };
const iconBtn: React.CSSProperties = {
  width: 36, height: 36, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none', padding: 0,
  borderRadius: 10, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', color: W95, cursor: 'pointer',
};
const pill: React.CSSProperties = {
  height: 32, padding: '0 14px', borderRadius: 999, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)',
  color: W95, fontSize: 13, fontWeight: 600, fontFamily: FONT, cursor: 'pointer', flex: 'none', marginLeft: 4,
};
