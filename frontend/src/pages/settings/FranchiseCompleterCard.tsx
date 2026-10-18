/**
 * FranchiseCompleterCard — nightly auto-grab of missing parts from
 * super-popular almost-complete TMDb franchises.
 *
 * Mirrors GenreRouletteCard / MovieNightCard: enable, run time, quality
 * profile, fame gate, caps, last run, preview + run now.
 */

import { SECONDARY_BTN } from './components/legacyTokens';
import { RealRunConfirm } from './components/RealRunConfirm';
import { useState, useEffect, useCallback } from 'react';
import { SettingsCard } from './components/SettingsCard';
import { FormField } from './components/FormField';
import { useSettings } from './useSettings';
import {
  fetchRadarrProfiles,
  fetchFranchiseCompleterPreview,
  fetchFranchiseCompleterStatus,
  triggerFranchiseCompleterRun,
  type QualityProfile,
  type FranchiseCompleterResult,
  type FranchiseCompleterStatus,
  type FranchiseCompleterLogEntry,
} from '@/services/settingsApi';

const HOUR_PATH = 'automation.franchise_completer.hour';
const MINUTE_PATH = 'automation.franchise_completer.minute';

const ALL_PATHS = [
  'automation.franchise_completer.enabled',
  HOUR_PATH,
  MINUTE_PATH,
  'automation.franchise_completer.quality_profile_id',
  'automation.franchise_completer.min_fame',
  'automation.franchise_completer.max_missing_parts',
  'automation.franchise_completer.movies_per_night',
  'automation.franchise_completer.min_year',
];

const OUTCOME_STYLES = {
  grabbed: 'text-emerald-300/80',
  failed: 'text-amber-300/80',
  error: 'text-rose-300/80',
  nothing: 'text-white/40',
} as const;

function outcomeClass(r: FranchiseCompleterResult): string {
  if (r.error) return OUTCOME_STYLES.error;
  if ((r.grabbed || 0) > 0) return OUTCOME_STYLES.grabbed;
  if (r.picks?.length) return OUTCOME_STYLES.failed;
  return OUTCOME_STYLES.nothing;
}

function describe(r: FranchiseCompleterResult): string {
  if (r.error) return r.error;
  const got = (r.picks || []).filter((p) => p.grabbed);
  if (got.length) {
    return `Grabbed ${got.length}: ${got
      .map((p) => `${p.title} (${p.year}) [${p.collection_name}]`)
      .join('; ')}`;
  }
  if (r.picks?.length && r.dry_run) {
    return `Would grab ${r.picks.length}: ${r.picks
      .map((p) => `${p.title} (${p.year}) [${p.collection_name}]`)
      .join('; ')}`;
  }
  if (r.picks?.length) {
    return `Nothing grabbed: ${r.picks
      .map((p) => `${p.title} — ${p.detail || 'unknown'}`)
      .join('; ')}`;
  }
  return r.reason || 'Nothing to grab';
}

function RunTimeField() {
  const { getByPath, setByPath } = useSettings();
  const hour = Number(getByPath(HOUR_PATH) ?? 23);
  const minute = Number(getByPath(MINUTE_PATH) ?? 30);
  const pad = (n: number) => String(Number.isFinite(n) ? n : 0).padStart(2, '0');

  return (
    <FormField
      label="Run time"
      path={HOUR_PATH}
      type="time"
      value={`${pad(hour)}:${pad(minute)}`}
      onChange={(v) => {
        const [h, m] = String(v).split(':');
        if (h === undefined || m === undefined || h === '') return;
        setByPath(HOUR_PATH, Number(h));
        setByPath(MINUTE_PATH, Number(m));
      }}
    />
  );
}

export function FranchiseCompleterCard() {
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [status, setStatus] = useState<FranchiseCompleterStatus | null>(null);
  const [log, setLog] = useState<FranchiseCompleterLogEntry[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [preview, setPreview] = useState<FranchiseCompleterResult | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const running = Boolean(status?.running);
  // v175 (PM): a real run asks first; Run sends the same single POST as before, Cancel sends nothing
  const [confirmRun, setConfirmRun] = useState(false);
  const cancelRun = useCallback(() => setConfirmRun(false), []);

  const refresh = useCallback(async () => {
    try {
      const s = await fetchFranchiseCompleterStatus();
      setStatus(s.status ?? null);
      setLog(Array.isArray(s.log) ? s.log : []);
      return s.status;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    fetchRadarrProfiles().then(setProfiles).catch(() => setProfiles([]));
    refresh();
  }, [refresh]);

  const pollUntilIdle = useCallback((baseline: string | null) => {
    let polls = 0;
    const iv = setInterval(async () => {
      polls++;
      const s = await refresh();
      const advanced = Boolean(s?.last_run && s.last_run !== baseline);
      if ((!s?.running && advanced) || polls >= 40) {
        clearInterval(iv);
        if (!s?.running && advanced) setMsg(null);
      }
    }, 2500);
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
    const p = await fetchFranchiseCompleterPreview();
    setPreview(p);
    return p.error || describe(p);
  });

  const handleRunNow = () => withBusy('run', async () => {
    const baseline = status?.last_run ?? null;
    await triggerFranchiseCompleterRun(false);
    pollUntilIdle(baseline);
    return 'Run started. Result appears below.';
  });

  return (
    <SettingsCard
      title="Franchise completer"
      paths={ALL_PATHS}
      headerToggle="automation.franchise_completer.enabled"
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
          <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
          <path d="M8 7h8M8 11h6" />
        </svg>
      }
      headerRight={
        <button
          type="button"
          onClick={handlePreview}
          disabled={busy !== null || running}
          className={SECONDARY_BTN}
        >
          {busy === 'preview' ? 'Checking…' : 'Preview gaps'}
        </button>
      }
    >
      <FormField
        label="Quality profile (Radarr)"
        path="automation.franchise_completer.quality_profile_id"
        type="select"
        options={profileOptions}
      />
      <RunTimeField />
      <FormField
        label="Minimum fame (IMDb votes)"
        path="automation.franchise_completer.min_fame"
        type="number"
        min={0}
        max={5000000}
        step={50000}
      />
      <FormField
        label="Max missing parts"
        path="automation.franchise_completer.max_missing_parts"
        type="number"
        min={1}
        max={10}
      />
      <FormField
        label="Movies per night"
        path="automation.franchise_completer.movies_per_night"
        type="number"
        min={1}
        max={10}
      />
      <FormField
        label="Oldest year to consider"
        path="automation.franchise_completer.min_year"
        type="number"
        min={0}
        max={2100}
      />

      {preview && !preview.error && (
        <div className="col-span-full rounded-[12px] border border-white/[0.08] bg-white/[0.04] p-3">
          <p className="mb-2 text-[12px] text-white/35">
            Preview · {preview.candidates ?? 0} gaps · {preview.collections_eligible ?? 0} franchises
            {' '}≥ {Math.round(preview.min_fame || 0).toLocaleString()} fame · ≤{preview.max_missing_parts} missing
          </p>
          {preview.picks?.length ? (
            preview.picks.map((p) => (
              <div key={p.tmdb_id} className="mb-2 last:mb-0">
                <p className="text-[14px] font-medium text-white/90">
                  {p.title} <span className="text-white/40">({p.year})</span>
                </p>
                <p className="text-[12px] text-white/45">
                  {p.collection_name} · fame {Math.round(p.fame_score || 0).toLocaleString()}
                  {' '}· {p.collection_missing} missing
                </p>
              </div>
            ))
          ) : (
            <p className="text-[13px] text-white/35">{preview.reason || 'No gaps matched'}</p>
          )}
        </div>
      )}

      {msg && <p className="col-span-full text-[12px] text-white/50">{msg}</p>}

      <div className="col-span-full rounded-[12px] border border-white/[0.08] bg-white/[0.04] px-3 py-2">
        <p className="text-[12px] text-white/35">
          Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'never'}
        </p>
        <p className="text-[12px] text-white/35">
          Next run: {status?.next_run
            ? `${new Date(status.next_run).toLocaleString()}${status.server_tz ? ` ${status.server_tz}` : ''}`
            : 'not scheduled'}
        </p>
        {status?.last_result && (
          <p className={`mt-1 text-[12px] ${outcomeClass(status.last_result)}`}>
            {describe(status.last_result)}
          </p>
        )}
      </div>

      {log.length > 0 && (
        <div className="col-span-full flex flex-col gap-1">
          <p className="text-[12px] text-white/35">Recent nights</p>
          {log.slice(0, 8).map((e, i) => (
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
        <button
          type="button"
          onClick={() => setConfirmRun(true)}
          disabled={busy !== null || running}
          className={SECONDARY_BTN}
        >
          {running ? 'Running…' : 'Run now'}
        </button>
      </div>
      <RealRunConfirm open={confirmRun} onCancel={cancelRun} onRun={() => { setConfirmRun(false); handleRunNow(); }} />
    </SettingsCard>
  );
}
