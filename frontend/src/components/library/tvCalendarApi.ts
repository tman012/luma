/** Settings > TV Calendar and Search (docs/design/tv.md, v1): the API,
 * and the local-date math the Calendar runs on. TMDB gives air dates, not air
 * times, so dates are calendar days ("YYYY-MM-DD") and never shift by zone;
 * "today" is the viewer's local day. Weeks start on Sunday. */
import { apiFetch } from '@/apiClient';
import { SAVE_FAILED, UNREACHABLE } from './libraryPathsApi';
import { TV_URL } from './tvLibraryApi';

export interface CalEpisode {
  episode_id: number; series_id: number; show: string; year: number | null; poster_path: string | null;
  season: number; episode: number; title: string; air_date: string; downloaded: boolean;
  /** "9:00 PM" when known (TMDB has none today). */
  air_time?: string | null;
  /** UTC air time (TVDB, else TVmaze) and minutes; null: the date only. */
  airstamp?: string | null; runtime?: number | null;
  /** Card still sources: TheTVDB episode image (with a key), TMDB still, show backdrop. */
  tvdb_image?: string | null; still_path?: string | null; backdrop_path?: string | null;
}

/** '9:00 – 9:42 PM' (or '11:30 PM – 12:10 AM') in the viewer's zone (*timeZone* for tests); null without an airstamp. */
export function timeRange(airstamp: string | null | undefined, runtime: number | null | undefined, timeZone?: string): string | null {
  if (!airstamp) return null;
  const start = new Date(airstamp);
  if (Number.isNaN(start.getTime())) return null;
  const fmt = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, timeZone });
  const parts = (d: Date) => {
    const p = fmt.formatToParts(d);
    const get = (t: string) => p.find(x => x.type === t)?.value ?? '';
    return { hm: `${get('hour')}:${get('minute')}`, ap: get('dayPeriod').toUpperCase() };
  };
  const a = parts(start);
  if (!runtime || runtime <= 0) return `${a.hm} ${a.ap}`;
  const b = parts(new Date(start.getTime() + runtime * 60000));
  return a.ap === b.ap ? `${a.hm} \u2013 ${b.hm} ${b.ap}` : `${a.hm} ${a.ap} \u2013 ${b.hm} ${b.ap}`;
}
export interface CalendarData { start: string; days: number; episodes: CalEpisode[]; libraries: number; refreshed_at: number | null }
export interface FindResult {
  tmdb_id: number; title: string; year: number | null; poster_path: string | null; seasons: number | null;
  in_library: boolean; series_id: number | null;
}
export type EpisodeState = 'downloaded' | 'missing' | 'upcoming';

type R<T> = { ok: true; data: T } | { ok: false; status: number; error: string };
async function call<T>(url: string, init: RequestInit | undefined, valid: (d: Record<string, unknown>) => boolean): Promise<R<T>> {
  try {
    const res = await apiFetch(url, init);
    const data = await res.json().catch(() => ({})) as Record<string, unknown> & { error?: string };
    if (res.ok && valid(data)) return { ok: true, data: data as unknown as T };
    return { ok: false, status: res.status, error: data.error || SAVE_FAILED };
  } catch {
    return { ok: false, status: 0, error: UNREACHABLE };
  }
}

export const loadCalendar = (start: string, days: number) =>
  call<CalendarData>(`${TV_URL}/calendar?start=${start}&days=${days}`, undefined, d => Array.isArray(d.episodes));
export const findShows = (q: string) =>
  call<{ results: FindResult[] }>(`${TV_URL}/find?q=${encodeURIComponent(q)}`, undefined, d => Array.isArray(d.results));
export const addShow = (tmdb_id: number, path_id?: string) =>
  call<{ ok: true; series_id: number }>(`${TV_URL}/shows`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(path_id ? { tmdb_id, path_id } : { tmdb_id }),
  }, d => typeof d.series_id === 'number');

// ---------------------------------------------------------------- dates

/** A calendar day as "YYYY-MM-DD". */
export type Day = string;
const pad2 = (n: number) => String(n).padStart(2, '0');
/** The viewer's local day. */
export const localDay = (now: Date = new Date()): Day => `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`;
/** Day math on the calendar itself (UTC noon), so a DST change never moves a day. */
const asDate = (d: Day) => { const [y = 0, m = 1, dd = 1] = d.split('-').map(Number); return new Date(Date.UTC(y, m - 1, dd, 12)); };
const fromDate = (t: Date): Day => `${t.getUTCFullYear()}-${pad2(t.getUTCMonth() + 1)}-${pad2(t.getUTCDate())}`;
export const addDays = (d: Day, n: number): Day => { const t = asDate(d); t.setUTCDate(t.getUTCDate() + n); return fromDate(t); };
export const weekday = (d: Day) => asDate(d).getUTCDay();
/** The Sunday that starts d's week. */
export const weekStart = (d: Day): Day => addDays(d, -weekday(d));
export const isDay = (s: string | null | undefined): s is Day => !!s && /^\d{4}-\d{2}-\d{2}$/.test(s) && fromDate(asDate(s)) === s;
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const parts = (d: Day) => { const t = asDate(d); return { y: t.getUTCFullYear(), m: t.getUTCMonth(), d: t.getUTCDate(), w: t.getUTCDay() }; };

/** "Oct 12 – 18", or "Dec 28 – Jan 3" across months. */
export function weekLabel(start: Day): string {
  const a = parts(start); const b = parts(addDays(start, 6));
  return a.m === b.m ? `${MON[a.m]} ${a.d} \u2013 ${b.d}` : `${MON[a.m]} ${a.d} \u2013 ${MON[b.m]} ${b.d}`;
}
/** "Today", "Tomorrow", else "Mon, Oct 20" (agenda day headers). */
export function agendaLabel(d: Day, today: Day): string {
  if (d === today) return 'Today';
  if (d === addDays(today, 1)) return 'Tomorrow';
  const p = parts(d);
  return `${WD[p.w]}, ${MON[p.m]} ${p.d}`;
}
/** "Oct 20" (a row's air date). */
export const shortDate = (d: Day) => { const p = parts(d); return `${MON[p.m]} ${p.d}`; };

/** Downloaded: a file is linked. Missing: aired before today, no file. Upcoming: today or later. */
export function episodeState(e: { downloaded: boolean; air_date: string | null }, today: Day): EpisodeState {
  if (e.downloaded) return 'downloaded';
  return e.air_date && e.air_date < today ? 'missing' : 'upcoming';
}
export const STATE_WORD: Record<EpisodeState, string> = { downloaded: 'Downloaded', missing: 'Missing', upcoming: 'Upcoming' };
export const STATE_COLOR: Record<EpisodeState, string> = { downloaded: '#22c55e', missing: '#ef4444', upcoming: '#3b82f6' };

/** "S02E05". */
export const code = (s: number, e: number) => `S${pad2(s)}E${pad2(e)}`;

/** Episodes by day (only days that have one), in day order. */
export function byDay<E extends { air_date: string }>(eps: E[]): [Day, E[]][] {
  const m = new Map<Day, E[]>();
  for (const e of [...eps].sort((a, b) => (a.air_date < b.air_date ? -1 : a.air_date > b.air_date ? 1 : 0))) {
    const l = m.get(e.air_date); if (l) l.push(e); else m.set(e.air_date, [e]);
  }
  return [...m.entries()];
}
/** The 7 days of the week that starts on start. */
export const weekDays = (start: Day): Day[] => Array.from({ length: 7 }, (_, i) => addDays(start, i));

/** "5 seasons", "1 season", '' when unknown. */
export const seasonsText = (n: number | null) => (n == null ? '' : `${n} ${n === 1 ? 'season' : 'seasons'}`);
