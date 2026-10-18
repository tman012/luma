/** Settings > TV Calendar, Search and Show detail (docs/design/tv.md): the API,
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
}
export interface CalendarData { start: string; days: number; episodes: CalEpisode[]; libraries: number; refreshed_at: number | null }
export interface FindResult {
  tmdb_id: number; title: string; year: number | null; poster_path: string | null; seasons: number | null;
  in_library: boolean; series_id: number | null;
}
export interface DetailEpisode { id: number; season: number; episode: number; title: string; air_date: string | null; downloaded: boolean; files: string[] }
export interface DetailSeason { number: number; name: string | null; episodes: DetailEpisode[] }
export interface ShowDetail {
  id: number; tmdb_id: number; title: string; year: number | null; network: string | null; status: string | null;
  poster_path: string | null; path_id: string; path_name: string; folder: string | null; have: number; aired: number;
  seasons: DetailSeason[];
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
export const loadShow = (id: number) => call<ShowDetail>(`${TV_URL}/shows/${id}`, undefined, d => Array.isArray(d.seasons));
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
/** "Sun 12" (the week grid's column header). */
export const columnLabel = (d: Day) => { const p = parts(d); return `${WD[p.w]} ${p.d}`; };
/** "Today", "Tomorrow", else "Mon, Oct 20" (agenda day headers). */
export function agendaLabel(d: Day, today: Day): string {
  if (d === today) return 'Today';
  if (d === addDays(today, 1)) return 'Tomorrow';
  const p = parts(d);
  return `${WD[p.w]}, ${MON[p.m]} ${p.d}`;
}
/** "Oct 20, 2026". */
export const longDate = (d: Day) => { const p = parts(d); return `${MON[p.m]} ${p.d}, ${p.y}`; };

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
/** "62 of 62 episodes". */
export const haveText = (have: number, aired: number) => `${have} of ${aired} ${aired === 1 ? 'episode' : 'episodes'}`;
/** "2008 · AMC · Ended". */
export const metaText = (s: Pick<ShowDetail, 'year' | 'network' | 'status'>) =>
  [s.year ? String(s.year) : '', s.network || '', s.status === 'Returning Series' ? 'Returning' : (s.status || '')]
    .filter(Boolean).join(' \u00b7 ');
/** Specials (season 0) last; regular seasons in order. */
export const seasonName = (n: number) => (n === 0 ? 'Specials' : `Season ${n}`);
/** The season Show detail opens on: the clicked episode's, else the latest regular season. */
export function openingSeason(seasons: DetailSeason[], want: number | null): number | null {
  if (want != null && seasons.some(s => s.number === want)) return want;
  const regular = seasons.filter(s => s.number > 0).map(s => s.number);
  return regular.length ? Math.max(...regular) : (seasons[0]?.number ?? null);
}
