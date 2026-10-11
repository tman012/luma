/**
 * GenreRouletteCard — nightly random-genre discovery.
 *
 * The counterpart to MovieNightCard. That one is deliberately narrow (one
 * canon film a week at an absolute 2160p Remux bar) and so needs almost no
 * settings; this one is about breadth, and breadth is exactly what the user
 * wants to steer — how many genres, how many films, how old, how famous (IMDb votes).
 * So it carries real controls where Movie Night carries almost none.
 */

import { useState, useEffect, useCallback } from 'react';
import { SettingsCard } from './components/SettingsCard';
import { FormField } from './components/FormField';
import { ToggleSwitch } from './components/ToggleSwitch';
import { ChipPicker } from './components/ChipPicker';
import { useSettings } from './useSettings';
import {
  fetchRadarrProfiles,
  fetchGenreRouletteGenres,
  fetchGenreRouletteTrackers,
  fetchGenreRoulettePreview,
  fetchGenreRouletteStatus,
  triggerGenreRouletteRun,
  type QualityProfile,
  type IndexerTracker,
  type GenreRouletteResult,
  type GenreRouletteStatus,
  type GenreRouletteLogEntry,
} from '@/services/settingsApi';

const GENRES_PATH = 'automation.genre_roulette.genres';
const TRACKERS_PATH = 'automation.genre_roulette.trackers';
const HOUR_PATH = 'automation.genre_roulette.hour';
const MINUTE_PATH = 'automation.genre_roulette.minute';

const ALL_PATHS = [
  'automation.genre_roulette.enabled',
  HOUR_PATH,
  MINUTE_PATH,
  'automation.genre_roulette.genres_per_night',
  'automation.genre_roulette.movies_per_night',
  'automation.genre_roulette.year_from',
  'automation.genre_roulette.year_to',
  'automation.genre_roulette.min_votes',
  'automation.genre_roulette.imdb_min_rating',
  'automation.genre_roulette.imdb_min_votes',
  'automation.genre_roulette.min_seeders',
  'automation.genre_roulette.quality_profile_id',
  GENRES_PATH,
  TRACKERS_PATH,
];

const OUTCOME_STYLES = {
  grabbed: 'text-emerald-300/80',
  failed: 'text-amber-300/80',
  error: 'text-rose-300/80',
  nothing: 'text-white/40',
} as const;

function outcomeClass(r: GenreRouletteResult): string {
  if (r.error) return OUTCOME_STYLES.error;
  if (r.grabbed > 0) return OUTCOME_STYLES.grabbed;
  if (r.picks?.length) return OUTCOME_STYLES.failed;
  return OUTCOME_STYLES.nothing;
}

/** Mirrors genre_roulette.summarise() so the card reads the same as the log. */
function describe(r: GenreRouletteResult): string {
  if (r.error) return r.error;
  const genres = r.genres?.join(', ') || 'no genres';
  const got = (r.picks || []).filter((p) => p.grabbed);
  if (got.length) {
    const names = got.map((p) => `${p.title} (${p.year})`).join('; ');
    // Naming the shortfall matters: "grabbed 2" when 3 were asked for reads as
    // a broken setting unless the run says why it stopped short.
    const short =
      r.wanted && got.length < r.wanted
        ? r.reason
          ? ` (of ${r.wanted} — ${r.reason})`
          : ` of ${r.wanted}`
        : '';
    return `${genres} → grabbed ${got.length}${short}: ${names}`;
  }
  if (r.picks?.length && r.dry_run) {
    return `${genres} → would grab ${r.picks
      .map((p) => `${p.title} (${p.year})`)
      .join('; ')}`;
  }
  if (r.picks?.length) {
    return `${genres} → ${r.picks.length} found but none grabbed: ${
      r.picks[0]?.detail || 'unknown reason'
    }`;
  }
  return `${genres} → ${r.reason || 'nothing grabbed'}`;
}

/**
 * One time picker over the two integer settings the backend schedules on.
 *
 * The backend keeps hour and minute apart so a half-typed value can't park the
 * schedule at an unparseable string, but two number boxes is a poor way to say
 * "22:30" — so the control is a single native time input and the split happens
 * here. Empty (the user cleared the field) writes nothing rather than
 * resetting the schedule to midnight.
 */
function RunTimeField() {
  const { getByPath, setByPath } = useSettings();
  const hour = Number(getByPath(HOUR_PATH) ?? 0);
  const minute = Number(getByPath(MINUTE_PATH) ?? 0);
  const pad = (n: number) => String(Number.isFinite(n) ? n : 0).padStart(2, '0');

  return (
    <FormField
      label="Run time (24h)"
      path={HOUR_PATH}
      type="time"
      value={`${pad(hour)}:${pad(minute)}`}
      onChange={(v) => {
        const [h, m] = String(v).split(':');
        if (h === undefined || m === undefined || h === '') return;
        setByPath(HOUR_PATH, Number(h));
        setByPath(MINUTE_PATH, Number(m));
      }}
      hint="Earliest time the nightly run may start, on the server's clock"
    />
  );
}

export function GenreRouletteCard() {
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [genres, setGenres] = useState<string[]>([]);
  const [trackers, setTrackers] = useState<IndexerTracker[]>([]);
  const [status, setStatus] = useState<GenreRouletteStatus | null>(null);
  const [log, setLog] = useState<GenreRouletteLogEntry[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [preview, setPreview] = useState<GenreRouletteResult | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const running = Boolean(status?.running);

  const refresh = useCallback(async () => {
    try {
      const s = await fetchGenreRouletteStatus();
      setStatus(s.status ?? null);
      setLog(Array.isArray(s.log) ? s.log : []);
      return s.status;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    fetchRadarrProfiles().then(setProfiles).catch(() => setProfiles([]));
    fetchGenreRouletteGenres().then((r) => setGenres(r.genres)).catch(() => setGenres([]));
    fetchGenreRouletteTrackers()
      .then((r) => setTrackers(r.selectable ? r.trackers : []))
      .catch(() => setTrackers([]));
    refresh();
  }, [refresh]);

  /** A run does up to 25 indexer searches with a pause between each, so it can
   *  take minutes. Keep polling until last_run advances (not merely until
   *  running flips false — that raced ahead of the stamp and left a stale time). */
  const pollUntilIdle = useCallback((baseline: string | null) => {
    let polls = 0;
    const iv = setInterval(async () => {
      polls++;
      const s = await refresh();
      const advanced = Boolean(s?.last_run && s.last_run !== baseline);
      if ((!s?.running && advanced) || polls >= 60) {
        clearInterval(iv);
        if (!s?.running && advanced) setMsg(null);
      }
    }, 3000);
  }, [refresh]);

  const profileOptions = [
    { value: 0, label: 'Select a profile…' },
    ...profiles.map((p) => ({ value: p.id, label: p.name })),
  ];

  const withBusy = useCallback(async (key: string, fn: () => Promise<string>) => {
    setBusy(key);
    setMsg(null);
    try {
      setMsg(await fn());
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Failed');
    } finally {
      setBusy(null);
      refresh();
    }
  }, [refresh]);

  const handlePreview = () => withBusy('preview', async () => {
    const p = await fetchGenreRoulettePreview();
    setPreview(p);
    return p.error || describe(p);
  });

  const handleRunNow = () => withBusy('run', async () => {
    const baseline = status?.last_run ?? null;
    await triggerGenreRouletteRun(false);
    pollUntilIdle(baseline);
    return 'Run started — this can take a few minutes. Result appears below.';
  });

  return (
    <SettingsCard
      title="Genre Roulette"
      subtitle="A few well-liked films you don't own, every night"
      paths={ALL_PATHS}
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <circle cx="12" cy="12" r="10" />
          <circle cx="12" cy="12" r="3" />
          <path d="M12 2v5M12 17v5M2 12h5M17 12h5" />
        </svg>
      }
      note="Every night, spins a few genres at random, asks TMDb for the best-rated films in them from your year range, drops everything already in your library, and grabs the configured number in 720p/1080p. Each genre is worked down from the top of its ratings and remembers where it got to, so the best films arrive first and later nights carry on below them. Randomising the genres is what keeps the breadth. English-audio only. Films with nothing usable are re-checked in 30 days."
      headerRight={
        <button
          type="button"
          onClick={handlePreview}
          disabled={busy !== null || running}
          className="rounded-lg border border-white/10 bg-white/8 px-3 py-2 text-[12px] font-semibold text-white/80 disabled:opacity-40"
        >
          {busy === 'preview' ? 'Spinning…' : 'Preview spin'}
        </button>
      }
    >
      <ToggleSwitch label="Enable Genre Roulette" path="automation.genre_roulette.enabled"
        hint="Nothing runs until this is on" />
      <FormField label="Quality profile (Radarr)" path="automation.genre_roulette.quality_profile_id"
        type="select" options={profileOptions} hint="Standard profile — must permit 720p/1080p" />

      <FormField label="Movies per night" path="automation.genre_roulette.movies_per_night"
        type="number" min={1} max={20} hint="How many to actually grab (1–20)" />
      <FormField label="Genres per night" path="automation.genre_roulette.genres_per_night"
        type="number" min={1} max={18} hint="How many to draw from the wheel" />

      <FormField label="Earliest year" path="automation.genre_roulette.year_from"
        type="number" min={1900} max={2100} hint="Blank = 40 years ago" />
      <FormField label="Latest year" path="automation.genre_roulette.year_to"
        type="number" min={1900} max={2100} hint="Blank = 3 years ago" />

      <FormField label="IMDb minimum votes" path="automation.genre_roulette.imdb_min_votes"
        type="number" min={0} max={2000000} step={5000}
        hint="The gate — IMDb vote count (default 120,000)" />
      <FormField label="IMDb minimum rating" path="automation.genre_roulette.imdb_min_rating"
        type="number" min={0} max={10} step={0.1}
        hint="0 = ignore rating entirely (default 0)" />

      <FormField label="TMDb votes prefilter" path="automation.genre_roulette.min_votes"
        type="number" min={0} max={100000} step={100}
        hint="Cheap fame prefilter to save OMDb calls; auto-relaxed to match the IMDb vote gate (default 800)" />

      <RunTimeField />
      <FormField label="Minimum seeders" path="automation.genre_roulette.min_seeders"
        type="number" min={0} max={1000} hint="Release health floor (default 5)" />

      {genres.length > 0 && (
        <ChipPicker
          label="Genres in play"
          path={GENRES_PATH}
          chips={genres.map((g) => ({ name: g }))}
          emptyHint={`None selected — all ${genres.length} genres can be drawn. Tick some to narrow the wheel.`}
          selectedHint={(n) =>
            n === 1
              ? 'Only this genre will ever be drawn.'
              : `Drawn from these ${n} genres only.`}
        />
      )}

      {trackers.length > 0 && (
        <ChipPicker
          label="Trackers"
          path={TRACKERS_PATH}
          chips={trackers.map((t) => ({ name: t.name, enabled: t.enabled }))}
          emptyHint={`None selected — all ${trackers.length} get searched. Tick some to spare the rest.`}
          selectedHint={(n) =>
            `Only ${n === 1 ? 'this tracker' : `these ${n}`} will be searched.`}
        />
      )}

      {/* Preview output */}
      {preview && !preview.error && (
        <div className="col-span-full rounded-lg border border-white/8 bg-black/25 p-3">
          <p className="mb-2 text-[11px] uppercase tracking-wide text-white/35">
            Tonight’s spin · {preview.genres?.join(' · ') || 'no genres'} ·{' '}
            {preview.candidates} unowned of {preview.considered} · {preview.year_from}–{preview.year_to}
          </p>
          {preview.picks?.length ? (
            preview.picks.map((p) => (
              <div key={p.tmdb_id} className="mb-2 last:mb-0">
                <p className="text-[14px] font-medium text-white/90">
                  {p.title} <span className="text-white/40">({p.year})</span>
                  <span className="ml-2 text-[11px] text-white/35">
                    {p.genre} · {p.rating.toFixed(1)}
                  </span>
                </p>
                <p className="text-[12px] text-white/45">
                  {p.quality} · {p.size_gb}GB · {p.seeders} seeders · {p.tracker}
                </p>
                <p className="truncate text-[11px] text-white/25">{p.torrent_name}</p>
              </div>
            ))
          ) : (
            <p className="text-[13px] text-white/35">{preview.reason || 'Nothing usable found'}</p>
          )}
          {/* Why a searched film was passed over. The eligibility filters discard
              most of what an indexer returns, so "found 93, kept 0" needs saying. */}
          {preview.scanned?.some((s) => !s.picked) && (
            <div className="mt-2 border-t border-white/8 pt-2">
              {preview.scanned.filter((s) => !s.picked).map((s) => (
                <p key={`${s.title}-${s.year}`} className="text-[11px] text-white/25">
                  {s.title} ({s.year}) — {s.releases_found} found, {s.eligible} eligible
                  {Object.keys(s.rejects).length > 0 &&
                    `: ${Object.entries(s.rejects).map(([k, v]) => `${v}× ${k}`).join(', ')}`}
                </p>
              ))}
            </div>
          )}
        </div>
      )}

      {msg && <p className="col-span-full text-[12px] text-white/50">{msg}</p>}

      {/* Last run — persisted server-side so it survives a restart. */}
      <div className="col-span-full rounded-lg border border-white/8 bg-black/20 px-3 py-2">
        <p className="text-[11px] uppercase tracking-wide text-white/35">
          Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'Never'}
        </p>
        {/* The time setting is the server's clock, so the naive ISO is parsed
            as local and shown as typed rather than shifted into the browser's
            timezone — and the zone is named, because "next run 22:30" with an
            unstated zone is how a schedule 4h off its intended hour hid. This
            line also distinguishes a spent slot from a dead scheduler. */}
        <p className="text-[11px] uppercase tracking-wide text-white/35">
          Next run: {status?.next_run
            ? `${new Date(status.next_run).toLocaleString()}${status.server_tz ? ` ${status.server_tz}` : ''}`
            : 'Not scheduled'}
        </p>
        {status?.last_result && (
          <p className={`mt-1 text-[12px] ${outcomeClass(status.last_result)}`}>
            {describe(status.last_result)}
          </p>
        )}
        {/* Films Radarr refused, with its reason. Without this a short night is
            just a smaller number with no explanation. */}
        {status?.last_result?.picks
          ?.filter((p) => !p.grabbed)
          .map((p) => (
            <p key={p.tmdb_id} className="mt-0.5 text-[11px] text-amber-300/50">
              {p.title} ({p.year}) refused — {p.detail || 'unknown reason'}
            </p>
          ))}
        {status?.last_result && !status.last_result.error && (
          <p className="mt-0.5 text-[11px] text-white/25">
            Searched {status.last_result.searched} of {status.last_result.candidates} unowned
            {status.last_result.considered
              ? ` (from ${status.last_result.considered} in genre)`
              : ''}
            {status.last_result.genres_failed?.length
              ? ` · TMDb failed for ${status.last_result.genres_failed.join(', ')}`
              : ''}
          </p>
        )}
      </div>

      {/* Run history — every night, not just the ones that grabbed something. */}
      {log.length > 0 && (
        <div className="col-span-full flex flex-col gap-1">
          <p className="text-[11px] uppercase tracking-wide text-white/35">Recent nights</p>
          {log.slice(0, 8).map((e, i) => (
            <div key={`${e.at}-${i}`} className="flex items-baseline justify-between gap-3 text-[12px]">
              <span className={`truncate ${OUTCOME_STYLES[e.outcome] || 'text-white/50'}`}>
                {e.summary}
              </span>
              <span className="shrink-0 text-[11px] text-white/25">
                {new Date(e.at).toLocaleDateString()}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="col-span-full flex flex-wrap items-center gap-2">
        <button type="button" onClick={handleRunNow} disabled={busy !== null || running}
          className="rounded-lg border border-amber-500/25 bg-amber-500/12 px-3 py-2 text-[12px] font-semibold text-amber-300 disabled:opacity-40">
          {running ? 'Running…' : 'Run now (downloads for real)'}
        </button>
        <span className="text-[11px] text-white/30">
          Preview is always safe — it spins the wheel and searches, but grabs nothing.
        </span>
      </div>
    </SettingsCard>
  );
}
