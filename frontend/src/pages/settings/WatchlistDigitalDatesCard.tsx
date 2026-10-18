/**
 * WatchlistDigitalDatesCard — morning check for newly dated watchlist movies.
 *
 * Lives under Settings → Automations. Enable toggle, run time (ET), last run,
 * newly-dated finds, and browsable recent-run history.
 */

import { SECONDARY_BTN } from './components/legacyTokens';
import { useState, useEffect, useCallback } from 'react';
import { SettingsCard } from './components/SettingsCard';
import { FormField } from './components/FormField';
import { ToggleSwitch } from './components/ToggleSwitch';
import { useSettings } from './useSettings';
import {
  fetchWatchlistDigitalDatesStatus,
  triggerWatchlistDigitalDatesRun,
  type WatchlistDigitalDatesStatus,
  type WatchlistDigitalDatesLogEntry,
  type WatchlistDigitalDatesResult,
} from '@/services/settingsApi';

const HOUR_PATH = 'automation.watchlist_digital_dates.hour';
const MINUTE_PATH = 'automation.watchlist_digital_dates.minute';

const ALL_PATHS = [
  'automation.watchlist_digital_dates.enabled',
  HOUR_PATH,
  MINUTE_PATH,
];

const OUTCOME_STYLES = {
  found: 'text-emerald-300/80',
  error: 'text-rose-300/80',
  nothing: 'text-white/40',
} as const;

function describe(r: WatchlistDigitalDatesResult | null | undefined): string {
  if (!r) return 'No result yet';
  if (r.error) return r.error;
  if (r.reason) return r.reason;
  const n = r.new_count || 0;
  const c = r.changed_count || 0;
  if (n || c) return `Checked ${r.checked ?? 0}: ${n} newly dated, ${c} changed`;
  return `Checked ${r.checked ?? 0}; nothing new`;
}

function outcomeClass(r: WatchlistDigitalDatesResult | null | undefined): string {
  if (!r) return OUTCOME_STYLES.nothing;
  if (r.error) return OUTCOME_STYLES.error;
  if ((r.new_count || 0) > 0 || (r.changed_count || 0) > 0) return OUTCOME_STYLES.found;
  return OUTCOME_STYLES.nothing;
}

function RunTimeField() {
  const { getByPath, setByPath } = useSettings();
  const hour = Number(getByPath(HOUR_PATH) ?? 6);
  const minute = Number(getByPath(MINUTE_PATH) ?? 0);
  const pad = (n: number) => String(Number.isFinite(n) ? n : 0).padStart(2, '0');

  return (
    <FormField
      label="Run time (America/New_York)"
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

export function WatchlistDigitalDatesCard() {
  const [status, setStatus] = useState<WatchlistDigitalDatesStatus | null>(null);
  const [log, setLog] = useState<WatchlistDigitalDatesLogEntry[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [expandedAt, setExpandedAt] = useState<string | null>(null);

  const running = Boolean(status?.running);

  const refresh = useCallback(async () => {
    try {
      const s = await fetchWatchlistDigitalDatesStatus();
      setStatus(s.status ?? null);
      setLog(Array.isArray(s.log) ? s.log : []);
      return s.status;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

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
    }, 2500);
  }, [refresh]);

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

  const handleRunNow = () => withBusy('run', async () => {
    const baseline = status?.last_run ?? null;
    await triggerWatchlistDigitalDatesRun(false);
    pollUntilIdle(baseline);
    return 'Run started. Result appears below.';
  });

  const last = status?.last_result;
  const foundAny = Boolean(
    last && ((last.new_count || 0) > 0 || (last.changed_count || 0) > 0),
  );

  return (
    <SettingsCard
      title="Watchlist digital dates"
      paths={ALL_PATHS}
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="3" y="4" width="18" height="18" rx="2" />
          <line x1="16" y1="2" x2="16" y2="6" />
          <line x1="8" y1="2" x2="8" y2="6" />
          <line x1="3" y1="10" x2="21" y2="10" />
          <path d="M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01" />
        </svg>
      }
    >
      <ToggleSwitch
        label="Enable morning digital-date check"
        path="automation.watchlist_digital_dates.enabled"
      />
      <RunTimeField />

      {msg && <p className="col-span-full text-[12px] text-white/50">{msg}</p>}

      <div className="col-span-full rounded-[12px] border border-white/[0.08] bg-white/[0.04] px-3 py-2">
        <p className="text-[12px] text-white/35">
          Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'never'}
        </p>
        <p className="text-[12px] text-white/35">
          Next run: {status?.next_run
            ? `${new Date(status.next_run).toLocaleString()} ET`
            : 'not scheduled'}
        </p>
        <p className="mt-1 text-[12px] text-white/55">
          Last run found newly dated titles:{' '}
          <span className={foundAny ? 'text-emerald-300/90' : 'text-white/40'}>
            {status?.last_run ? (foundAny ? 'Yes' : 'No') : '—'}
          </span>
        </p>
        {last && (
          <p className={`mt-1 text-[12px] ${outcomeClass(last)}`}>
            {describe(last)}
          </p>
        )}
        {last?.changes && last.changes.length > 0 && (
          <div className="mt-2 space-y-1">
            {last.changes.slice(0, 12).map((c) => (
              <p key={`${c.tmdb_id}-${c.new_date}`} className="text-[12px] text-white/70">
                <span className="text-white/40">{c.kind === 'new' ? 'NEW' : 'CHG'}</span>
                {' '}{c.title}{c.year ? ` (${c.year})` : ''} — {c.label || c.new_date}
              </p>
            ))}
          </div>
        )}
      </div>

      {log.length > 0 && (
        <div className="col-span-full flex flex-col gap-1">
          <p className="text-[12px] text-white/35">Recent runs</p>
          {log.slice(0, 20).map((e, i) => {
            const open = expandedAt === e.at;
            return (
              <div key={`${e.at}-${i}`} className="rounded-md border border-white/5 bg-white/[0.04] px-2 py-1.5">
                <button
                  type="button"
                  className="flex w-full items-baseline justify-between gap-3 text-left text-[12px]"
                  onClick={() => setExpandedAt(open ? null : e.at)}
                >
                  <span className={`truncate ${OUTCOME_STYLES[e.outcome] || 'text-white/50'}`}>
                    {e.summary}
                  </span>
                  <span className="shrink-0 text-[12px] text-white/25">
                    {new Date(e.at).toLocaleString()}
                  </span>
                </button>
                {open && (
                  <div className="mt-1.5 border-t border-white/5 pt-1.5 text-[12px] text-white/45">
                    <p>
                      Checked {e.checked ?? '—'} · with date {e.with_date ?? '—'} ·
                      without {e.without_date ?? '—'} · notified {e.notified ?? 0}
                    </p>
                    {(e.changes || []).length === 0 ? (
                      <p className="mt-1 text-white/30">No title-level changes this run.</p>
                    ) : (
                      <ul className="mt-1 space-y-0.5">
                        {e.changes!.map((c) => (
                          <li key={`${c.tmdb_id}-${c.new_date}-${c.kind}`}>
                            [{c.kind}] {c.title}{c.year ? ` (${c.year})` : ''}:{' '}
                            {c.old_date || '—'} → {c.label || c.new_date}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <div className="col-span-full flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={handleRunNow}
          disabled={busy !== null || running}
          className={SECONDARY_BTN}
        >
          {running || busy === 'run' ? 'Running…' : 'Run now'}
        </button>
      </div>
    </SettingsCard>
  );
}

