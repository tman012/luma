import { apiFetch } from '../apiClient';
/**
 * settingsApi.ts — API functions for the Settings page.
 *
 * All settings endpoints return JSON. The PUT endpoint accepts
 * a flat object of dot-path keys: { "plex.url": "...", ... }.
 * Passwords masked as "********" are skipped on save.
 */

const MASK = '********';

interface SettingsResponse {
  success: boolean;
  settings?: Record<string, unknown>;
  errors?: string[];
}

interface TestResult {
  success: boolean;
  message: string;
  details?: Record<string, unknown>;
}

export interface RoomData {
  id: string;
  name: string;
  useHarmonyHub: boolean;
  harmonyHubIp: string;
  harmonyVolumeDevice: string;
  devices: DeviceData[];
}

export interface DeviceData {
  id: string;
  name: string;
  platform: 'android_tv' | 'chromecast_gtv' | 'network';
  host: string;
  connectPort: number;
  pairPort?: number | null;
  paired: boolean;
  harmonyActivity?: string;
  harmonyNavigationDevice?: string;
  macAddress?: string;
  powerMethod?: 'adb' | 'homeassistant';
  haPowerEntity?: string;
  services: ServiceData[];
}

export interface ServiceData {
  id: string;
  type: 'kodi' | 'plex';
  kodi_host?: string;
  kodi_port?: number;
  kodi_username?: string;
  kodi_password?: string;
}

interface RoomsResponse {
  rooms: RoomData[];
  defaultPlaybackTarget: { roomId: string; deviceId: string; serviceId: string };
}

export interface QualityProfile {
  id: number;
  name: string;
}

export interface PlexLibrary {
  id: number;
  title: string;
  type: string;
}

export interface PlexHealthResponse {
  success: boolean;
  server_name?: string;
  libraries?: PlexLibrary[];
  default_movie_section_id?: number;
}

// ── Settings CRUD ────────────────────────────────────────────────

export async function fetchSettings(): Promise<Record<string, unknown>> {
  const resp = await apiFetch('/api/settings');
  if (!resp.ok) throw new Error(`Failed to load settings (${resp.status})`);
  return resp.json();
}

export async function fetchSchema(): Promise<Record<string, unknown>> {
  const resp = await apiFetch('/api/settings/schema');
  if (!resp.ok) throw new Error(`Failed to load schema (${resp.status})`);
  return resp.json();
}

export async function saveSettings(
  changes: Record<string, unknown>,
): Promise<SettingsResponse> {
  const resp = await apiFetch('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(changes),
  });
  return resp.json();
}

// ── Test connections ─────────────────────────────────────────────

export async function testConnection(
  service: string,
  body?: Record<string, unknown>,
): Promise<TestResult> {
  const resp = await apiFetch(`/api/settings/test/${service}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  return resp.json();
}

// ── Rooms CRUD ───────────────────────────────────────────────────

export async function fetchRooms(): Promise<RoomsResponse> {
  const resp = await apiFetch('/api/settings/rooms');
  if (!resp.ok) throw new Error(`Failed to load rooms (${resp.status})`);
  return resp.json();
}

export async function createRoom(data: Partial<RoomData>): Promise<{ room: RoomData }> {
  const resp = await apiFetch('/api/settings/rooms', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(err.error || 'Failed to create room');
  }
  return resp.json();
}

export async function updateRoom(
  roomId: string,
  data: Partial<RoomData>,
): Promise<{ room: RoomData }> {
  const resp = await apiFetch(`/api/settings/rooms/${roomId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(err.error || 'Failed to update room');
  }
  return resp.json();
}

export async function deleteRoom(roomId: string): Promise<void> {
  const resp = await apiFetch(`/api/settings/rooms/${roomId}`, { method: 'DELETE' });
  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(err.error || 'Failed to delete room');
  }
}

// ── Devices CRUD ─────────────────────────────────────────────────

export async function createDevice(
  roomId: string,
  data: Partial<DeviceData>,
): Promise<{ device: DeviceData }> {
  const resp = await apiFetch(`/api/settings/rooms/${roomId}/devices`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(err.error || 'Failed to create device');
  }
  return resp.json();
}

export async function updateDevice(
  roomId: string,
  deviceId: string,
  data: Partial<DeviceData>,
): Promise<{ device: DeviceData }> {
  const resp = await apiFetch(`/api/settings/rooms/${roomId}/devices/${deviceId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(err.error || 'Failed to update device');
  }
  return resp.json();
}

export async function deleteDevice(
  roomId: string,
  deviceId: string,
): Promise<void> {
  const resp = await apiFetch(`/api/settings/rooms/${roomId}/devices/${deviceId}`, {
    method: 'DELETE',
  });
  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(err.error || 'Failed to delete device');
  }
}

// ── Services CRUD ────────────────────────────────────────────────

export async function createService(
  roomId: string,
  deviceId: string,
  data: Partial<ServiceData>,
): Promise<{ service: ServiceData }> {
  const resp = await apiFetch(
    `/api/settings/rooms/${roomId}/devices/${deviceId}/services`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    },
  );
  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(err.error || 'Failed to create service');
  }
  return resp.json();
}

export async function updateService(
  roomId: string,
  deviceId: string,
  serviceId: string,
  data: Partial<ServiceData>,
): Promise<{ service: ServiceData }> {
  const resp = await apiFetch(
    `/api/settings/rooms/${roomId}/devices/${deviceId}/services/${serviceId}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    },
  );
  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(err.error || 'Failed to update service');
  }
  return resp.json();
}

export async function deleteService(
  roomId: string,
  deviceId: string,
  serviceId: string,
): Promise<void> {
  const resp = await apiFetch(
    `/api/settings/rooms/${roomId}/devices/${deviceId}/services/${serviceId}`,
    { method: 'DELETE' },
  );
  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(err.error || 'Failed to delete service');
  }
}

export async function setDefaultPlaybackTarget(target: {
  roomId: string;
  deviceId: string;
  serviceId: string;
}): Promise<void> {
  const resp = await apiFetch('/api/settings/rooms/default', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(target),
  });
  if (!resp.ok) throw new Error('Failed to set default target');
}

// ── Device test / pair ───────────────────────────────────────────

export async function testDevice(
  roomId: string,
  deviceId: string,
): Promise<TestResult> {
  const resp = await apiFetch(`/api/rooms/${roomId}/devices/${deviceId}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  return resp.json();
}

export async function pairDevice(
  roomId: string,
  deviceId: string,
  pairPort: number,
  pairCode: string,
): Promise<TestResult & { ok?: boolean; needsPairing?: boolean; hint?: string }> {
  const resp = await apiFetch(`/api/rooms/${roomId}/devices/${deviceId}/pair`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // Backend expects camelCase; also send snake_case for compatibility
    body: JSON.stringify({
      pairPort,
      pairCode,
      pair_port: pairPort,
      pair_code: pairCode,
    }),
  });
  return resp.json();
}

// ── Radarr quality profiles ─────────────────────────────────────

export async function fetchRadarrProfiles(): Promise<QualityProfile[]> {
  const resp = await apiFetch('/api/radarr/quality-profiles');
  if (!resp.ok) return [];
  const data = await resp.json();
  return (data.profiles ?? data) as QualityProfile[];
}

// ── Sonarr quality profiles ─────────────────────────────────────

export async function fetchSonarrProfiles(): Promise<QualityProfile[]> {
  const resp = await apiFetch('/api/sonarr/quality-profiles');
  if (!resp.ok) return [];
  const data = await resp.json();
  return (data.profiles ?? data) as QualityProfile[];
}

// ── Plex health (for library dropdown) ───────────────────────────

export async function fetchPlexHealth(): Promise<PlexHealthResponse> {
  const resp = await apiFetch('/api/plex/health');
  if (!resp.ok) return { success: false };
  return resp.json();
}

// ── Media types / paths ──────────────────────────────────────────

export async function syncFromPlex(): Promise<{
  success: boolean;
  paths?: Record<string, unknown>[];
  message?: string;
}> {
  const resp = await apiFetch('/api/settings/media/sync-from-plex', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  return resp.json();
}

// ── Trending Watchlist Automation ────────────────────────────────

interface TrendingWatchlistMovie {
  title: string;
  year: number;
  tmdb_id: number;
}

interface TrendingWatchlistResult {
  checked: number;
  added: number;
  added_movies?: TrendingWatchlistMovie[];
  skipped_plex: number;
  skipped_plex_titles?: string[];
  skipped_radarr: number;
  skipped_radarr_titles?: string[];
  errors: string[];
  error?: string;
}

export interface TrendingWatchlistStatus {
  running: boolean;
  last_run: string | null;
  cycle_count: number;
  last_result: TrendingWatchlistResult | null;
}

export interface TrendingWatchlistLogEntry {
  title: string;
  year: number;
  tmdb_id: number;
  added_at: string;
}

export async function fetchTrendingWatchlistStatus(): Promise<TrendingWatchlistStatus> {
  const resp = await apiFetch('/api/automation/trending-watchlist/status');
  if (!resp.ok) throw new Error(`Failed to fetch status (${resp.status})`);
  return resp.json();
}

export async function fetchTrendingWatchlistLog(): Promise<TrendingWatchlistLogEntry[]> {
  const resp = await apiFetch('/api/automation/trending-watchlist/log');
  if (!resp.ok) return [];
  return resp.json();
}

export async function triggerTrendingWatchlistRun(): Promise<{ success: boolean; message: string }> {
  const resp = await apiFetch('/api/automation/trending-watchlist/run', { method: 'POST' });
  return resp.json();
}

// ── Trending TV Watchlist Automation ─────────────────────────────

interface TrendingTvWatchlistResult {
  checked: number;
  filtered_first_season: number;
  added: number;
  added_shows?: { title: string; year: number; tmdb_id: number; tvdb_id: number; added_at: string; type: string }[];
  skipped_seasons: number;
  skipped_seasons_detail?: string[];
  skipped_plex: number;
  skipped_plex_titles?: string[];
  skipped_sonarr: number;
  skipped_sonarr_titles?: string[];
  errors: string[];
  error?: string;
}

export interface TrendingTvWatchlistStatus {
  running: boolean;
  last_run: string | null;
  cycle_count: number;
  last_result: TrendingTvWatchlistResult | null;
}

export interface TrendingTvWatchlistLogEntry {
  title: string;
  year: number;
  tmdb_id: number;
  tvdb_id: number;
  added_at: string;
  type: string;
}

export async function fetchTrendingTvWatchlistStatus(): Promise<TrendingTvWatchlistStatus> {
  const resp = await apiFetch('/api/automation/trending-tv-watchlist/status');
  if (!resp.ok) throw new Error(`Failed to fetch TV status (${resp.status})`);
  return resp.json();
}

export async function fetchTrendingTvWatchlistLog(): Promise<TrendingTvWatchlistLogEntry[]> {
  const resp = await apiFetch('/api/automation/trending-tv-watchlist/log');
  if (!resp.ok) return [];
  return resp.json();
}

export async function triggerTrendingTvWatchlistRun(): Promise<{ success: boolean; message: string }> {
  const resp = await apiFetch('/api/automation/trending-tv-watchlist/run', { method: 'POST' });
  return resp.json();
}

// ── Now Available Watchlist Automation ────────────────────────────

interface NowAvailableWatchlistResult {
  checked: number;
  added: number;
  added_movies?: { title: string; year: number; tmdb_id: number; added_at: string; type: string }[];
  skipped_plex: number;
  skipped_plex_titles?: string[];
  skipped_radarr: number;
  skipped_radarr_titles?: string[];
  errors: string[];
  error?: string;
}

export interface NowAvailableWatchlistStatus {
  running: boolean;
  last_run: string | null;
  cycle_count: number;
  last_result: NowAvailableWatchlistResult | null;
}

export interface NowAvailableWatchlistLogEntry {
  title: string;
  year: number;
  tmdb_id: number;
  added_at: string;
  type: string;
}

export async function fetchNowAvailableWatchlistStatus(): Promise<NowAvailableWatchlistStatus> {
  const resp = await apiFetch('/api/automation/now-available-watchlist/status');
  if (!resp.ok) throw new Error(`Failed to fetch now-available status (${resp.status})`);
  return resp.json();
}

export async function fetchNowAvailableWatchlistLog(): Promise<NowAvailableWatchlistLogEntry[]> {
  const resp = await apiFetch('/api/automation/now-available-watchlist/log');
  if (!resp.ok) return [];
  return resp.json();
}

export async function triggerNowAvailableWatchlistRun(): Promise<{ success: boolean; message: string }> {
  const resp = await apiFetch('/api/automation/now-available-watchlist/run', { method: 'POST' });
  return resp.json();
}

// ── Anticipated Watchlist Automation ──────────────────────────────

export interface AnticipatedWatchlistStatus {
  running: boolean;
  last_run: string | null;
  cycle_count: number;
  last_result: {
    added: number;
    added_movies?: number;
    added_shows?: number;
    skipped_owned?: number;
    skipped_existing?: number;
    skipped_language?: number;
    errors?: string[];
    error?: string;
  } | null;
}

export interface AnticipatedWatchlistLogEntry {
  title: string;
  year: number;
  tmdb_id: number;
  tvdb_id?: number;
  added_at: string;
  type: string;
  source?: string;
}

export async function fetchAnticipatedWatchlistStatus(): Promise<AnticipatedWatchlistStatus> {
  const resp = await apiFetch('/api/automation/anticipated-watchlist/status');
  if (!resp.ok) throw new Error(`Failed to fetch anticipated status (${resp.status})`);
  return resp.json();
}

export async function fetchAnticipatedWatchlistLog(): Promise<AnticipatedWatchlistLogEntry[]> {
  const resp = await apiFetch('/api/automation/anticipated-watchlist/log');
  if (!resp.ok) return [];
  return resp.json();
}

export async function triggerAnticipatedWatchlistRun(): Promise<{ success: boolean; message: string }> {
  const resp = await apiFetch('/api/automation/anticipated-watchlist/run', { method: 'POST' });
  return resp.json();
}

// ── Latest Auto-Grab Automation ───────────────────────────────────

interface LatestAutoGrabResult {
  checked: number;
  candidates: number;
  grabbed: number;
  grabbed_movies?: {
    title: string;
    year: number;
    tmdb_id: number;
    torrent_name: string;
    indexer: string;
    seeders: number;
    size_gb: number;
    quality: string;
    guid: string;
    added_at: string;
  }[];
  skipped_in_plex: number;
  skipped_in_plex_titles?: string[];
  skipped_rejected: number;
  skipped_rejected_details?: { title: string; torrent_name: string; reason: string }[];
  errors: string[];
  min_seeders?: number;
  max_age_hours?: number;
  section_4k_id?: number;
  error?: string;
}

export interface LatestAutoGrabStatus {
  running: boolean;
  last_run: string | null;
  cycle_count: number;
  last_result: LatestAutoGrabResult | null;
}

export interface LatestAutoGrabLogEntry {
  title: string;
  year: number;
  tmdb_id: number;
  torrent_name: string;
  indexer: string;
  seeders: number;
  size_gb: number;
  quality: string;
  guid: string;
  added_at: string;
}

export async function fetchLatestAutoGrabStatus(): Promise<LatestAutoGrabStatus> {
  const resp = await apiFetch('/api/automation/latest-auto-grab/status');
  if (!resp.ok) throw new Error(`Failed to fetch Latest auto-grab status (${resp.status})`);
  return resp.json();
}

export async function fetchLatestAutoGrabLog(): Promise<LatestAutoGrabLogEntry[]> {
  const resp = await apiFetch('/api/automation/latest-auto-grab/log');
  if (!resp.ok) return [];
  return resp.json();
}

export async function triggerLatestAutoGrabRun(): Promise<{ success: boolean; message: string }> {
  const resp = await apiFetch('/api/automation/latest-auto-grab/run', { method: 'POST' });
  return resp.json();
}

// ── 4K Remux Auto-Grab Automation ─────────────────────────────────

interface Upgrade4kAutoGrabResult {
  checked: number;
  candidates: number;
  grabbed: number;
  grabbed_movies?: {
    title: string;
    year: number;
    tmdb_id: number;
    torrent_name: string;
    indexer: string;
    seeders: number;
    size_gb: number;
    quality: string;
    guid: string;
    added_at: string;
  }[];
  skipped_in_4k: number;
  skipped_in_4k_titles?: string[];
  skipped_rejected: number;
  skipped_rejected_details?: { title: string; torrent_name: string; reason: string }[];
  errors: string[];
  min_seeders?: number;
  max_age_hours?: number;
  section_4k_id?: number;
  error?: string;
}

export interface Upgrade4kAutoGrabStatus {
  running: boolean;
  last_run: string | null;
  cycle_count: number;
  last_result: Upgrade4kAutoGrabResult | null;
}

export interface Upgrade4kAutoGrabLogEntry {
  title: string;
  year: number;
  tmdb_id: number;
  torrent_name: string;
  indexer: string;
  seeders: number;
  size_gb: number;
  quality: string;
  guid: string;
  added_at: string;
}

export async function fetchUpgrade4kAutoGrabStatus(): Promise<Upgrade4kAutoGrabStatus> {
  const resp = await apiFetch('/api/automation/upgrade-4k-auto-grab/status');
  if (!resp.ok) throw new Error(`Failed to fetch 4K auto-grab status (${resp.status})`);
  return resp.json();
}

export async function fetchUpgrade4kAutoGrabLog(): Promise<Upgrade4kAutoGrabLogEntry[]> {
  const resp = await apiFetch('/api/automation/upgrade-4k-auto-grab/log');
  if (!resp.ok) return [];
  return resp.json();
}

export async function triggerUpgrade4kAutoGrabRun(): Promise<{ success: boolean; message: string }> {
  const resp = await apiFetch('/api/automation/upgrade-4k-auto-grab/run', { method: 'POST' });
  return resp.json();
}

// ── Movie Night (weekly canon acquisition) ───────────────────────

export interface MovieNightPick {
  title: string;
  year: number | null;
  tmdb_id: number;
  rank: number;
  quality: string;
  size_gb: number;
  seeders: number;
  tracker: string;
  torrent_name: string;
}

export interface MovieNightPreview {
  error?: string;
  dry_run: boolean;
  candidates: number;
  /** Non-English canon gaps the run skipped — they can't clear Radarr's
   *  English-only profile, so they are never attempted. */
  skipped_foreign?: number;
  /** Gaps excluded by the `min_year` floor. */
  skipped_old?: number;
  /** Gaps that failed the IMDb vote/rating fame gate. */
  skipped_imdb?: number;
  skipped_imdb_titles?: string[];
  imdb_min_votes?: number;
  imdb_min_rating?: number;
  min_year?: number;
  searched: number;
  trackers: string[];
  pick: MovieNightPick | null;
  grabbed: boolean;
  grab_detail?: string;
  reason?: string;
}

export interface MovieNightStatus {
  running: boolean;
  last_run: string | null;
  /** Naive ISO in the server's clock — render without timezone conversion. */
  next_run: string | null;
  /** Abbreviation of the server's zone (e.g. EDT), to label next_run. */
  server_tz: string | null;
  last_result: MovieNightPreview | null;
  cycle_count: number;
}

export interface MovieNightLogEntry {
  at: string;
  outcome: 'grabbed' | 'error' | 'failed' | 'nothing';
  summary: string;
  title: string | null;
  year: number | null;
  quality: string | null;
  size_gb: number | null;
  tracker: string | null;
}

export interface IndexerTracker {
  id: number;
  name: string;
  enabled: boolean;
}

/** Movie trackers the automation can be restricted to — already filtered to the
 *  ones worth searching for remuxes. `selectable` is false for Torznab, which
 *  has no per-tracker addressing. */
export async function fetchMovieNightTrackers(): Promise<{
  trackers: IndexerTracker[];
  selectable: boolean;
}> {
  const resp = await apiFetch('/api/movie-night/trackers');
  if (!resp.ok) return { trackers: [], selectable: false };
  return resp.json();
}

export interface CanonStats {
  built: boolean;
  total: number;
  owned: number;
  missing: number;
  /** Of the gaps, the English-language ones — the only films Movie Night can
   *  actually grab into an English-only Radarr profile. */
  missing_english: number;
  language_unknown: number;
  at_remux_or_better: number;
  at_4k_remux: number;
  below_remux: number;
}

export async function fetchCanonStats(): Promise<CanonStats> {
  const resp = await apiFetch('/api/canon?limit=1');
  if (!resp.ok) throw new Error(`Failed to fetch canon stats (${resp.status})`);
  return (await resp.json()).stats;
}

export async function buildCanonIndex(force = false): Promise<{ resolved: number; unresolved: number }> {
  const resp = await apiFetch('/api/canon/build', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ force }),
  });
  return resp.json();
}

export async function fetchMovieNightPreview(): Promise<MovieNightPreview> {
  const resp = await apiFetch('/api/movie-night/preview');
  if (!resp.ok) throw new Error(`Preview failed (${resp.status})`);
  return resp.json();
}

export async function fetchMovieNightStatus(): Promise<{
  status: MovieNightStatus;
  log: MovieNightLogEntry[];
}> {
  const resp = await apiFetch('/api/movie-night/status');
  if (!resp.ok) throw new Error(`Failed to fetch Movie Night status (${resp.status})`);
  return resp.json();
}

export async function triggerMovieNightRun(dryRun = true): Promise<{ ok: boolean; dry_run: boolean }> {
  const resp = await apiFetch('/api/movie-night/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dry_run: dryRun }),
  });
  return resp.json();
}

// ── Genre Roulette (nightly random-genre discovery) ──────────────

export interface GenreRoulettePick {
  title: string;
  year: number;
  tmdb_id: number;
  genre: string;
  rating: number;
  votes: number;
  quality: string;
  size_gb: number | null;
  seeders: number | null;
  tracker: string | null;
  torrent_name: string | null;
  grabbed: boolean;
  detail: string | null;
}

export interface GenreRouletteScan {
  title: string;
  year: number;
  genre: string;
  releases_found: number;
  eligible: number;
  rejects: Record<string, number>;
  picked: boolean;
}

export interface GenreRouletteResult {
  error?: string;
  dry_run: boolean;
  /** The genres this run drew. */
  genres: string[];
  genres_failed?: string[];
  year_from?: number;
  year_to?: number;
  /** Unowned films available to search after all filters. */
  candidates?: number;
  /** Films TMDb returned across tonight's genres, before the ownership cut. */
  considered?: number;
  searched?: number;
  scanned?: GenreRouletteScan[];
  wanted?: number;
  trackers?: string[];
  picks: GenreRoulettePick[];
  grabbed: number;
  reason?: string;
}

export interface GenreRouletteStatus {
  running: boolean;
  last_run: string | null;
  /** Naive ISO in the server's clock — render without timezone conversion. */
  next_run: string | null;
  /** Abbreviation of the server's zone (e.g. EDT), to label next_run. */
  server_tz: string | null;
  last_result: GenreRouletteResult | null;
  cycle_count: number;
}

export interface GenreRouletteLogEntry {
  at: string;
  outcome: 'grabbed' | 'error' | 'failed' | 'nothing';
  summary: string;
  genres: string[];
  candidates: number | null;
  searched: number | null;
  grabbed: number;
}

export async function fetchGenreRouletteGenres(): Promise<{
  genres: string[];
  selected: string[];
}> {
  const resp = await apiFetch('/api/genre-roulette/genres');
  if (!resp.ok) return { genres: [], selected: [] };
  return resp.json();
}

export async function fetchGenreRouletteTrackers(): Promise<{
  trackers: IndexerTracker[];
  selectable: boolean;
}> {
  const resp = await apiFetch('/api/genre-roulette/trackers');
  if (!resp.ok) return { trackers: [], selectable: false };
  return resp.json();
}

export async function fetchGenreRoulettePreview(): Promise<GenreRouletteResult> {
  const resp = await apiFetch('/api/genre-roulette/preview');
  if (!resp.ok) throw new Error(`Preview failed (${resp.status})`);
  return resp.json();
}

export async function fetchGenreRouletteStatus(): Promise<{
  status: GenreRouletteStatus;
  log: GenreRouletteLogEntry[];
}> {
  const resp = await apiFetch('/api/genre-roulette/status');
  if (!resp.ok) throw new Error(`Failed to fetch Genre Roulette status (${resp.status})`);
  return resp.json();
}

export async function triggerGenreRouletteRun(dryRun = true): Promise<{ ok: boolean; dry_run: boolean }> {
  const resp = await apiFetch('/api/genre-roulette/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dry_run: dryRun }),
  });
  return resp.json();
}


// ── Franchise Completer (nightly popular franchise gaps) ─────────

export interface FranchiseCompleterPick {
  title: string;
  year: number | null;
  tmdb_id: number;
  collection_name: string;
  collection_id?: number;
  fame_score?: number;
  collection_missing?: number;
  grabbed: boolean;
  detail: string | null;
  unmonitored?: boolean;
}

export interface FranchiseCompleterResult {
  error?: string;
  dry_run: boolean;
  candidates?: number;
  collections_considered?: number;
  collections_eligible?: number;
  wanted?: number;
  min_fame?: number;
  max_missing_parts?: number;
  min_year?: number;
  quality_profile_id?: number;
  picks: FranchiseCompleterPick[];
  grabbed: number;
  reason?: string;
}

export interface FranchiseCompleterStatus {
  running: boolean;
  last_run: string | null;
  next_run: string | null;
  server_tz: string | null;
  last_result: FranchiseCompleterResult | null;
  cycle_count: number;
}

export interface FranchiseCompleterLogEntry {
  at: string;
  outcome: 'grabbed' | 'error' | 'failed' | 'nothing';
  summary: string;
  candidates: number | null;
  grabbed: number;
}

export async function fetchFranchiseCompleterPreview(): Promise<FranchiseCompleterResult> {
  const resp = await apiFetch('/api/franchise-completer/preview');
  if (!resp.ok) throw new Error(`Preview failed (${resp.status})`);
  return resp.json();
}

export async function fetchFranchiseCompleterStatus(): Promise<{
  status: FranchiseCompleterStatus;
  log: FranchiseCompleterLogEntry[];
}> {
  const resp = await apiFetch('/api/franchise-completer/status');
  if (!resp.ok) throw new Error(`Failed to fetch Franchise Completer status (${resp.status})`);
  return resp.json();
}

export async function triggerFranchiseCompleterRun(dryRun = true): Promise<{ ok: boolean; dry_run: boolean }> {
  const resp = await apiFetch('/api/franchise-completer/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dry_run: dryRun }),
  });
  return resp.json();
}


// ── Watchlist Digital Dates (morning newly-dated check) ──────────

export interface WatchlistDigitalDatesChange {
  tmdb_id: number;
  title: string;
  year: number | null;
  kind: 'new' | 'changed';
  old_date: string | null;
  new_date: string;
  label: string | null;
}

export interface WatchlistDigitalDatesResult {
  error?: string;
  dry_run?: boolean;
  checked?: number;
  with_date?: number;
  without_date?: number;
  changes?: WatchlistDigitalDatesChange[];
  new_count?: number;
  changed_count?: number;
  notified?: number;
  reason?: string;
}

export interface WatchlistDigitalDatesStatus {
  running: boolean;
  last_run: string | null;
  next_run: string | null;
  server_tz: string | null;
  tz?: string | null;
  last_result: WatchlistDigitalDatesResult | null;
  cycle_count: number;
}

export interface WatchlistDigitalDatesLogEntry {
  at: string;
  outcome: 'found' | 'error' | 'nothing';
  summary: string;
  checked: number | null;
  with_date?: number | null;
  without_date?: number | null;
  new_count: number;
  changed_count: number;
  notified?: number;
  changes?: WatchlistDigitalDatesChange[];
  dry_run?: boolean;
}

export async function fetchWatchlistDigitalDatesStatus(): Promise<{
  status: WatchlistDigitalDatesStatus;
  log: WatchlistDigitalDatesLogEntry[];
}> {
  const resp = await apiFetch('/api/watchlist-digital-dates/status');
  if (!resp.ok) throw new Error(`Failed to fetch Watchlist Digital Dates status (${resp.status})`);
  return resp.json();
}

export async function triggerWatchlistDigitalDatesRun(dryRun = false): Promise<{ ok: boolean; dry_run: boolean }> {
  const resp = await apiFetch('/api/watchlist-digital-dates/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dry_run: dryRun }),
  });
  return resp.json();
}

// ── Helpers ──────────────────────────────────────────────────────

export function isMasked(value: unknown): boolean {
  return value === MASK;
}

// ── Downloads Guard (free-space floor on /downloads) ─────────────

export interface DownloadsGuardTracker {
  key: string;
  label: string;
  count: number;
  size_bytes: number;
}

export interface DownloadsGuardTrackerSummary {
  available: boolean;
  trackers: DownloadsGuardTracker[];
  /** Announce hosts nothing could classify — a tracker that moved domain. */
  unknown_hosts: { host: string; count: number }[];
  unclassified_count: number;
  unclassified_bytes: number;
  total_count: number;
}

/** The trigger decision, so the card shows what the scheduler concluded. */
export interface DownloadsGuardEvaluation {
  enabled: boolean;
  available: boolean;
  path: string | null;
  free_bytes: number;
  /** Bytes the in-flight downloads still have to write; null if unknown. */
  pending_bytes: number | null;
  /** free_bytes minus pending_bytes — the figure the trigger acts on. */
  projected_free_bytes: number;
  total_bytes: number;
  min_free_bytes: number;
  reclaim_bytes: number;
  daily_cap_bytes: number;
  freed_today_bytes: number;
  trackers: string[];
  triggered: boolean;
  reason: string;
  target_bytes?: number;
}

export interface DownloadsGuardEntry {
  name: string;
  size_bytes: number;
  mtime: number;
  tracker_label: string | null;
  /** 'preferred' = from a nominated tracker; 'fallback' = those ran dry. */
  phase: 'preferred' | 'fallback';
}

export interface DownloadsGuardPreview {
  evaluation: DownloadsGuardEvaluation;
  error?: string | null;
  target_bytes: number;
  planned_bytes: number;
  preferred_bytes: number;
  fallback_bytes: number;
  preferred_available: number;
  eligible_count: number;
  short: boolean;
  entries: DownloadsGuardEntry[];
}

export interface DownloadsGuardResult {
  at: string;
  dry_run: boolean;
  forced: boolean;
  triggered: boolean;
  reason: string;
  free_before_bytes: number;
  free_after_bytes: number;
  min_free_bytes: number;
  freed_bytes: number;
  freed_preferred_bytes: number;
  freed_fallback_bytes: number;
  deleted: DownloadsGuardEntry[];
  failed: string[];
  short: boolean;
  error: string | null;
}

export interface DownloadsGuardStatus {
  running: boolean;
  last_run: string | null;
  last_result: DownloadsGuardResult | null;
  /** Last time the watcher looked, whether or not it acted. */
  last_check: string | null;
  last_check_free_bytes: number | null;
  evaluation: DownloadsGuardEvaluation | null;
  server_tz: string | null;
}

export interface DownloadsGuardLogEntry {
  at: string;
  outcome: 'freed' | 'partial' | 'preview' | 'error' | 'nothing';
  summary: string;
  freed_bytes: number;
  free_after_bytes: number;
  dry_run: boolean;
}

export async function fetchDownloadsGuardTrackers(): Promise<DownloadsGuardTrackerSummary> {
  const resp = await apiFetch('/api/downloads-guard/trackers');
  if (!resp.ok) {
    return {
      available: false, trackers: [], unknown_hosts: [],
      unclassified_count: 0, unclassified_bytes: 0, total_count: 0,
    };
  }
  return resp.json();
}

export async function fetchDownloadsGuardPreview(): Promise<DownloadsGuardPreview> {
  const resp = await apiFetch('/api/downloads-guard/preview');
  if (!resp.ok) throw new Error(`Preview failed (${resp.status})`);
  return resp.json();
}

export async function fetchDownloadsGuardStatus(): Promise<{
  status: DownloadsGuardStatus;
  log: DownloadsGuardLogEntry[];
}> {
  const resp = await apiFetch('/api/downloads-guard/status');
  if (!resp.ok) throw new Error(`Failed to fetch Downloads Guard status (${resp.status})`);
  return resp.json();
}

export async function triggerDownloadsGuardRun(dryRun = true): Promise<{ ok: boolean; dry_run: boolean }> {
  const resp = await apiFetch('/api/downloads-guard/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dry_run: dryRun }),
  });
  return resp.json();
}
