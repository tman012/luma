/** Settings > TV > Calendar (v1, desktop web): the week's episodes by TMDB air
 * date as an agenda (day headers, 72px rows), capped at 720px and centered.
 * Rows aren't clickable. The week toolbar has ‹ ›, the week label and Today. */
import { useEffect, useState } from 'react';
import { FONT } from '@/components/start/StartUI';
import { ChevronIcon, ChevronLeftIcon } from '@/components/nav/AppNav';
import { Poster } from './TvImport';
import {
  STATE_COLOR, STATE_WORD, addDays, agendaLabel, byDay, code, episodeState, loadCalendar, shortDate, timeRange, weekLabel, weekStart,
  type CalEpisode, type CalendarData, type Day,
} from './tvCalendarApi';

export const CAL_MAX_W = 720;
const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';

export function TvCalendar({ today, week, onWeek, reload = 0 }: {
  today: Day; week: Day; onWeek: (start: Day) => void; reload?: number;
}) {
  const [data, setData] = useState<{ key: string; cal: CalendarData } | null>(null);
  const [failed, setFailed] = useState('');
  const key = `${week}/${reload}`;
  useEffect(() => {
    let live = true;
    setFailed('');
    void loadCalendar(week, 7).then(r => {
      if (!live) return;
      if (r.ok) setData({ key, cal: r.data }); else setFailed(key);
    });
    return () => { live = false; };
  }, [week, key]);
  const loading = data?.key !== key && failed !== key;
  const end = addDays(week, 7);
  const eps = data?.key === key ? data.cal.episodes.filter(e => e.air_date >= week && e.air_date < end) : [];

  return (
    <div data-testid="tv-calendar" style={{ fontFamily: FONT, width: '100%' }}>
      <Toolbar label={weekLabel(week)} showToday={week !== weekStart(today)}
        onPrev={() => onWeek(addDays(week, -7))} onNext={() => onWeek(addDays(week, 7))} onToday={() => onWeek(weekStart(today))} />
      <div style={{ marginTop: 16 }}>
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

function Toolbar({ label, showToday, onPrev, onNext, onToday }: {
  label: string; showToday: boolean; onPrev: () => void; onNext: () => void; onToday: () => void;
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <button type="button" aria-label="Previous week" data-testid="tv-cal-prev" className="lp-focus tvc-glass" onClick={onPrev} style={iconBtn}>
        <ChevronLeftIcon size={18} />
      </button>
      <span data-testid="tv-cal-label" aria-live="polite" style={{ flex: 1, textAlign: 'center', fontSize: 15, fontWeight: 600, color: W95, whiteSpace: 'nowrap' }}>
        {label}
      </span>
      <button type="button" aria-label="Next week" data-testid="tv-cal-next" className="lp-focus tvc-glass" onClick={onNext} style={iconBtn}>
        <ChevronIcon size={18} />
      </button>
      {showToday
        ? <button type="button" data-testid="tv-cal-today" className="lp-focus tvc-glass" onClick={onToday} style={pill}>Today</button>
        : <span aria-hidden="true" style={{ width: 70, flex: 'none' }} />}
    </div>
  );
}

/** The week grid (default): 7 columns, Sunday first, one glass card per episode with a 3px state bar. */
export function WeekGrid({ eps, week, today }: { eps: CalEpisode[]; week: Day; today: Day }) {
  const days = Array.from({ length: 7 }, (_, i) => addDays(week, i));
  return (
    <div data-testid="tv-cal-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', gap: 8 }}>
      {days.map(d => {
        const list = eps.filter(e => e.air_date === d);
        const isToday = d === today;
        return (
          <section key={d} data-testid="tv-grid-day" data-day={d} style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
            <h4 style={{ margin: 0, padding: '6px 8px', borderRadius: 8, fontSize: 12, fontWeight: 600,
              background: isToday ? 'rgba(255,255,255,0.10)' : 'rgba(255,255,255,0.04)', color: isToday ? '#3b82f6' : W50 }}>
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

function GridCard({ e, today }: { e: CalEpisode; today: Day }) {
  const st = episodeState(e, today);
  return (
    <div data-testid="tv-agenda-row" data-state={st} aria-label={`${STATE_WORD[st]}: ${e.show} ${code(e.season, e.episode)}`}
      style={{ position: 'relative', padding: '8px 8px 8px 11px', borderRadius: 8, background: 'rgba(255,255,255,0.06)',
        border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden', display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Stripe color={STATE_COLOR[st]} />
      <span style={{ display: 'flex', gap: 6, alignItems: 'baseline' }}>
        <span style={{ ...one, flex: 1, minWidth: 0, fontSize: 13, fontWeight: 600, color: W95 }}>{e.show}</span>
        <span style={{ flex: 'none', fontSize: 11, color: W50 }}>{code(e.season, e.episode)}</span>
      </span>
      {e.title && <span style={{ ...one, fontSize: 12, color: W70 }}>{e.title}</span>}
      <span data-testid="tv-air" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, color: W50 }}>
        {st === 'downloaded' && <Check />}
        {timeRange(e.airstamp, e.runtime) || e.air_time || shortDate(e.air_date)}
      </span>
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
  return (
    <div data-testid="tv-agenda-row" data-state={st} aria-label={`${STATE_WORD[st]}: ${e.show} ${code(e.season, e.episode)}`}
      style={{ position: 'relative', display: 'flex', alignItems: 'center', height: 72, padding: '0 16px 0 15px', gap: 12, boxSizing: 'border-box',
        borderTop: first ? 'none' : '1px solid rgba(255,255,255,0.06)' }}>
      <Stripe color={STATE_COLOR[st]} />
      <Poster path={e.poster_path} w={32} h={48} tmdbLogo />
      <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
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

export const Stripe = ({ color }: { color: string }) => (
  <span aria-hidden="true" data-testid="tv-stripe" style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 3, background: color }} />
);
const Check = () => (
  <svg data-testid="tv-check" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" style={{ flex: 'none' }}>
    <path d="M2.5 6.2l2.3 2.3 4.7-5" fill="none" stroke="#22c55e" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

/** Quiet loading: glass .04 rows where the agenda goes (no spinner, no motion). */
function Placeholder() {
  return (
    <div data-testid="tv-cal-loading" aria-busy="true" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ ...glassCard, background: 'rgba(255,255,255,0.04)', marginTop: 37 }}>
        {[0, 1, 2].map(i => <span key={i} style={{ display: 'block', height: 72, borderTop: i ? '1px solid rgba(255,255,255,0.04)' : 'none' }} />)}
      </div>
    </div>
  );
}

export const tvCalendarCss = `
.tvc-row:hover{background:rgba(255,255,255,.04)!important}
.tvc-glass:hover{background:rgba(255,255,255,.10)!important}
`;

const one: React.CSSProperties = { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' };
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
