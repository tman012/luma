/**
 * DownloadsGuardCard — the free-space floor on /downloads.
 *
 * The only automation here that *deletes*, so the card is built around a
 * different question than the acquisition cards. Those ask "what will it get?";
 * this one has to answer "what is it about to destroy, and why that file?"
 * before the user is willing to leave it running unattended. Hence the preview
 * listing every entry with its tracker and age, the live trigger verdict rather
 * than two numbers to compare by eye, and the per-tracker runway — a
 * tracker-scoped cleanup with nothing left in that tracker quietly becomes a
 * cleanup of everything else, and the sizes are what warn of it in advance.
 */

import { useState, useEffect, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { SettingsCard } from './components/SettingsCard';
import { FormField } from './components/FormField';
import { ToggleSwitch } from './components/ToggleSwitch';
import { useSettings } from './useSettings';
import { formatBytes } from '@/services/storageApi';
import {
  fetchDownloadsGuardTrackers,
  fetchDownloadsGuardPreview,
  fetchDownloadsGuardStatus,
  triggerDownloadsGuardRun,
  type DownloadsGuardTracker,
  type DownloadsGuardTrackerSummary,
  type DownloadsGuardPreview,
  type DownloadsGuardStatus,
  type DownloadsGuardLogEntry,
  type DownloadsGuardResult,
} from '@/services/settingsApi';

const TRACKERS_PATH = 'automation.downloads_guard.trackers';
export const SIGN_IN_TO_CLEAN = 'Sign in to clean up';

const ALL_PATHS = [
  'automation.downloads_guard.enabled',
  'automation.downloads_guard.min_free_gb',
  'automation.downloads_guard.reclaim_gb',
  'automation.downloads_guard.daily_cap_gb',
  'automation.downloads_guard.check_interval_minutes',
  TRACKERS_PATH,
];

const OUTCOME_STYLES = {
  freed: 'text-emerald-300/80',
  partial: 'text-amber-300/80',
  preview: 'text-sky-300/70',
  error: 'text-rose-300/80',
  nothing: 'text-white/40',
} as const;

function ageDays(mtime: number): string {
  const days = (Date.now() / 1000 - mtime) / 86400;
  if (days < 1) return 'today';
  return `${Math.round(days)}d old`;
}

/**
 * Tracker preference, stored as backend tracker keys.
 *
 * Purpose-built rather than the shared ChipPicker for two reasons: the size
 * behind each tracker has to be on the chip (it is what says whether the
 * preference has any runway), and the empty case means something different here
 * — ChipPicker's convention is "empty = all of them", while an empty preference
 * here means "no preference, straight oldest-first across everything".
 */
function TrackerPicker({ summary }: { summary: DownloadsGuardTrackerSummary | null }) {
  const { getByPath, setByPath } = useSettings();
  const raw = getByPath(TRACKERS_PATH);
  // Undefined means untouched, and the backend default is IPTorrents — so the
  // picker must show that, not an empty selection it would then write back.
  const selected: string[] = Array.isArray(raw) ? (raw as string[]).map(String) : ['ipt'];

  const toggle = (key: string) =>
    setByPath(
      TRACKERS_PATH,
      selected.includes(key) ? selected.filter((k) => k !== key) : [...selected, key],
    );

  const present = (summary?.trackers ?? []).filter((t) => t.count > 0);
  // A selected tracker with nothing left in qBittorrent still has to render, or
  // the setting would silently disappear from the UI it is configured in.
  const orphaned = selected.filter((k) => !present.some((t) => t.key === k));

  const chips: DownloadsGuardTracker[] = [
    ...present,
    ...orphaned.map((key) => ({ key, label: key, count: 0, size_bytes: 0 })),
  ];

  return (
    <div className="col-span-full flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2">
        <label className="text-[14px] font-medium text-white/60">Delete from these trackers first</label>
        {selected.length > 0 && (
          <button type="button" onClick={() => setByPath(TRACKERS_PATH, [])}
            className="text-[11px] font-semibold text-white/40 hover:text-white/70">
            Clear
          </button>
        )}
      </div>

      {summary && !summary.available && (
        <p className="text-[12px] text-amber-300/70">
          qBittorrent unreachable
        </p>
      )}

      <div className="flex flex-wrap gap-1.5">
        {chips.map((t) => {
          const on = selected.includes(t.key);
          return (
            <button key={t.key} type="button" onClick={() => toggle(t.key)}
              className={`rounded-lg border px-2.5 py-1.5 text-left text-[12px] transition ${
                on
                  ? 'border-emerald-400/30 bg-emerald-400/12 text-emerald-200'
                  : 'border-white/10 bg-white/5 text-white/50 hover:text-white/80'
              }`}>
              <span className="font-semibold">{t.label}</span>
              <span className={`ml-1.5 ${on ? 'text-emerald-200/60' : 'text-white/30'}`}>
                {t.count > 0 ? `${t.count} · ${formatBytes(t.size_bytes)}` : 'nothing here'}
              </span>
            </button>
          );
        })}
      </div>


      {summary && summary.unclassified_count > 0 && (
        <p className="text-[11px] text-white/30">
          {summary.unclassified_count} torrents ({formatBytes(summary.unclassified_bytes)}) couldn’t
          be matched to a tracker
          {summary.unknown_hosts.length > 0 &&
            `: ${summary.unknown_hosts.slice(0, 3).map((h) => h.host).join(', ')}`}
        </p>
      )}
    </div>
  );
}

/** Free space against the floor, plus what the watcher last concluded. */
function StatusPanel({ status }: { status: DownloadsGuardStatus | null }) {
  const ev = status?.evaluation;
  if (!ev) return null;

  const pct = ev.total_bytes > 0 ? (ev.free_bytes / ev.total_bytes) * 100 : 0;
  const floorPct = ev.total_bytes > 0 ? (ev.min_free_bytes / ev.total_bytes) * 100 : 0;
  // The trigger acts on projected, not actual — qBittorrent doesn't preallocate,
  // so a 90GB grab eats the disk gradually and actual free space lags intent.
  const projPct = ev.total_bytes > 0 ? (ev.projected_free_bytes / ev.total_bytes) * 100 : 0;
  const breached = ev.projected_free_bytes < ev.min_free_bytes;
  const pending = ev.pending_bytes ?? 0;

  return (
    <div className="col-span-full rounded-lg border border-white/8 bg-black/25 p-3">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <p className="text-[13px] text-white/70">
          <span className={breached ? 'font-semibold text-amber-300' : 'font-semibold text-white/90'}>
            {formatBytes(ev.free_bytes)}
          </span>{' '}
          free of {formatBytes(ev.total_bytes)}
          <span className="ml-2 text-[11px] text-white/35">{ev.path}</span>
        </p>
        <p className="shrink-0 text-[11px] text-white/35">
          Floor {formatBytes(ev.min_free_bytes)}
        </p>
      </div>

      <div className="mb-2">
        {pending > 0 && (
          <p className="text-[12px] text-amber-200/70">
            {formatBytes(pending)} still downloading &rarr;{' '}
            <span className="font-semibold">{formatBytes(ev.projected_free_bytes)}</span> free
            when it lands
          </p>
        )}
      </div>

      {/* Free-space bar with the floor marked, so "below the line" is literal. */}
      <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-white/8">
        <div className={`absolute top-0 h-full rounded-full ${breached ? 'bg-amber-400/70' : 'bg-emerald-400/60'}`}
          style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
        {/* The slice the in-flight downloads will consume — hatched off the
            right of the free bar, so "heading below the floor" is visible. */}
        {pending > 0 && (
          <div className="absolute top-0 h-full bg-amber-300/25"
            style={{ left: `${Math.min(100, Math.max(0, projPct))}%`,
                     width: `${Math.min(100 - projPct, Math.max(0, pct - projPct))}%` }} />
        )}
        <div className="absolute top-0 h-full w-px bg-white/60"
          style={{ left: `${Math.min(100, Math.max(0, floorPct))}%` }} />
      </div>

      <p className={`mt-2 text-[12px] ${ev.triggered ? 'text-amber-300/80' : 'text-white/45'}`}>
        {ev.enabled ? ev.reason : 'Disabled — nothing is deleted until this is switched on'}
      </p>

      {status?.last_check && (
        <p className="mt-0.5 text-[11px] text-white/25">
          Last checked {new Date(status.last_check).toLocaleString()}
          {status.server_tz ? ` ${status.server_tz}` : ''}
        </p>
      )}
      {ev.daily_cap_bytes > 0 && ev.freed_today_bytes > 0 && (
        <p className="mt-0.5 text-[11px] text-white/25">
          Deleted today: {formatBytes(ev.freed_today_bytes)} of {formatBytes(ev.daily_cap_bytes)} allowed
        </p>
      )}
    </div>
  );
}

/** The entries a run would delete, or did. */
function EntryList({
  title,
  entries,
  note,
}: {
  title: string;
  entries: { name: string; size_bytes: number; mtime?: number; tracker_label?: string | null; phase?: string }[];
  note?: string;
}) {
  if (!entries.length) return null;
  return (
    <div className="col-span-full rounded-lg border border-white/8 bg-black/25 p-3">
      <p className="mb-2 text-[11px] text-white/35">{title}</p>
      {note && <p className="mb-2 text-[12px] text-white/45">{note}</p>}
      <div className="flex flex-col gap-1">
        {entries.slice(0, 40).map((e) => (
          <div key={e.name} className="flex items-baseline justify-between gap-3 text-[12px]">
            <span className="truncate text-white/70">{e.name}</span>
            <span className="shrink-0 text-[11px] text-white/35">
              {/* The tracker is on every row on purpose: it is the one field
                  that says whether the preference is still holding. */}
              <span className={e.phase === 'fallback' ? 'text-amber-300/60' : 'text-white/40'}>
                {e.tracker_label || 'unknown'}
              </span>
              {' · '}
              {formatBytes(e.size_bytes)}
              {e.mtime ? ` · ${ageDays(e.mtime)}` : ''}
            </span>
          </div>
        ))}
        {entries.length > 40 && (
          <p className="text-[11px] text-white/25">…and {entries.length - 40} more</p>
        )}
      </div>
    </div>
  );
}

/** Mirrors downloads_guard.summarise() so the card reads like the log. */
export function describe(r: DownloadsGuardResult): string {
  if (r.error) return r.error;
  if (!r.triggered) return r.reason || 'nothing to do';
  if (!r.freed_bytes) return `Nothing deleted — ${r.reason}`;
  const verb = r.dry_run ? 'Would free' : 'Freed';
  const parts = [`${verb} ${formatBytes(r.freed_bytes)} across ${r.deleted.length} entries`];
  if (r.freed_fallback_bytes) {
    parts.push(r.freed_preferred_bytes
      ? `${formatBytes(r.freed_preferred_bytes)} from chosen trackers + ${formatBytes(r.freed_fallback_bytes)} other`
      : `${formatBytes(r.freed_fallback_bytes)} from other trackers`);
  }
  if (r.short) parts.push('short of target — ran out of eligible entries');
  if (r.failed.length) parts.push(`${r.failed.length} failed to delete`);
  if (!r.dry_run) parts.push(`${formatBytes(r.free_after_bytes)} free now`);
  return parts.join(' · ');
}

export function DownloadsGuardCard() {
  const [summary, setSummary] = useState<DownloadsGuardTrackerSummary | null>(null);
  const [status, setStatus] = useState<DownloadsGuardStatus | null>(null);
  const [log, setLog] = useState<DownloadsGuardLogEntry[]>([]);
  const [preview, setPreview] = useState<DownloadsGuardPreview | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const { implicit } = useAuth();
  const [needSignIn, setNeedSignIn] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const signIn = () => navigate('/login', { state: { from: location.pathname + location.search, signIn: true } });
  const showSignIn = Boolean(implicit) || needSignIn;

  const running = Boolean(status?.running);

  const refresh = useCallback(async () => {
    try {
      const s = await fetchDownloadsGuardStatus();
      setStatus(s.status ?? null);
      setLog(Array.isArray(s.log) ? s.log : []);
      return s.status;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    fetchDownloadsGuardTrackers().then(setSummary).catch(() => setSummary(null));
    refresh();
  }, [refresh]);

  const pollUntilIdle = useCallback((baselineCheck: string | null, baselineRun: string | null) => {
    let polls = 0;
    const iv = setInterval(async () => {
      polls++;
      const s = await refresh();
      const checkAdvanced = Boolean(s?.last_check && s.last_check !== baselineCheck);
      const runAdvanced = Boolean(s?.last_run && s.last_run !== baselineRun);
      if ((!s?.running && (checkAdvanced || runAdvanced)) || polls >= 40) {
        clearInterval(iv);
        if (!s?.running && (checkAdvanced || runAdvanced)) setMsg(null);
      }
    }, 2000);
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

  const handlePreview = () => withBusy('preview', async () => {
    const p = await fetchDownloadsGuardPreview();
    setPreview(p);
    if (p.error) return p.error;
    return `Would delete ${p.entries.length} entries totalling ${formatBytes(p.planned_bytes)}${
      p.short ? ' — short of target, the folder ran out of eligible entries' : ''
    }`;
  });

  /** Deletes for real. Two presses, because there is no undo. */
  const handleRunNow = () => withBusy('run', async () => {
    const baselineCheck = status?.last_check ?? null;
    const baselineRun = status?.last_run ?? null;
    const r = await triggerDownloadsGuardRun(false);
    setConfirming(false);
    if (r.signIn) { setNeedSignIn(true); return ''; }
    pollUntilIdle(baselineCheck, baselineRun);
    return 'Cleanup started — the result appears below.';
  });

  const lastEntries = status?.last_result?.deleted ?? [];

  return (
    <SettingsCard
      title="Downloads Guard"
      subtitle="Keeps free space above a floor by deleting the oldest downloads"
      paths={ALL_PATHS}
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <ellipse cx="12" cy="5" rx="9" ry="3" />
          <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
          <path d="M3 12c0 1.66 4 3 9 3s9-1.34 9-3" />
        </svg>
      }
      headerRight={
        <button type="button" onClick={handlePreview} disabled={busy !== null || running}
          className="rounded-lg border border-white/10 bg-white/8 px-3 py-2 text-[12px] font-semibold text-white/80 disabled:opacity-40">
          {busy === 'preview' ? 'Checking…' : 'Preview'}
        </button>
      }
    >
      <ToggleSwitch label="Enable Downloads Guard" path="automation.downloads_guard.enabled" />
      <FormField label="Check every (minutes)" path="automation.downloads_guard.check_interval_minutes"
        type="number" min={1} max={1440} />

      <FormField label="Free-space floor (GB)" path="automation.downloads_guard.min_free_gb"
        type="number" min={1} max={10000} />
      <FormField label="Reclaim per run (GB)" path="automation.downloads_guard.reclaim_gb"
        type="number" min={1} max={2000} />

      <FormField label="Daily limit (GB)" path="automation.downloads_guard.daily_cap_gb"
        type="number" min={0} max={20000} />

      <TrackerPicker summary={summary} />

      <StatusPanel status={status} />

      {preview && !preview.error && (
        <EntryList
          title={`Would delete now · ${formatBytes(preview.planned_bytes)} of a ${formatBytes(
            preview.target_bytes,
          )} target`}
          note={[
            `${preview.preferred_available} eligible in your chosen trackers, ${preview.eligible_count} in the folder overall.`,
            preview.fallback_bytes
              ? `Chosen trackers covered ${formatBytes(preview.preferred_bytes)}; the remaining ${formatBytes(preview.fallback_bytes)} came from other trackers.`
              : '',
            preview.short ? 'Short of target — not enough eligible entries.' : '',
          ].filter(Boolean).join(' ')}
          entries={preview.entries}
        />
      )}
      {preview?.error && (
        <p className="col-span-full text-[12px] text-amber-300/70">{preview.error}</p>
      )}

      {msg && <p className="col-span-full text-[12px] text-white/50">{msg}</p>}

      {/* Last run — persisted server-side so it survives a restart. */}
      <div className="col-span-full rounded-lg border border-white/8 bg-black/20 px-3 py-2">
        <p className="text-[11px] text-white/35">
          Last acted: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'never'}
        </p>
        {status?.last_result && (
          <p className={`mt-1 text-[12px] ${
            status.last_result.error
              ? OUTCOME_STYLES.error
              : status.last_result.dry_run
                ? OUTCOME_STYLES.preview
                : status.last_result.freed_bytes
                  ? OUTCOME_STYLES.freed
                  : OUTCOME_STYLES.nothing
          }`}>
            {describe(status.last_result)}
          </p>
        )}
        {status?.last_result?.failed?.length ? (
          <p className="mt-0.5 text-[11px] text-amber-300/50">
            Could not delete: {status.last_result.failed.slice(0, 5).join(', ')}
          </p>
        ) : null}
      </div>

      <EntryList
        title={status?.last_result?.dry_run ? 'Last preview would have deleted' : 'Deleted in the last run'}
        entries={lastEntries}
      />

      {log.length > 0 && (
        <div className="col-span-full flex flex-col gap-1">
          <p className="text-[11px] text-white/35">Recent activity</p>
          {log.slice(0, 8).map((e, i) => (
            <div key={`${e.at}-${i}`} className="flex items-baseline justify-between gap-3 text-[12px]">
              <span className={`truncate ${OUTCOME_STYLES[e.outcome] || 'text-white/50'}`}>
                {e.summary}
              </span>
              <span className="shrink-0 text-[11px] text-white/25">
                {new Date(e.at).toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="col-span-full flex flex-wrap items-center gap-2">
        {showSignIn ? (
          <button type="button" onClick={signIn} data-testid="guard-sign-in"
            className="rounded-lg border border-white/10 bg-white/8 px-3 py-2 text-[12px] font-semibold text-white/90">
            {SIGN_IN_TO_CLEAN}
          </button>
        ) : confirming ? (
          <>
            <button type="button" onClick={handleRunNow} disabled={busy !== null || running}
              className="rounded-lg border border-rose-500/30 bg-rose-500/15 px-3 py-2 text-[12px] font-semibold text-rose-200 disabled:opacity-40">
              {running ? 'Deleting…' : 'Yes — delete permanently'}
            </button>
            <button type="button" onClick={() => setConfirming(false)}
              className="rounded-lg border border-white/10 bg-white/8 px-3 py-2 text-[12px] font-semibold text-white/70">
              Cancel
            </button>
            <span className="text-[11px] text-rose-200/60">
              This cannot be undone.
            </span>
          </>
        ) : (
          <>
            <button type="button" onClick={() => setConfirming(true)} disabled={busy !== null || running}
              className="rounded-lg border border-amber-500/25 bg-amber-500/12 px-3 py-2 text-[12px] font-semibold text-amber-300 disabled:opacity-40">
              {running ? 'Running…' : 'Clean up now'}
            </button>
          </>
        )}
      </div>
    </SettingsCard>
  );
}
