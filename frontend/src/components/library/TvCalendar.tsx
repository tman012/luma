/** Settings > TV > Calendar (v1, desktop web): the week's episodes by TMDB air
 * date as an agenda (day headers, 72px rows), capped at 720px and centered.
 * A card opens the show's detail at that episode. The week toolbar: ‹ › (and Today) on the left, the range centered. */
import { createContext, useContext, useEffect, useState } from 'react';
import { ModalHostContext } from '@/contexts/ModalHostContext';
import { FONT } from '@/components/start/StartUI';
import { ChevronIcon, ChevronLeftIcon } from '@/components/nav/AppNav';
import { Poster } from './TvImport';
import {
  STATE_COLOR, STATE_HEX, STATE_WORD, addDays, agendaLabel, byDay, code, sortDay, episodeState, loadCalendar, setEpisodeMonitored, shortDate, timeRangeParts, weekDays, weekLabel, weekStart,
  type CalEpisode, type CalendarData, type Day, type EpisodeState,
} from './tvCalendarApi';

export const CAL_MAX_W = 720;
/** v182: Settings > TV opens a card's show on its own series detail page (series id, episode id); without it,
 * the app's TV detail modal (ModalHostContext) as before. */
/** v184 (PM): the week grid hides unmonitored episodes (the episode, its season or its show). A card's own
 * bookmark hides it at once; a change made elsewhere arrives with the quiet poll, like a downloaded flip. */
const HideContext = createContext<((id: number, hidden: boolean) => void) | null>(null);
export const shown = (e: CalEpisode) => e.monitored !== false;

export const OpenShowContext = createContext<((seriesId: number, episodeId?: number | null) => void) | null>(null);
export const REFRESH_RELOAD_MS = 6000;
export const POLL_MS = 60_000;
const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';

export function TvCalendar({ today, week, onWeek, reload = 0, toolbar = true, phone: forcePhone = false }: {
  today: Day; week: Day; onWeek: (start: Day) => void; reload?: number;
  /** v204: the home screen's TV calendar sheet is always the phone layout */
  phone?: boolean;
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
      // air times are being fetched for this week: ask once more, quietly
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
  const isPhone = usePhone();
  const phone = forcePhone || isPhone;
  const [dayPick, setDayPick] = useState<Day>(today);
  const inWeek = (d: Day) => d >= week && d < addDays(week, 7);
  const day = inWeek(dayPick) ? dayPick : inWeek(today) ? today : week;
  const step = (delta: number) => { const n = addDays(day, delta); setDayPick(n); if (!inWeek(n)) onWeek(weekStart(n)); };
  const loading = data?.key !== key && failed !== key;
  const end = addDays(week, 7);
  const eps = data?.key === key ? data.cal.episodes.filter(e => e.air_date >= week && e.air_date < end && shown(e)) : [];

  return (
    <div data-testid="tv-calendar" style={{ fontFamily: FONT, width: '100%', containerType: 'inline-size', containerName: 'tvcal' }}>
      {/* v301/v222: while phone is loading, PhonePlaceholder owns the week strip (no DayHeader / no desktop grid). */}
      {phone ? (loading ? null : <DayHeader day={day} today={today} onStep={step} />) : toolbar && <WeekControls today={today} week={week} onWeek={onWeek} />}
      <div style={{ marginTop: toolbar || phone ? 16 : 0 }}>
        {failed === key ? (
          <div role="alert" style={emptyCard}>{"Couldn't load calendar"}</div>
        ) : loading ? (
          phone ? <PhonePlaceholder week={week} day={day} today={today} /> : <Placeholder />
        ) : phone ? (
          <PhoneDay eps={eps} day={day} today={today} />
        ) : (
          <>
            <WeekHero eps={eps} week={week} today={today} />
            {!eps.length ? (
              <div data-testid="tv-cal-empty" style={emptyCard}>No episodes this week</div>
            ) : (
              <WeekGrid eps={eps} week={week} today={today} />
            )}
          </>
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

/** The week grid (default): 7 columns, Sunday first, one text-only glass card per episode with a 4px state edge. */
/** v321 Designer D: This week count strip + Today pill above the week grid (desktop only). */
export function WeekHero({ eps, week, today }: { eps: CalEpisode[]; week: Day; today: Day }) {
  const counts = weekCounts(eps, today);
  const current = week === weekStart(today);
  const todayLabel = (() => {
    const [y = 1970, m = 1, dd = 1] = today.split('-').map(Number);
    const dow = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][new Date(y, m - 1, dd).getDay()];
    return `${dow} ${dd} · Today`;
  })();
  return (
    <div data-testid="tv-cal-hero" style={{ display: 'flex', alignItems: 'center', gap: 10, height: 40, marginBottom: 14, flex: 'none' }}>
      <div data-testid="tv-cal-this-week" style={{
        display: 'flex', alignItems: 'center', gap: 16, height: 40, padding: '0 16px', borderRadius: 12,
        background: 'rgba(255,255,255,.06)', border: '1px solid rgba(255,255,255,.08)',
      }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'rgba(255,255,255,.85)' }}>This week</span>
        <CountChip color={STATE_HEX.downloaded} n={counts.downloaded} />
        <CountChip color={STATE_HEX.missing} n={counts.missing} />
        <CountChip color={STATE_HEX.unaired} n={counts.unaired} />
      </div>
      {current && (
        <div data-testid="tv-cal-today-pill" style={{
          height: 40, padding: '0 14px', borderRadius: 12, display: 'inline-flex', alignItems: 'center',
          background: 'rgba(59,130,246,.16)', border: '1px solid rgba(59,130,246,.35)',
          fontSize: 13, fontWeight: 600, color: '#60a5fa',
        }}>{todayLabel}</div>
      )}
    </div>
  );
}

function CountChip({ color, n }: { color: string; n: number }) {
  return (
    <span data-testid="tv-cal-count" data-color={color} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 600, color }}>
      <i aria-hidden="true" style={{ width: 8, height: 8, borderRadius: '50%', background: color, display: 'inline-block' }} />
      {n}
    </span>
  );
}

export function weekCounts(eps: CalEpisode[], today: Day): Record<EpisodeState, number> {
  const out: Record<EpisodeState, number> = { downloaded: 0, missing: 0, unaired: 0 };
  for (const e of eps) out[episodeState(e, today)] += 1;
  return out;
}

export function WeekGrid({ eps: all, week, today }: { eps: CalEpisode[]; week: Day; today: Day }) {
  const days = Array.from({ length: 7 }, (_, i) => addDays(week, i));
  const [gone, setGone] = useState<Set<number>>(new Set());
  // a new answer from the server is the truth again for what it lists as unmonitored
  useEffect(() => { setGone(g => (g.size ? new Set([...g].filter(id => all.some(e => e.episode_id === id && shown(e)))) : g)); }, [all]);
  const hide = (id: number, hidden: boolean) => setGone(g => { const n = new Set(g); if (hidden) n.add(id); else n.delete(id); return n; });
  const eps = all.filter(e => shown(e) && !gone.has(e.episode_id));
  return (
    <HideContext.Provider value={hide}>
    <div data-testid="tv-cal-grid" style={gridWrap}>
      {days.map(d => {
        const list = sortDay(eps.filter(e => e.air_date === d));
        const isToday = d === today;
        return (
          <section key={d} data-testid="tv-grid-day" data-day={d} data-today={isToday ? 'true' : undefined}
            style={{
              ...gridCol,
              ...(isToday ? {
                boxShadow: 'inset 0 0 0 1px rgba(59,130,246,.35)',
                background: 'rgba(59,130,246,.05)',
                borderRadius: 10,
                padding: 6,
              } : { padding: 6 }),
            }}>
            <h4 style={{
              ...gridHead,
              background: isToday ? 'rgba(59,130,246,.22)' : 'rgba(255,255,255,0.04)',
              color: isToday ? '#60a5fa' : W50,
              display: 'flex', alignItems: 'center', justifyContent: 'center', height: 28, boxSizing: 'border-box',
            }}>
              {dayHeader(d)}
              {isToday && <span aria-hidden="true" data-testid="tv-today-dot" style={{
                display: 'inline-block', width: 4, height: 4, borderRadius: '50%', background: '#3b82f6', marginLeft: 6,
              }} />}
            </h4>
            {list.map(e => <GridCard key={e.episode_id} e={e} today={today} />)}
          </section>
        );
      })}
    </div>
    </HideContext.Provider>
  );
}

const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
/** v321 Designer D: `Sun 25` (weekday + day-of-month). */
const dayHeader = (d: Day) => {
  const [y = 1970, m = 1, dd = 1] = d.split('-').map(Number);
  return `${DOW[new Date(y, m - 1, dd).getDay()]} ${dd}`;
};

/** Click or Enter/Space: the show's detail, at that season and episode (no-op without a TMDB id or a modal host). */
function useOpenEpisode(e: CalEpisode) {
  const host = useContext(ModalHostContext);
  const openShow = useContext(OpenShowContext);
  if (openShow && e.series_id) {
    const go = () => openShow(e.series_id, e.episode_id);
    return {
      role: 'button' as const, tabIndex: 0, 'data-clickable': 'true', className: 'lp-focus', onClick: go,
      onKeyDown: (k: React.KeyboardEvent) => { if (k.key === 'Enter' || k.key === ' ') { k.preventDefault(); go(); } },
    };
  }
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
  const hide = useContext(HideContext);
  useEffect(() => { setMon(e.monitored !== false); }, [e.monitored, e.episode_id]);
  const flip = (on: boolean) => {
    setMon(on);
    if (!on) hide?.(e.episode_id, true);           // v184: unmonitored cards leave the week grid at once
    void setEpisodeMonitored(e.episode_id, on).then(r => { if (!r.ok) { setMon(!on); if (!on) hide?.(e.episode_id, false); } });
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
      style={{ position: 'absolute', top: 4, right: 4, zIndex: 2, width: 24, height: 24, padding: 0, borderRadius: 6, display: 'inline-flex',
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

/** v321 Designer D: glass card, 10px corners, 3px status stripe, 28×42 poster (or initial), show / SxxExx · title.
 * v325 (Taylor): the air time sits centered at the top; no Downloaded/quality chip, no green check: the stripe
 * (green have it, red missing) is the only got-it indicator. */
function GridCard({ e, today }: { e: CalEpisode; today: Day }) {
  const st = episodeState(e, today);
  const open = useOpenEpisode(e);
  const [mon, flip] = useMonitored(e);
  const air = airLine(e);
  const initial = (e.show.trim()[0] || '?').toUpperCase();
  return (
    <div data-testid="tv-agenda-row" data-state={st} data-monitored={mon ? 'true' : 'false'} aria-label={`${STATE_WORD[st]}: ${e.show} ${code(e.season, e.episode)}`} {...open}
      className={`tvc-card ${open.className || ''}`.trim()}
      style={{ cursor: open.onClick ? 'pointer' : undefined, position: 'relative', flex: 'none', borderRadius: 10, background: 'rgba(255,255,255,0.06)',
        border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden', padding: '8px 8px 8px 11px' }}>
      <Stripe color={STATE_COLOR[st]} w={3} />
      <Bookmark e={e} on={mon} onToggle={flip} />
      {air && (
        <span data-testid="tv-air" style={{ display: 'block', textAlign: 'center', margin: '0 0 6px', fontSize: AIR_SIZE, lineHeight: '16px', color: W50, whiteSpace: 'nowrap', opacity: mon ? 1 : DIM }}>
          {air}
        </span>
      )}
      <div data-testid="tv-card-body" style={{ opacity: mon ? 1 : DIM, transition: 'opacity 150ms', display: 'flex', gap: 8 }}>
        {e.poster_path ? (
          <span data-testid="tv-card-poster" style={{ flex: 'none' }}><Poster path={e.poster_path} w={28} h={42} /></span>
        ) : (
          <span aria-hidden="true" data-testid="tv-poster-initial" style={{
            width: 28, height: 42, borderRadius: 6, flex: 'none', display: 'grid', placeItems: 'center',
            background: 'linear-gradient(160deg, rgba(59,130,246,.35), rgba(15,23,42,.9))',
            border: '1px solid rgba(255,255,255,.1)', fontSize: 11, fontWeight: 700, color: 'rgba(255,255,255,.55)',
          }}>{initial}</span>
        )}
        <div className="tvc-text" style={{ minWidth: 0, flex: 1, display: 'flex', flexDirection: 'column', gap: 1 }}>
          <span data-testid="tv-card-show" title={e.show} style={{ ...one, fontSize: 13, fontWeight: 600, color: W95 }}>{e.show}</span>
          <span data-testid="tv-card-title" title={epLine(e)} style={{ ...one, fontSize: 12, color: W70 }}>{epLine(e)}</span>
        </div>
      </div>
    </div>
  );
}

/** The agenda: a day header and one glass card per day; empty days are left out. */
export function Agenda({ eps, today, noHead = false }: { eps: CalEpisode[]; today: Day; noHead?: boolean }) {
  return (
    <div data-testid="tv-cal-agenda" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {byDay(eps).map(([d, list]) => (
        <section key={d} data-testid="tv-agenda-day" data-day={d}>
          {!noHead && <h4 style={dayHead}>{agendaLabel(d, today)}</h4>}
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
      <span style={{ display: 'inline-flex', opacity: mon ? 1 : DIM }}><Poster path={e.poster_path} w={32} h={48} tmdbLogo /></span>
      <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2, opacity: mon ? 1 : DIM }}>
        <span style={{ ...one, fontSize: 14, fontWeight: 600, color: W95 }}>{e.show}</span>
        <span style={{ ...one, fontSize: 13, color: W70 }}>{epLine(e)}</span>
      </span>
      <span style={{ flex: 'none', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2 }}>
        <span data-testid="tv-air" style={{ fontSize: 13, color: W50 }}>{e.air_time ? `${shortDate(e.air_date)}, ${e.air_time}` : shortDate(e.air_date)}</span>
        <QualityChip e={e} />
      </span>
    </div>
  );
}

/** The week-grid card's bottom line: the start time in the viewer's zone ('9:00 PM'), or null (no line). */
export const airLine = (e: Pick<CalEpisode, 'airstamp'>, timeZone?: string): string | null => timeRangeParts(e.airstamp, null, timeZone)?.[0] ?? null;

const epLine = (e: CalEpisode) => (e.title ? `${code(e.season, e.episode)} \u00b7 ${e.title}` : code(e.season, e.episode));

/** v197 (Taylor, Q2): a downloaded episode's file quality as an outlined chip; nothing when there's no file.
 * One color, from the --quality-chip CSS variable (index.css). */
export function QualityChip({ e, style }: { e: Pick<CalEpisode, 'downloaded' | 'quality'>; style?: React.CSSProperties }) {
  if (!e.downloaded) return null;      // v211 (Taylor): a downloaded episode of unknown quality reads 'Downloaded'
  return (
    <span data-testid="tv-quality-chip" style={{ flex: 'none', display: 'inline-flex', alignItems: 'center', height: 24, padding: '0 8px', borderRadius: 6, boxSizing: 'border-box',
      fontSize: 12, fontWeight: 500, whiteSpace: 'nowrap', color: 'color-mix(in srgb, var(--quality-chip) 55%, #fff)', background: 'color-mix(in srgb, var(--quality-chip) 14%, transparent)', border: '1px solid color-mix(in srgb, var(--quality-chip) 45%, transparent)', ...style }}>
      {e.quality || 'Downloaded'}
    </span>
  );
}

export const Stripe = ({ color, w = 3 }: { color: string; w?: number }) => (
  <span aria-hidden="true" data-testid="tv-stripe" style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: w, background: color, zIndex: 1 }} />
);
/** v321: status edge is Stripe (3px); EDGE_W retired with text-only cards. */
/** Quiet loading that mirrors the week grid: 7 day-header strips and 2–3 glass .04
 * cards per column at a text-only card's height; no spinner, no motion. */
const LOADING_CARDS = [3, 2, 3, 2, 3, 2, 2];
function Placeholder() {
  return (
    <div data-testid="tv-cal-loading" aria-busy="true" style={gridWrap}>
      {LOADING_CARDS.map((n, c) => (
        <div key={c} data-testid="tv-cal-loading-day" style={gridCol}>
          <span aria-hidden="true" style={{ ...gridHead, display: 'block', background: 'rgba(255,255,255,0.04)' }}>{'\u00a0'}</span>
          {Array.from({ length: n }, (_, i) => (
            <span key={i} data-testid="tv-cal-loading-card" style={{ display: 'block', height: CARD_TEXT_H, borderRadius: 10,
              border: '1px solid rgba(255,255,255,0.04)', background: 'rgba(255,255,255,0.04)' }} />
          ))}
        </div>
      ))}
    </div>
  );
}

/** v222/v301: phone loading never paints the desktop week grid. v301 (Designer / SPEC-v187): week strip
 * without dots + 5 Home-style skeleton-shimmer cards with a neutral edge (variant-B 20-calendar-loading). */
function PhonePlaceholder({ week, day, today }: { week: Day; day: Day; today: Day }) {
  const days = weekDays(week);
  const sel = days.includes(day) ? day : days.includes(today) ? today : week;
  const dayLabel = new Date(`${sel}T12:00:00`).toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
  const widths = [
    ['70%', '50%'], ['55%', '35%'], ['80%', '60%'], ['62%', '42%'], ['74%', '54%'],
  ] as const;
  return (
    <div data-testid="tv-cal-loading-phone" aria-busy="true">
      <div data-testid="tv-cal-loading-strip" style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 4 }}>
        {days.map(d => {
          const on = d === sel;
          const dt = new Date(`${d}T12:00:00`);
          return (
            <div key={d} data-testid="tv-cal-loading-chip" aria-hidden="true"
              style={{ height: 68, minWidth: 0, borderRadius: 12, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 2,
                background: on ? '#3b82f6' : 'rgba(255,255,255,0.06)', color: on ? '#fff' : W95,
                border: !on && d === today ? '1px solid #3b82f6' : '1px solid rgba(255,255,255,0.08)' }}>
              <span style={{ fontSize: 12, fontWeight: 500, color: on ? '#fff' : W70 }}>{dt.toLocaleDateString('en-US', { weekday: 'short' })}</span>
              <span style={{ fontSize: 17, fontWeight: 700 }}>{dt.getDate()}</span>
              {/* SPEC-v187 loading: strip without dots — reserve the dots row height, leave empty */}
              <span style={{ display: 'block', height: 5 }} />
            </div>
          );
        })}
      </div>
      <div data-testid="tv-cal-loading-daylabel" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', margin: '16px 4px 8px' }}>
        <span style={{ fontSize: 15, fontWeight: 700, color: W95 }}>{dayLabel}</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {widths.map(([w1, w2], i) => (
          <div key={i} data-testid="tv-cal-loading-row" style={{ display: 'flex', borderRadius: 12, background: 'rgba(255,255,255,0.06)',
            border: '1px solid rgba(255,255,255,0.08)', minHeight: 56, overflow: 'hidden',
            boxShadow: 'inset 4px 0 0 rgba(255,255,255,0.14)' }}>
            <span aria-hidden="true" style={{ width: 76, flex: 'none', padding: '14px 0 0 16px', boxSizing: 'border-box' }}>
              <span className="skeleton-shimmer" style={{ display: 'block', width: 48, height: 12, borderRadius: 6 }} />
            </span>
            <span aria-hidden="true" style={{ flex: 1, minWidth: 0, padding: '12px 14px 12px 0' }}>
              <span className="skeleton-shimmer" style={{ display: 'block', width: w1, height: 15, borderRadius: 6 }} />
              <span className="skeleton-shimmer" style={{ display: 'block', width: w2, height: 12, borderRadius: 6, marginTop: 8 }} />
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** v166 (Designer): the card's time line is 12px at every width, never shrunk. v179: it is the start time
 * only ('9:00 PM', one nowrap line), so it never wraps. The show name keeps 28px on its right from 1000px,
 * where the 24px hover bookmark (4px from the edge) sits, so a wrapped name never runs under it. */
export const AIR_SIZE = 12;
export const tvCalendarCss = `
:root{--tv-state-downloaded:#22c55e;--tv-state-missing:#ef4444;--tv-state-unaired:#3b82f6}
.tvc-row:hover{background:rgba(255,255,255,.04)!important}
.tvc-glass:hover{background:rgba(255,255,255,.10)!important}
.tvc-card .tvc-bm{opacity:0;transition:opacity 120ms}
.tvc-card:hover .tvc-bm,.tvc-card:focus-within .tvc-bm,.tvc-bm:focus-visible{opacity:1}
.tvc-bm:hover{background:rgba(59,130,246,.35)!important}
@media (hover:none){.tvc-card .tvc-bm{opacity:1}}
@container tvcal (max-width: 999px){.tvc-card .tvc-bm{display:none!important}}
@container tvcal (min-width: 1000px){.tvc-card [data-testid=tv-card-show]{padding-right:28px}}
@container tvcal (max-width: 599px){.tvc-card [data-testid=tv-card-show]{-webkit-line-clamp:2!important}.tvc-card [data-testid=tv-card-title]{-webkit-line-clamp:1!important}}
`;

/** v179: rows and columns never stretch a card: the grid starts every day column at its content height
 * (align-items start), and a column is a plain flex stack of cards that keep their text height (flex none). */
const gridWrap: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', alignItems: 'start', gap: 8 };
const gridCol: React.CSSProperties = { display: 'flex', flexDirection: 'column', justifyContent: 'flex-start', gap: 8, minWidth: 0, boxSizing: 'border-box' };
const gridHead: React.CSSProperties = { margin: 0, padding: '0 8px', borderRadius: 8, fontSize: 12, fontWeight: 600, lineHeight: '16px' };
/** v321 Designer D: poster row ~8+42+8; loading skeletons match card height. */
export const CARD_TEXT_H = 74;
const one: React.CSSProperties = { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' };
/** v158c: week-grid captions wrap instead of clipping: the show name up to NAME_LINES, the episode line
 * up to EP_LINES, then an ellipsis. On a phone (calendar under 600px: day columns ~55px) they stay at 2 and 1
 * lines as in v162, so a name never breaks into one letter per line; from 600px (desktop 1100/1280, whose
 * calendar is under 1000px) they wrap. v164: was 999px, which clipped episode lines at 1100 and 1280; and 8/6 lines (was 5/3) so
 * the longest fixture titles fit the ~76px day columns at 1100. */
export const NAME_LINES = 8;
export const EP_LINES = 6;
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

const PHONE_MQ = '(max-width: 767px)';
/** Phone widths (under 768px) get the agenda with the 7-day strip; desktop is unchanged. */
export function usePhone(): boolean {
  const get = () => typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia(PHONE_MQ).matches;
  const [m, setM] = useState(get);
  useEffect(() => {
    if (!window.matchMedia) return;
    const q = window.matchMedia(PHONE_MQ); const on = () => setM(q.matches);
    q.addEventListener?.('change', on); return () => q.removeEventListener?.('change', on);
  }, []);
  return m;
}

/** v197 phone calendar header A (Taylor's pick): one day at a time, ‹ and ›, "Today" or the weekday bold and centered, the date under it. */
export function DayHeader({ day, today, onStep }: { day: Day; today: Day; onStep: (delta: number) => void }) {
  const dt = new Date(`${day}T12:00:00`);
  const big = day === today ? 'Today' : dt.toLocaleDateString('en-US', { weekday: 'long' });
  const small = dt.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
  const nav: React.CSSProperties = { width: 52, height: 52, flex: 'none', borderRadius: 14, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
    background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', color: W95, padding: 0 };
  return (
    <div data-testid="tv-day-header" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <button type="button" className="lp-focus tvc-glass" data-testid="tv-day-prev" aria-label="Previous day" onClick={() => onStep(-1)} style={nav}><ChevronLeftIcon size={18} /></button>
      <div aria-live="polite" style={{ flex: 1, minWidth: 0, textAlign: 'center' }}>
        <div data-testid="tv-day-title" style={{ fontSize: 20, fontWeight: 700, color: W95, lineHeight: '26px' }}>{big}</div>
        <div data-testid="tv-day-date" style={{ fontSize: 15, color: W70, lineHeight: '20px' }}>{small}</div>
      </div>
      <button type="button" className="lp-focus tvc-glass" data-testid="tv-day-next" aria-label="Next day" onClick={() => onStep(1)} style={nav}><ChevronIcon size={18} /></button>
    </div>
  );
}

/** The phone day list: the count on the right (the date is in the header), then the day's cards. */
export function PhoneDay({ eps, day, today }: { eps: CalEpisode[]; day: Day; today: Day }) {
  const list = eps.filter(e => e.air_date === day);
  if (!list.length) return <div data-testid="tv-cal-empty" style={{ ...emptyCard, minHeight: 240, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>No episodes</div>;
  return (
    <div data-testid="tv-cal-phone">
      <div data-testid="tv-day-count" style={{ margin: '0 4px 8px', fontSize: 15, fontWeight: 600, color: W95 }}>
        {list.length === 1 ? '1 episode' : `${list.length} episodes`}
      </div>
      {/* v211 (Taylor's pick U3, replaces T2's air-time headers): one card per episode, a leading ET time column */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {list.map(e => <U3Row key={e.episode_id} e={e} today={today} />)}
      </div>
    </div>
  );
}

/** '8:00 PM' in ET (America/New_York) from an episode's UTC airstamp; '' when untimed. */
export function etTime(airstamp: string | null | undefined): string {
  if (!airstamp) return '';
  const d = new Date(airstamp);
  return isNaN(d.getTime()) ? '' : d.toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' });
}

/** U3: the ET air time split for the time column, ['9:00', 'PM']; null when untimed (the column stays, blank). */
export function etParts(airstamp: string | null | undefined): [string, string] | null {
  const t = etTime(airstamp).replace(/\u202f/g, ' ');
  const m = /^(\d{1,2}:\d{2})\s*([AP]M)$/.exec(t);
  return m ? [m[1]!, m[2]!] : null;
}

/** v211 U3 row: the time column (bold time, muted AM/PM below) vertically centered, show and episode to the
 * right. v325 phone (Taylor): no quality/Downloaded chip and no check; the green/red left edge (Stripe, same
 * STATE_COLOR as the desktop week cards) is the only got-it indicator. */
function U3Row({ e, today }: { e: CalEpisode; today: Day }) {
  const st = episodeState(e, today);
  const open = useOpenEpisode(e);
  const [mon] = useMonitored(e);
  const t = etParts(e.airstamp);
  return (
    <div data-testid="tv-agenda-row" data-state={st} data-monitored={mon ? 'true' : 'false'} aria-label={`${STATE_WORD[st]}: ${e.show} ${code(e.season, e.episode)}`} {...open}
      className={`tv-u3-row ${open.className || ''}`.trim()}
      style={{ cursor: open.onClick ? 'pointer' : undefined, position: 'relative', overflow: 'hidden', display: 'flex', alignItems: 'center', gap: 12, minHeight: 82, padding: '10px 16px 10px 12px', boxSizing: 'border-box',
        borderRadius: 12, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)' }}>
      <Stripe color={STATE_COLOR[st]} w={3} />
      <span data-testid="tv-u3-time" style={{ flex: 'none', width: 50, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', alignSelf: 'center', opacity: mon ? 1 : DIM }}>
        {t && <span style={{ fontSize: 16, fontWeight: 700, color: W95, lineHeight: '20px' }}>{t[0]}</span>}
        {t && <span style={{ fontSize: 13, color: W50, lineHeight: '16px' }}>{t[1]}</span>}
      </span>
      <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 6, opacity: mon ? 1 : DIM }}>
        <span data-testid="tv-card-show" style={{ fontSize: 17, fontWeight: 600, color: W95, lineHeight: '22px' }}>{e.show}</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span data-testid="tv-card-title" style={{ ...one, flex: 1, minWidth: 0, fontSize: 14, color: W70 }}>{epLine(e)}</span>
        </span>
      </span>
    </div>
  );
}
