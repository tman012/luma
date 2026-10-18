/**
 * MovieNightCard — one weekly grab, a short settings surface.
 *
 * Everything the automation used to expose (canon list weights, release format
 * weights, rank cutoffs, seeder floors, size caps, a second track) is now a
 * constant in app/movie_night.py. What's left is the only part worth a control:
 * whether it runs, when, which Radarr profile the grab lands on, and the IMDb
 * fame/quality floors that keep obscure mid-tier remuxes out of the queue.
 *
 * "Rebuild index" is an action, not a setting — the canon list only needs
 * building once, and again if the seed list itself changes.
 */

import { SECONDARY_BTN } from './components/legacyTokens';
import { RealRunConfirm } from './components/RealRunConfirm';
import { useState, useEffect, useCallback } from 'react';
import { SettingsCard } from './components/SettingsCard';
import { FormField } from './components/FormField';
import { ChipPicker } from './components/ChipPicker';
import { useSettings } from './useSettings';
import {
  fetchRadarrProfiles,
  fetchCanonStats,
  buildCanonIndex,
  fetchMovieNightTrackers,
  fetchMovieNightPreview,
  fetchMovieNightStatus,
  triggerMovieNightRun,
  type QualityProfile,
  type CanonStats,
  type IndexerTracker,
  type MovieNightPreview,
  type MovieNightStatus,
  type MovieNightLogEntry,
} from '@/services/settingsApi';

const TRACKERS_PATH = 'automation.movie_night.trackers';

const ALL_PATHS = [
  'automation.movie_night.enabled',
  'automation.movie_night.weekday',
  'automation.movie_night.hour',
  'automation.movie_night.minute',
  'automation.movie_night.quality_profile_id',
  'automation.movie_night.min_year',
  'automation.movie_night.imdb_min_votes',
  'automation.movie_night.imdb_min_rating',
  TRACKERS_PATH,
];

const WEEKDAYS = [
  { value: 0, label: 'Monday' }, { value: 1, label: 'Tuesday' },
  { value: 2, label: 'Wednesday' }, { value: 3, label: 'Thursday' },
  { value: 4, label: 'Friday' }, { value: 5, label: 'Saturday' },
  { value: 6, label: 'Sunday' },
];

/**
 * One time picker over the two integer settings the backend schedules on.
 *
 * The backend keeps hour and minute apart so a half-typed value can't park the
 * schedule at an unparseable string, but two number boxes is a poor way to say
 * "00:30" — so the control is a single native time input and the split happens
 * here. Empty (the user cleared the field) writes nothing rather than resetting
 * the schedule to midnight. Same control as GenreRouletteCard's.
 */
function RunTimeField() {
  const { getByPath, setByPath } = useSettings();
  const hour = Number(getByPath('automation.movie_night.hour') ?? 0);
  const minute = Number(getByPath('automation.movie_night.minute') ?? 0);
  const pad = (n: number) => String(Number.isFinite(n) ? n : 0).padStart(2, '0');

  return (
    <FormField
      label="Run time"
      path="automation.movie_night.hour"
      type="time"
      value={`${pad(hour)}:${pad(minute)}`}
      onChange={(v) => {
        const [h, m] = String(v).split(':');
        if (h === undefined || m === undefined || h === '') return;
        setByPath('automation.movie_night.hour', Number(h));
        setByPath('automation.movie_night.minute', Number(m));
      }}
    />
  );
}

const OUTCOME_STYLES: Record<string, string> = {
  grabbed: 'text-emerald-300/80',
  failed: 'text-amber-300/80',
  error: 'text-rose-300/80',
  nothing: 'text-white/40',
};

export function MovieNightCard() {
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [stats, setStats] = useState<CanonStats | null>(null);
  const [trackers, setTrackers] = useState<IndexerTracker[]>([]);
  const [status, setStatus] = useState<MovieNightStatus | null>(null);
  const [log, setLog] = useState<MovieNightLogEntry[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [preview, setPreview] = useState<MovieNightPreview | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const running = Boolean(status?.running);
  // v175 (PM): a real run asks first; Run sends the same single POST as before, Cancel sends nothing
  const [confirmRun, setConfirmRun] = useState(false);
  const cancelRun = useCallback(() => setConfirmRun(false), []);

  const refresh = useCallback(async () => {
    fetchCanonStats().then(setStats).catch(() => setStats(null));
    try {
      const s = await fetchMovieNightStatus();
      setStatus(s.status ?? null);
      setLog(Array.isArray(s.log) ? s.log : []);
      return s.status;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    fetchRadarrProfiles().then(setProfiles).catch(() => setProfiles([]));
    fetchMovieNightTrackers()
      .then((r) => setTrackers(r.selectable ? r.trackers : []))
      .catch(() => setTrackers([]));
    refresh();
  }, [refresh]);

  /**
   * A run walks up to 40 films with a 1s pause between searches, so it can take
   * minutes. Firing the POST and refreshing once — which is what this card used
   * to do — always read back the status from before the run finished, so the
   * outcome never appeared and the button looked dead. Poll until it clears.
   */
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

  const handleRebuild = () => withBusy('build', async () => {
    const r = await buildCanonIndex(true);
    return `Canon rebuilt — ${r.resolved} films resolved, ${r.unresolved} unresolved.`;
  });

  const handlePreview = () => withBusy('preview', async () => {
    const p = await fetchMovieNightPreview();
    setPreview(p);
    if (p.error) return p.error;
    return p.pick
      ? `Next up: ${p.pick.title} (${p.pick.year})`
      : p.reason || 'Nothing available right now';
  });

  const handleRunNow = () => withBusy('run', async () => {
    const baseline = status?.last_run ?? null;
    await triggerMovieNightRun(false);
    pollUntilIdle(baseline);
    return 'Run started — this can take a few minutes. Result appears below.';
  });

  return (
    <SettingsCard
      title="Movie night"
      paths={ALL_PATHS}
      headerToggle="automation.movie_night.enabled"
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="2" y="2" width="20" height="20" rx="2.18" />
          <line x1="7" y1="2" x2="7" y2="22" /><line x1="17" y1="2" x2="17" y2="22" />
          <line x1="2" y1="12" x2="22" y2="12" />
        </svg>
      }
      headerRight={
        <button
          type="button"
          onClick={handlePreview}
          disabled={busy !== null || running}
          className={SECONDARY_BTN}
        >
          {busy === 'preview' ? 'Checking…' : 'Preview pick'}
        </button>
      }
    >
      {/* Coverage summary. "English gaps" is the number that matters — the
          Radarr profile only accepts English audio, so the foreign-language
          portion of the backlog is never a candidate. */}
      <div className="col-span-full grid grid-cols-2 min-[600px]:grid-cols-4 gap-2" data-testid="movie-night-stats">
        {[
          ['Great films', stats?.total, null],
          ['In Plex', stats?.owned, null],
          ['Missing', stats?.missing, null],
          ['English gaps', stats?.missing_english,
            stats && stats.missing_english != null
              ? `${stats.missing - stats.missing_english} foreign, skipped`
              : null],
        ].map(([label, val, sub]) => (
          <div key={label as string} className="rounded-[12px] border border-white/[0.08] bg-white/[0.04] px-3 py-2">
            <p className="whitespace-nowrap text-[12px] text-white/35">{label}</p>
            <p className="mt-0.5 text-[18px] font-semibold text-white/90">{(val as number) ?? '—'}</p>
            {sub && <p className="text-[12px] text-white/25">{sub}</p>}
          </div>
        ))}
      </div>
      <FormField label="Quality profile (Radarr)" path="automation.movie_night.quality_profile_id"
        type="select" options={profileOptions} />
      <FormField label="Run day" path="automation.movie_night.weekday" type="select" options={WEEKDAYS} />
      <RunTimeField />
      <FormField label="Oldest year to consider" path="automation.movie_night.min_year"
        type="number" min={0} max={2100} />
      <FormField label="IMDb minimum votes" path="automation.movie_night.imdb_min_votes"
        type="number" min={0} max={2000000} step={5000} />
      <FormField label="IMDb minimum rating" path="automation.movie_night.imdb_min_rating"
        type="number" min={0} max={10} step={0.1} />
      {/* The list only ever contains the movie trackers (MOVIE_TRACKERS in
          app/movie_night.py), so selecting none means all of *those* — never
          every indexer in Prowlarr. Selected names become indexer ids at search
          time, so an unticked tracker is not contacted at all. */}
      {trackers.length > 0 && (
        <ChipPicker
          label="Trackers"
          path={TRACKERS_PATH}
          chips={trackers.map((t) => ({ name: t.name, enabled: t.enabled }))}
        />
      )}

      {/* Preview output */}
      {preview && !preview.error && (
        <div className="col-span-full rounded-[12px] border border-white/[0.08] bg-white/[0.04] p-3">
          <p className="mb-2 text-[12px] text-white/35">
            Next run · {preview.candidates} eligible ·{' '}
            {preview.trackers?.join(', ') || 'no trackers'}
            {preview.skipped_imdb ? ` · ${preview.skipped_imdb} below IMDb floors` : ''}
          </p>
          {preview.pick ? (
            <>
              <p className="text-[14px] font-medium text-white/90">
                {preview.pick.title} <span className="text-white/40">({preview.pick.year})</span>
                <span className="ml-2 text-[12px] text-white/35">#{preview.pick.rank}</span>
              </p>
              <p className="text-[12px] text-white/45">
                {preview.pick.quality} · {preview.pick.size_gb}GB · {preview.pick.seeders} seeders · {preview.pick.tracker}
              </p>
              <p className="truncate text-[12px] text-white/25">{preview.pick.torrent_name}</p>
            </>
          ) : (
            <p className="text-[13px] text-white/35">{preview.reason || 'No 2160p Remux available'}</p>
          )}
        </div>
      )}

      {msg && <p className="col-span-full text-[12px] text-white/50">{msg}</p>}

      {/* Last run. Persisted server-side, so a weekly job still reports its
          outcome after a container restart. */}
      <div className="col-span-full rounded-[12px] border border-white/[0.08] bg-white/[0.04] px-3 py-2">
        <p className="text-[12px] text-white/35">
          Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'never'}
        </p>
        {status?.last_result && (
          <p className={`mt-1 text-[12px] ${
            status.last_result.error ? OUTCOME_STYLES.error
              : status.last_result.grabbed ? OUTCOME_STYLES.grabbed
              : status.last_result.pick ? OUTCOME_STYLES.failed
              : OUTCOME_STYLES.nothing}`}>
            {status.last_result.error
              || (status.last_result.grabbed && status.last_result.pick
                ? `Grabbed ${status.last_result.pick.title} (${status.last_result.pick.year}) — ${status.last_result.pick.quality}, ${status.last_result.pick.size_gb}GB, ${status.last_result.pick.tracker}`
                : status.last_result.pick
                ? `Failed to grab ${status.last_result.pick.title}: ${status.last_result.grab_detail || 'unknown reason'}`
                : status.last_result.reason || 'Nothing grabbed')}
          </p>
        )}
        {status?.last_result && !status.last_result.error && (
          <p className="mt-0.5 text-[12px] text-white/25">
            Searched {status.last_result.searched} of {status.last_result.candidates} eligible candidates
            {status.last_result.skipped_foreign
              ? ` · ${status.last_result.skipped_foreign} non-English skipped`
              : ''}
            {status.last_result.skipped_old
              ? ` · ${status.last_result.skipped_old} older than ${status.last_result.min_year}`
              : ''}
          </p>
        )}
      </div>

      {/* Run history — every run, not just the ones that grabbed something. */}
      {log.length > 0 && (
        <div className="col-span-full flex flex-col gap-1">
          <p className="text-[12px] text-white/35">Recent runs</p>
          {log.slice(0, 6).map((e, i) => (
            <div key={`${e.at}-${i}`} className="flex items-baseline justify-between gap-3 text-[12px]">
              <span className={`truncate ${OUTCOME_STYLES[e.outcome] || 'text-white/50'}`}>
                {e.summary}
              </span>
              <span className="shrink-0 text-[12px] text-white/25">
                {new Date(e.at).toLocaleDateString()}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="col-span-full flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => setConfirmRun(true)} disabled={busy !== null || running}
          className={SECONDARY_BTN}>
          {running ? 'Running…' : 'Run now'}
        </button>
        <button type="button" onClick={handleRebuild} disabled={busy !== null}
          className={SECONDARY_BTN}>
          {busy === 'build' ? 'Rebuilding…' : 'Rebuild film list'}
        </button>
      </div>
      <RealRunConfirm open={confirmRun} onCancel={cancelRun} onRun={() => { setConfirmRun(false); handleRunNow(); }} />
    </SettingsCard>
  );
}
