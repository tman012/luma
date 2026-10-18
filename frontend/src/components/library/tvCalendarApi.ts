/** Settings > TV Calendar and Search (docs/design/tv.md, v1): the API,
 * and the local-date math the Calendar runs on. TMDB gives air dates, not air
 * times, so dates are calendar days ("YYYY-MM-DD") and never shift by zone;
 * "today" is the viewer's local day. Weeks start on Sunday. */
import { apiFetch } from '@/apiClient';
import { SAVE_FAILED, UNREACHABLE } from './libraryPathsApi';
import { TV_URL } from './tvLibraryApi';

export interface CalEpisode {
  episode_id: number; series_id: number; tmdb_id?: number | null; show: string; year: number | null; poster_path: string | null;
  season: number; episode: number; title: string; air_date: string; downloaded: boolean;
  /** "9:00 PM" when known (TMDB has none today). */
  air_time?: string | null;
  /** UTC air time (TVDB, else TVmaze) and minutes; null: the date only. */
  airstamp?: string | null; runtime?: number | null;
  /** Card still sources: TheTVDB episode image (with a key), TMDB still, show backdrop. */
  tvdb_image?: string | null; still_path?: string | null; backdrop_path?: string | null;
  /** v158a: the series and this episode are monitored (unmonitored cards render dimmed). */
  monitored?: boolean;
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
export interface CalendarData { start: string; days: number; episodes: CalEpisode[]; libraries: number; refreshed_at: number | null;
  /** a background refresh (episode images, air times) started: ask again shortly */
  refreshing?: boolean }
export interface FindResult {
  tmdb_id: number; title: string; year: number | null; poster_path: string | null; seasons: number | null;
  in_library: boolean; series_id: number | null;
}
/** downloaded (a file or Plex has it), missing (aired, no file), unaired (not aired yet; airing now folds in). */
export type EpisodeState = 'downloaded' | 'missing' | 'unaired';

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
/** Monitor options on Add (Designer: All episodes is the default). */
export type MonitorOption = 'all' | 'future' | 'missing' | 'existing' | 'recent' | 'pilot' | 'first_season' | 'last_season' | 'none';
export const ADD_MONITOR: { id: MonitorOption; label: string }[] = [
  { id: 'all', label: 'All episodes' }, { id: 'future', label: 'Future episodes' }, { id: 'missing', label: 'Missing episodes' },
  { id: 'first_season', label: 'First season' }, { id: 'last_season', label: 'Latest season' }, { id: 'none', label: 'None' },
];
export const addShow = (tmdb_id: number, path_id?: string, opts: { monitor?: MonitorOption; profile_id?: number | null } = {}) =>
  call<{ ok: true; series_id: number }>(`${TV_URL}/shows`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tmdb_id, ...(path_id ? { path_id } : {}), ...(opts.monitor ? { monitor: opts.monitor } : {}),
      ...(opts.profile_id != null ? { profile_id: opts.profile_id } : {}) }),
  }, d => typeof d.series_id === 'number');
/** v158b: Search for an episode or a season (grabs what it picks when grabs are allowed). */
export interface SearchPick { title: string; quality: string | null; episodes: number[]; grabbed: boolean; error: string | null }
export interface SearchResult { ok: boolean; wanted: number; searched: number; results: number; picks: SearchPick[];
  grabs_allowed: boolean; error: string | null }
export const searchShow = (seriesId: number, target: { season: number } | { episode_id: number }) =>
  call<SearchResult>(`${TV_URL}/shows/${seriesId}/search`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(target),
  }, d => Array.isArray(d.picks));
/** v158c: whether grabs can go out on this instance (the search chips show only then). One ask per page load. */
let grabsAsk: Promise<boolean> | null = null;
export const grabsAllowed = (): Promise<boolean> => {
  if (!grabsAsk) {
    grabsAsk = call<{ grabs_allowed: boolean }>(`${TV_URL}/search/status`, undefined, d => typeof d.grabs_allowed === 'boolean')
      .then(r => (r.ok ? r.data.grabs_allowed : false));
  }
  return grabsAsk;
};
export const resetGrabsAllowed = () => { grabsAsk = null; };
/** One short line for a search's outcome (status, not helper text). */
export function searchOutcome(r: { ok: true; data: SearchResult } | { ok: false; status: number; error: string }): string {
  if (!r.ok) return r.error;
  const d = r.data;
  if (d.error) return d.error;
  if (!d.wanted) return 'Nothing missing';
  const grabbed = d.picks.filter(p => p.grabbed).length;
  if (grabbed) return grabbed === 1 ? 'Grabbed 1 release' : `Grabbed ${grabbed} releases`;
  if (d.picks.length) return d.picks[0]?.error || 'Not grabbed';
  return 'No releases found';
}
const put = (body: unknown): RequestInit => ({ method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
/** The calendar bookmark: that episode only. */
export const setEpisodeMonitored = (episodeId: number, monitored: boolean) =>
  call<SeriesView>(`${TV_URL}/episodes/${episodeId}/monitored`, put({ monitored }), d => typeof d.id === 'number');
export const setSeasonMonitored = (seriesId: number, season: number, monitored: boolean) =>
  call<SeriesView>(`${TV_URL}/shows/${seriesId}/seasons/${season}/monitored`, put({ monitored }), d => typeof d.id === 'number');
export const setSeriesMonitored = (seriesId: number, monitored: boolean) =>
  call<SeriesView>(`${TV_URL}/shows/${seriesId}/monitored`, put({ monitored }), d => typeof d.id === 'number');

// ---------------------------------------------------------------- series edit + profiles (v158a)

export type SeriesType = 'standard' | 'daily' | 'anime';
export interface SeriesView {
  id: number; title: string; year: number | null; tmdb_id: number; monitored: boolean; monitor: MonitorOption;
  monitor_new_seasons: 'all' | 'none'; monitor_specials: boolean; season_folder: boolean; series_type: SeriesType;
  profile_id: number | null; path_id: string | null; folder: string | null; path: string | null;
  seasons: { season: number; monitored: boolean }[];
  episodes: { id: number; season: number; episode: number; monitored: boolean }[];
}
export type SeriesEdit = Partial<Pick<SeriesView, 'monitored' | 'monitor_new_seasons' | 'monitor_specials' | 'season_folder' | 'series_type' | 'profile_id' | 'path'>>;
export const getSeries = (id: number) => call<SeriesView>(`${TV_URL}/shows/${id}`, undefined, d => typeof d.id === 'number');
export const getSeriesByTmdb = (tmdbId: number) =>
  call<SeriesView>(`${TV_URL}/shows/by-tmdb/${tmdbId}`, undefined, d => typeof d.id === 'number');
export const saveSeries = (id: number, body: SeriesEdit) => call<SeriesView>(`${TV_URL}/shows/${id}`, put(body), d => typeof d.id === 'number');
export const deleteSeries = (id: number, deleteFiles: boolean) =>
  call<{ ok: true }>(`${TV_URL}/shows/${id}${deleteFiles ? '?delete_files=1' : ''}`, { method: 'DELETE' }, d => d.ok === true);
export const monitorMissing = (ids: number[]) =>
  call<{ ok: true; updated: number }>(`${TV_URL}/shows/monitor-missing`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }),
  }, d => d.ok === true);

export interface Quality { id: string; name: string; resolution: string; source: string }
export interface QualityItem { id: string; allowed: boolean }
export interface Profile { id: number; name: string; items: QualityItem[]; cutoff: string | null; upgrades: boolean; default: boolean; in_use?: boolean }
export type ProfileDraft = { name: string; items: QualityItem[]; cutoff: string | null; upgrades: boolean; default: boolean };
export const loadProfiles = () =>
  call<{ profiles: Profile[]; qualities: Quality[] }>(`${TV_URL}/profiles`, undefined, d => Array.isArray(d.profiles) && Array.isArray(d.qualities));
export const saveProfile = (draft: ProfileDraft, id?: number) =>
  call<Profile>(id == null ? `${TV_URL}/profiles` : `${TV_URL}/profiles/${id}`,
    { method: id == null ? 'POST' : 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) },
    d => typeof d.id === 'number');
export const deleteProfile = (id: number) =>
  call<{ ok: true }>(`${TV_URL}/profiles/${id}`, { method: 'DELETE' }, d => d.ok === true);
/** "Up to 1080p" from a cutoff id like '1080p-web' (SD for 'sd'). */
export const cutoffLabel = (cutoff: string | null): string => {
  if (!cutoff) return '';
  const res = cutoff.split('-')[0];
  return `Up to ${res === 'sd' ? 'SD' : res}`;
};

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
  return e.air_date && e.air_date < today ? 'missing' : 'unaired';
}
export const STATE_WORD: Record<EpisodeState, string> = { downloaded: 'Downloaded', missing: 'Missing', unaired: 'Unaired' };
/** The card edge colors, as CSS vars (Designer tunes them in one place; tvCalendarCss sets the defaults). */
export const STATE_VAR: Record<EpisodeState, string> = {
  downloaded: '--tv-state-downloaded', missing: '--tv-state-missing', unaired: '--tv-state-unaired',
};
export const STATE_HEX: Record<EpisodeState, string> = { downloaded: '#22c55e', missing: '#ef4444', unaired: '#3b82f6' };
export const STATE_COLOR: Record<EpisodeState, string> = {
  downloaded: `var(${STATE_VAR.downloaded}, ${STATE_HEX.downloaded})`,
  missing: `var(${STATE_VAR.missing}, ${STATE_HEX.missing})`,
  unaired: `var(${STATE_VAR.unaired}, ${STATE_HEX.unaired})`,
};

/** "S02E05". */
export const code = (s: number, e: number) => `S${pad2(s)}E${pad2(e)}`;

type Sortable = { air_date: string; airstamp?: string | null; show: string; season: number; episode: number };
/** The instant an episode airs (ms), from its UTC airstamp; null when untimed. Compared as instants, so an
 * 11:30 PM ET show (airstamp 03:30Z the next UTC day) sorts after an 8 PM one, as it does on the viewer's clock. */
export const airMs = (e: { airstamp?: string | null }): number | null => {
  if (!e.airstamp) return null;
  const t = new Date(e.airstamp).getTime();
  return Number.isNaN(t) ? null : t;
};
const nameKey = (s: string) => s.toLowerCase().replace(/^(the|a|an)\s+/, '').replace(/[^a-z0-9 ]+/g, '');
/** Within a day: by air time, then name, season, episode; untimed episodes last. */
export function byAirTime(a: Sortable, b: Sortable): number {
  const ta = airMs(a); const tb = airMs(b);
  if (ta != null && tb != null && ta !== tb) return ta - tb;
  if ((ta == null) !== (tb == null)) return ta == null ? 1 : -1;
  const na = nameKey(a.show); const nb = nameKey(b.show);
  if (na !== nb) return na < nb ? -1 : 1;
  return (a.season - b.season) || (a.episode - b.episode);
}
/** One day's episodes in air-time order. */
export const sortDay = <E extends Sortable>(list: E[]): E[] => [...list].sort(byAirTime);

/** Episodes by day (only days that have one), in day order, each day in air-time order. */
export function byDay<E extends Sortable>(eps: E[]): [Day, E[]][] {
  const m = new Map<Day, E[]>();
  for (const e of [...eps].sort((a, b) => (a.air_date < b.air_date ? -1 : a.air_date > b.air_date ? 1 : byAirTime(a, b)))) {
    const l = m.get(e.air_date); if (l) l.push(e); else m.set(e.air_date, [e]);
  }
  return [...m.entries()];
}
/** The 7 days of the week that starts on start. */
export const weekDays = (start: Day): Day[] => Array.from({ length: 7 }, (_, i) => addDays(start, i));

/** "5 seasons", "1 season", '' when unknown. */
export const seasonsText = (n: number | null) => (n == null ? '' : `${n} ${n === 1 ? 'season' : 'seasons'}`);
