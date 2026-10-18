/** Settings > TV > Calendar (docs/design/tv.md §2, §3): the week's episodes by
 * air date. Content 1000px and wider gets the 7-column week grid; narrower
 * (phones, 1024 desktops) gets the agenda by day with sticky day headers. The
 * current week is followed by Upcoming (the next 3 weeks). */
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { FONT } from '@/components/start/StartUI';
import { ChevronIcon, ChevronLeftIcon } from '@/components/nav/AppNav';
import { Poster } from './TvImport';
import {
  STATE_COLOR, STATE_WORD, addDays, agendaLabel, byDay, code, columnLabel, episodeState, loadCalendar, weekDays, weekLabel, weekStart,
  type CalEpisode, type CalendarData, type Day,
} from './tvCalendarApi';

export const GRID_MIN_W = 1000;
export const UPCOMING_WEEKS = 3;
const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';
const ACCENT = '#3b82f6';

/** The content width (ResizeObserver); null before the first measure or without ResizeObserver. */
export function useContentWidth(ref: React.RefObject<HTMLElement | null>): number | null {
  const [w, setW] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(([e]) => { if (e) setW(e.contentRect.width); });
    ro.observe(el);
    if (el.clientWidth) setW(el.clientWidth);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

export function TvCalendar({ wide, today, week, onWeek, onOpen, reload = 0 }: {
  wide: boolean; today: Day; week: Day; onWeek: (start: Day) => void; onOpen: (e: CalEpisode) => void; reload?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const width = useContentWidth(box);
  const grid = width == null ? wide : width >= GRID_MIN_W;
  const current = week === weekStart(today);
  const days = current ? 7 * (1 + UPCOMING_WEEKS) : 7;
  const [data, setData] = useState<{ key: string; cal: CalendarData } | null>(null);
  const [failed, setFailed] = useState('');
  const key = `${week}/${days}/${reload}`;
  useEffect(() => {
    let live = true;
    setFailed('');
    void loadCalendar(week, days).then(r => {
      if (!live) return;
      if (r.ok) setData({ key, cal: r.data }); else setFailed(key);
    });
    return () => { live = false; };
  }, [week, days, key]);
  const loading = data?.key !== key && failed !== key;
  const end = addDays(week, 7);
  const eps = data?.key === key ? data.cal.episodes : [];
  const inWeek = eps.filter(e => e.air_date >= week && e.air_date < end);
  const upcoming = current ? eps.filter(e => e.air_date >= end) : [];

  return (
    <div ref={box} data-testid="tv-calendar" data-layout={grid ? 'grid' : 'agenda'} style={{ fontFamily: FONT }}>
      <Toolbar grid={grid} label={weekLabel(week)} current={current}
        onPrev={() => onWeek(addDays(week, -7))} onNext={() => onWeek(addDays(week, 7))}
        onToday={() => onWeek(weekStart(today))} />
      <div style={{ marginTop: 16 }}>
        {failed === key ? (
          <div role="alert" style={emptyCard}>{"Couldn't load calendar"}</div>
        ) : loading ? (
          grid ? <GridSkeleton /> : <AgendaSkeleton />
        ) : !inWeek.length ? (
          <div data-testid="tv-cal-empty" style={emptyCard}>No episodes this week</div>
        ) : grid ? (
          <WeekGrid week={week} today={today} eps={inWeek} onOpen={onOpen} />
        ) : (
          <Agenda eps={inWeek} today={today} onOpen={onOpen} />
        )}
      </div>
      {current && !loading && failed !== key && upcoming.length > 0 && (
        <section data-testid="tv-cal-upcoming" style={{ marginTop: 28 }}>
          <h3 style={upLabel}>Upcoming</h3>
          <Agenda eps={upcoming} today={today} onOpen={onOpen} />
        </section>
      )}
    </div>
  );
}

function Toolbar({ grid, label, current, onPrev, onNext, onToday }: {
  grid: boolean; label: string; current: boolean; onPrev: () => void; onNext: () => void; onToday: () => void;
}) {
  const s = grid ? 36 : 44;
  const prev = <button type="button" aria-label="Previous week" data-testid="tv-cal-prev" className="lp-focus tvc-glass" onClick={onPrev} style={iconBtn(s)}><ChevronLeftIcon size={18} /></button>;
  const next = <button type="button" aria-label="Next week" data-testid="tv-cal-next" className="lp-focus tvc-glass" onClick={onNext} style={iconBtn(s)}><ChevronIcon size={18} /></button>;
  const today = !current && <button type="button" data-testid="tv-cal-today" className="lp-focus tvc-glass" onClick={onToday} style={pill(s)}>Today</button>;
  const text = <span data-testid="tv-cal-label" aria-live="polite" style={{ fontSize: 15, fontWeight: 600, color: W95, whiteSpace: 'nowrap' }}>{label}</span>;
  if (grid) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {prev}{next}{today}
        <span style={{ flex: 1 }} />
        {text}
      </div>
    );
  }
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      {prev}
      <span style={{ flex: 1, textAlign: 'center', minWidth: 0 }}>{text}</span>
      {next}
      {today}
    </div>
  );
}

function WeekGrid({ week, today, eps, onOpen }: { week: Day; today: Day; eps: CalEpisode[]; onOpen: (e: CalEpisode) => void }) {
  const days = weekDays(week);
  return (
    <div data-testid="tv-cal-grid" style={gridStyle}>
      {days.map(d => {
        const on = d === today;
        const list = eps.filter(e => e.air_date === d);
        return (
          <div key={d} data-testid="tv-cal-day" data-day={d} style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 }}>
            <div aria-current={on ? 'date' : undefined}
              style={{ display: 'flex', alignItems: 'center', gap: 6, height: 20, fontSize: 12, fontWeight: 600, color: on ? ACCENT : W50 }}>
              {columnLabel(d)}
              {on && <span aria-hidden="true" style={{ width: 4, height: 4, borderRadius: 2, background: ACCENT }} />}
            </div>
            {list.map(e => <GridCard key={e.episode_id} e={e} today={today} onOpen={onOpen} />)}
          </div>
        );
      })}
    </div>
  );
}

function GridCard({ e, today, onOpen }: { e: CalEpisode; today: Day; onOpen: (e: CalEpisode) => void }) {
  const st = episodeState(e, today);
  return (
    <button type="button" data-testid="tv-cal-card" data-state={st} aria-label={`${STATE_WORD[st]}: ${e.show} ${code(e.season, e.episode)}`}
      className="lp-focus tvc-card" onClick={() => onOpen(e)}
      style={{ ...cardBtn, padding: 8, borderRadius: 10, gap: 8, alignItems: 'flex-start' }}>
      <Stripe color={STATE_COLOR[st]} />
      <Poster path={e.poster_path} w={28} h={42} tmdbLogo />
      <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{ ...one, fontSize: 13, fontWeight: 600, color: W95 }}>{e.show}</span>
        <span style={{ fontSize: 12, lineHeight: '16px', color: W70, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden', overflowWrap: 'anywhere' }}>
          {epLine(e)}
        </span>
        {(e.air_time || st === 'downloaded') && (
          <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: W50 }}>
            {st === 'downloaded' && <Check />}
            {e.air_time}
          </span>
        )}
      </span>
    </button>
  );
}

/** The agenda: one glass card per day with its sticky header; empty days are left out. */
export function Agenda({ eps, today, onOpen }: { eps: CalEpisode[]; today: Day; onOpen: (e: CalEpisode) => void }) {
  return (
    <div data-testid="tv-cal-agenda" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {byDay(eps).map(([d, list]) => (
        <section key={d} data-testid="tv-agenda-day" data-day={d}>
          <h4 style={dayHead}>{agendaLabel(d, today)}</h4>
          <div style={glassCard}>
            {list.map((e, i) => <AgendaRow key={e.episode_id} e={e} today={today} first={i === 0} onOpen={onOpen} />)}
          </div>
        </section>
      ))}
    </div>
  );
}

function AgendaRow({ e, today, first, onOpen }: { e: CalEpisode; today: Day; first: boolean; onOpen: (e: CalEpisode) => void }) {
  const st = episodeState(e, today);
  return (
    <button type="button" data-testid="tv-agenda-row" data-state={st} aria-label={`${STATE_WORD[st]}: ${e.show} ${code(e.season, e.episode)}`}
      className="lp-focus tvc-row" onClick={() => onOpen(e)}
      style={{ ...cardBtn, height: 72, padding: 12, gap: 12, alignItems: 'center', borderRadius: 0, border: 'none',
        borderTop: first ? 'none' : '1px solid rgba(255,255,255,0.06)', background: 'none' }}>
      <Stripe color={STATE_COLOR[st]} />
      <Poster path={e.poster_path} w={40} h={60} tmdbLogo />
      <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{ ...one, fontSize: 14, fontWeight: 600, color: W95 }}>{e.show}</span>
        <span style={{ ...one, fontSize: 13, color: W70 }}>{epLine(e)}</span>
      </span>
      <span style={{ flex: 'none', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2 }}>
        {e.air_time && <span style={{ fontSize: 13, color: W50 }}>{e.air_time}</span>}
        <span data-testid="tv-state-word" style={{ fontSize: 12, fontWeight: 600, color: STATE_COLOR[st] }}>{STATE_WORD[st]}</span>
      </span>
    </button>
  );
}

const epLine = (e: CalEpisode) => (e.title ? `${code(e.season, e.episode)} \u00b7 ${e.title}` : code(e.season, e.episode));

export const Stripe = ({ color }: { color: string }) => (
  <span aria-hidden="true" style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 3, background: color }} />
);
const Check = () => (
  <svg data-testid="tv-check" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" style={{ flex: 'none' }}>
    <path d="M2.5 6.2l2.3 2.3 4.7-5" fill="none" stroke="#22c55e" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

function GridSkeleton() {
  return (
    <div data-testid="tv-cal-loading" aria-busy="true" style={gridStyle}>
      {Array.from({ length: 7 }, (_, d) => (
        <div key={d} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <span className="tvc-pulse" style={{ height: 12, width: 44, margin: '4px 0', borderRadius: 4, background: 'rgba(255,255,255,0.04)' }} />
          {[0, 1, 2].map(i => <span key={i} className="tvc-pulse" style={{ height: 76, borderRadius: 10, background: 'rgba(255,255,255,0.04)' }} />)}
        </div>
      ))}
    </div>
  );
}
function AgendaSkeleton() {
  return (
    <div data-testid="tv-cal-loading" aria-busy="true" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {[0, 1].map(d => (
        <div key={d}>
          <span className="tvc-pulse" style={{ display: 'block', height: 13, width: 90, margin: '0 4px 8px', borderRadius: 4, background: 'rgba(255,255,255,0.04)' }} />
          <div style={{ ...glassCard, background: 'rgba(255,255,255,0.04)' }}>
            {[0, 1, 2].map(i => <span key={i} className="tvc-pulse" style={{ display: 'block', height: 72 }} />)}
          </div>
        </div>
      ))}
    </div>
  );
}

export const tvCalendarCss = `
.tvc-card{transition:background .15s}
.tvc-card:hover{background:rgba(255,255,255,.10)!important}
.tvc-row:hover{background:rgba(255,255,255,.04)!important}
.tvc-glass:hover{background:rgba(255,255,255,.10)!important}
@keyframes tvc-pulse{0%,100%{opacity:1}50%{opacity:.5}}
.tvc-pulse{animation:tvc-pulse 1.6s ease-in-out infinite}
@media (prefers-reduced-motion: reduce){.tvc-pulse{animation:none}}
`;

const one: React.CSSProperties = { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' };
const gridStyle: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', gap: 8, alignItems: 'start' };
export const glassCard: React.CSSProperties = {
  borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden',
};
const cardBtn: React.CSSProperties = {
  position: 'relative', overflow: 'hidden', display: 'flex', width: '100%', boxSizing: 'border-box', textAlign: 'left', cursor: 'pointer',
  background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', color: W95, fontFamily: FONT,
};
const emptyCard: React.CSSProperties = {
  ...glassCard, height: 160, display: 'flex', alignItems: 'center', justifyContent: 'center',
  fontSize: 15, fontWeight: 500, color: W70, fontFamily: FONT,
};
const dayHead: React.CSSProperties = {
  position: 'sticky', top: 0, zIndex: 2, margin: '0 -4px', padding: '8px 8px', fontSize: 13, fontWeight: 600, color: W70,
  borderRadius: 8, background: 'rgba(21,16,42,0.55)', backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)',
};
const upLabel: React.CSSProperties = { margin: '0 4px 8px', fontSize: 13, fontWeight: 600, color: W50 };
const iconBtn = (s: number): React.CSSProperties => ({
  width: s, height: s, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none', padding: 0,
  borderRadius: 10, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', color: W95, cursor: 'pointer',
});
const pill = (s: number): React.CSSProperties => ({
  height: s, padding: '0 16px', borderRadius: 999, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)',
  color: W95, fontSize: 14, fontWeight: 600, fontFamily: FONT, cursor: 'pointer', flex: 'none',
});
