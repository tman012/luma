/**
 * Sonarr status + monitor controls for the TV modal (owner only).
 *
 * One fetch per show (/api/sonarr/series-status) feeds show, season and
 * episode indicators through context. Cached ~60s; while anything is in
 * Sonarr's queue (or right after a grab/add) it re-polls every ~12s.
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { ShowMonitorSheet } from './ShowMonitorSheet';

export type SonarrEpState =
  | 'downloaded' | 'downloading' | 'queued' | 'importing' | 'blocked'
  | 'missing' | 'unmonitored' | 'unaired';

export interface SonarrEpStatus {
  id?: number;
  s: number;
  e: number;
  state: SonarrEpState;
  monitored: boolean;
  quality?: string | null;
  size?: number | null;
  progress?: number;
  eta?: string;
  detail?: string;
  onDisk?: boolean;
}

export interface SonarrSeasonStatusData {
  season: number;
  monitored: boolean;
  downloaded: number;
  total: number;
  downloading: number;
  queued: number;
  importing: number;
  blocked?: number;
}

export interface SonarrSeriesStatusData {
  enabled: boolean;
  exists: boolean;
  series?: {
    id: number;
    title: string;
    monitored: boolean;
    status: string;
    downloaded: number;
    total: number;
    sizeOnDisk?: number;
    downloading: number;
    queued: number;
    importing: number;
    blocked?: number;
  };
  seasons?: SonarrSeasonStatusData[];
  episodes?: SonarrEpStatus[];
  active?: boolean;
}

const TTL = 60_000;
const ACTIVE_POLL = 12_000;
const cache = new Map<string, { ts: number; data: SonarrSeriesStatusData }>();

/** Small transient pill (no React tree needed; survives re-renders). */
export function showSonarrToast(text: string, ms = 2200) {
  if (typeof document === 'undefined') return;
  document.querySelectorAll('[data-sonarr-toast]').forEach((n) => n.remove());
  const el = document.createElement('div');
  el.setAttribute('data-sonarr-toast', '');
  el.textContent = text;
  Object.assign(el.style, {
    position: 'fixed', left: '50%', bottom: 'calc(env(safe-area-inset-bottom, 0px) + 32px)',
    transform: 'translate(-50%, 8px)', zIndex: '100000', pointerEvents: 'none',
    padding: '10px 18px', borderRadius: '999px', whiteSpace: 'nowrap',
    background: 'rgba(28,32,48,0.92)', color: '#fff', fontSize: '14px', fontWeight: '600',
    border: '1px solid rgba(255,255,255,0.12)', boxShadow: '0 8px 24px rgba(0,0,0,0.45)',
    backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)',
    opacity: '0', transition: 'opacity 180ms ease, transform 180ms ease',
    fontFamily: 'inherit',
  } as Partial<CSSStyleDeclaration>);
  document.body.appendChild(el);
  requestAnimationFrame(() => { el.style.opacity = '1'; el.style.transform = 'translate(-50%, 0)'; });
  window.setTimeout(() => {
    el.style.opacity = '0';
    window.setTimeout(() => el.remove(), 220);
  }, ms);
}

const SEARCH_BOOST = 150_000;

/** Sonarr's "downloading" purple, lifted for dark backgrounds. */
export const SONARR_PURPLE = '#a78bfa';

/**
 * Fetch + poll Sonarr status for a show. `refresh(boostMs)` forces a reload
 * and keeps polling for boostMs even if nothing is queued yet (used right
 * after a grab or add, before Sonarr's queue shows the download).
 */
export function useSonarrSeriesStatus(
  tvdbId: number | null | undefined,
  tmdbId: number | null | undefined,
  enabled: boolean,
) {
  const [data, setData] = useState<SonarrSeriesStatusData | null>(null);
  const [nonce, setNonce] = useState(0);
  const boostUntil = useRef(0);
  const key = `${tvdbId ?? ''}:${tmdbId ?? ''}`;

  useEffect(() => {
    if (!enabled || (!tvdbId && !tmdbId)) {
      setData(null);
      return;
    }
    let cancelled = false;
    let timer: number | undefined;

    const schedule = (d: SonarrSeriesStatusData | null) => {
      if (cancelled) return;
      const boosting = Date.now() < boostUntil.current;
      if (!(d?.active || boosting)) return;
      timer = window.setTimeout(() => {
        if (document.visibilityState === 'visible') void run(true);
        else schedule(d);
      }, ACTIVE_POLL);
    };

    const run = async (force: boolean) => {
      const hit = cache.get(key);
      if (!force && hit && Date.now() - hit.ts < TTL) {
        setData(hit.data);
        schedule(hit.data);
        return;
      }
      try {
        const p = new URLSearchParams();
        if (tvdbId) p.set('tvdb_id', String(tvdbId));
        if (tmdbId) p.set('tmdb_id', String(tmdbId));
        const resp = await fetch(`/api/sonarr/series-status?${p.toString()}`);
        if (!resp.ok) throw new Error(String(resp.status));
        const d = (await resp.json()) as SonarrSeriesStatusData;
        if (cancelled) return;
        cache.set(key, { ts: Date.now(), data: d });
        setData(d);
        schedule(d);
      } catch {
        schedule(cache.get(key)?.data ?? null);
      }
    };

    void run(nonce > 0);
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [key, enabled, nonce, tvdbId, tmdbId]);

  const refresh = useCallback((boostMs = 0) => {
    if (boostMs > 0) boostUntil.current = Date.now() + boostMs;
    cache.delete(key);
    setNonce((n) => n + 1);
  }, [key]);

  const patch = useCallback((fn: (d: SonarrSeriesStatusData) => SonarrSeriesStatusData) => {
    setData((prev) => {
      if (!prev) return prev;
      const next = fn(prev);
      cache.set(key, { ts: Date.now(), data: next });
      return next;
    });
  }, [key]);

  /** POST a monitor change; on ON, Sonarr searches — poll fast and say so. */
  const postMonitor = useCallback(async (url: string, body: Record<string, unknown>, monitored: boolean) => {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(String(resp.status));
    const j = (await resp.json().catch(() => ({}))) as { searching?: boolean };
    if (monitored) {
      showSonarrToast(j.searching === false ? 'Monitoring' : 'Monitoring \u00b7 Searching');
      refresh(j.searching === false ? 0 : SEARCH_BOOST);
    }
    return j;
  }, [refresh]);

  /** Owner-only: monitor/unmonitor one episode (optimistic, reverts on failure). */
  const setEpisodeMonitored = useCallback(async (s: number, e: number, monitored: boolean): Promise<boolean> => {
    const ep = data?.episodes?.find((x) => x.s === s && x.e === e);
    const seriesId = data?.series?.id;
    if (!ep?.id || !seriesId) return false;
    const apply = (m: boolean) => patch((d) => ({
      ...d,
      series: d.series && m ? { ...d.series, monitored: true } : d.series,
      episodes: d.episodes?.map((x) => (x.s === s && x.e === e ? { ...x, monitored: m } : x)),
    }));
    apply(monitored);
    try {
      await postMonitor('/api/sonarr/monitor/episodes', { series_id: seriesId, episode_ids: [ep.id], monitored }, monitored);
      return true;
    } catch {
      apply(ep.monitored);
      showSonarrToast("Couldn't update Sonarr");
      return false;
    }
  }, [data, patch, postMonitor]);

  /** Owner-only: monitor/unmonitor a season (and its episodes). */
  const setSeasonMonitored = useCallback(async (n: number, monitored: boolean): Promise<boolean> => {
    const seriesId = data?.series?.id;
    if (!seriesId || !data) return false;
    const before = data;
    patch((d) => ({
      ...d,
      series: d.series && monitored ? { ...d.series, monitored: true } : d.series,
      seasons: d.seasons?.map((x) => (x.season === n ? { ...x, monitored } : x)),
      episodes: d.episodes?.map((x) => (x.s === n ? { ...x, monitored } : x)),
    }));
    try {
      await postMonitor('/api/sonarr/monitor/season', { series_id: seriesId, season: n, monitored }, monitored);
      return true;
    } catch {
      patch(() => before);
      showSonarrToast("Couldn't update Sonarr");
      return false;
    }
  }, [data, patch, postMonitor]);

  /** Owner-only: monitor/unmonitor the series itself. */
  const setSeriesMonitored = useCallback(async (monitored: boolean, allSeasons = false): Promise<boolean> => {
    const seriesId = data?.series?.id;
    if (!seriesId || !data) return false;
    const before = data;
    const all = monitored && allSeasons;
    patch((d) => ({
      ...d,
      series: d.series ? { ...d.series, monitored } : d.series,
      seasons: all ? d.seasons?.map((x) => (x.season > 0 ? { ...x, monitored: true } : x)) : d.seasons,
      episodes: all ? d.episodes?.map((x) => (x.s > 0 ? { ...x, monitored: true } : x)) : d.episodes,
    }));
    try {
      await postMonitor('/api/sonarr/monitor/series', { series_id: seriesId, monitored, ...(all ? { all_seasons: true } : {}) }, monitored);
      return true;
    } catch {
      patch(() => before);
      showSonarrToast("Couldn't update Sonarr");
      return false;
    }
  }, [data, patch, postMonitor]);

  /**
   * Owner-only show-level prompt: 'all' (every regular season + SeriesSearch),
   * 'future' (new/unaired only, no search), 'seasons' (exact set, SeasonSearch
   * for newly enabled ones), 'off' (series flag off).
   */
  const setSeriesMonitorMode = useCallback(async (mode: SeriesMonitorMode, seasons?: number[]): Promise<boolean> => {
    const seriesId = data?.series?.id;
    if (!seriesId || !data) return false;
    const before = data;
    const want = new Set(seasons ?? []);
    patch((d) => {
      if (mode === 'off') return { ...d, series: d.series ? { ...d.series, monitored: false } : d.series };
      if (mode === 'all') return {
        ...d,
        series: d.series ? { ...d.series, monitored: true } : d.series,
        seasons: d.seasons?.map((x) => (x.season > 0 ? { ...x, monitored: true } : x)),
        episodes: d.episodes?.map((x) => (x.s > 0 ? { ...x, monitored: true } : x)),
      };
      if (mode === 'future') return {
        ...d,
        series: d.series ? { ...d.series, monitored: true } : d.series,
        episodes: d.episodes?.map((x) => (x.s > 0 && x.state === 'unaired' ? { ...x, monitored: true } : x)),
      };
      return {
        ...d,
        series: d.series && want.size ? { ...d.series, monitored: true } : d.series,
        seasons: d.seasons?.map((x) => ({ ...x, monitored: want.has(x.season) })),
        episodes: d.episodes?.map((x) => {
          const was = d.seasons?.find((z) => z.season === x.s)?.monitored;
          if (want.has(x.s)) return { ...x, monitored: true };
          return was ? { ...x, monitored: false } : x;
        }),
      };
    });
    try {
      const body: Record<string, unknown> = { series_id: seriesId, mode };
      if (mode === 'seasons') body.seasons = [...want].sort((a, b) => a - b);
      const on = mode !== 'off' && (mode !== 'seasons' || want.size > 0);
      const j = await postMonitor('/api/sonarr/monitor/series', body, on);
      if (!on) refresh(0);
      void j;
      return true;
    } catch {
      patch(() => before);
      showSonarrToast("Couldn't update Sonarr");
      return false;
    }
  }, [data, patch, postMonitor, refresh]);

  return { data, refresh, setEpisodeMonitored, setSeasonMonitored, setSeriesMonitored, setSeriesMonitorMode };
}

export type SeriesMonitorMode = 'all' | 'future' | 'seasons' | 'off';

export interface SeasonInfo { season: number; name?: string | null; have: number; total: number }

export interface SonarrStatusContextValue {
  data: SonarrSeriesStatusData | null;
  setEpisodeMonitored?: (s: number, e: number, monitored: boolean) => Promise<boolean>;
  setSeasonMonitored?: (n: number, monitored: boolean) => Promise<boolean>;
  setSeriesMonitored?: (monitored: boolean, allSeasons?: boolean) => Promise<boolean>;
  setSeriesMonitorMode?: (mode: SeriesMonitorMode, seasons?: number[]) => Promise<boolean>;
  /** For the show-level prompt. */
  showTitle?: string;
  seasonInfo?: SeasonInfo[];
  /** Owner session — badges render only for the owner. */
  owner?: boolean;
}

export const SonarrStatusContext = createContext<SonarrStatusContextValue>({ data: null });

export function useSonarrStatusData() {
  return useContext(SonarrStatusContext).data;
}

// ---------------------------------------------------------------------------
// Pure lookups (safe inside .map())
// ---------------------------------------------------------------------------

export function isInFlight(state?: SonarrEpState | null): boolean {
  return state === 'downloading' || state === 'queued' || state === 'importing';
}

export function sonarrEpisode(st: SonarrSeriesStatusData | null, s: number, e: number): SonarrEpStatus | null {
  if (!st?.exists) return null;
  return st.episodes?.find((x) => x.s === s && x.e === e) ?? null;
}

export function sonarrSeason(st: SonarrSeriesStatusData | null, n: number): SonarrSeasonStatusData | null {
  if (!st?.exists) return null;
  return st.seasons?.find((x) => x.season === n) ?? null;
}

export function seasonInFlight(x: SonarrSeasonStatusData | null): number {
  return x ? x.downloading + x.queued + x.importing : 0;
}

/** Orb color: purple while Sonarr is downloading it, otherwise the caller's. */
export function orbWithSonarr(base: string, inFlight: boolean): string {
  return inFlight ? SONARR_PURPLE : base;
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

function fmtEta(iso?: string): string | null {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  if (!isFinite(ms)) return null;
  if (ms <= 60_000) return '<1m left';
  const mins = Math.round(ms / 60_000);
  if (mins < 60) return `${mins}m left`;
  const h = Math.floor(mins / 60);
  if (h >= 48) return null;
  return `${h}h ${mins % 60}m left`;
}

function activityText(x: { downloading: number; queued: number; importing: number }): string | null {
  const parts: string[] = [];
  if (x.downloading) parts.push(`${x.downloading} downloading`);
  if (x.importing) parts.push(`${x.importing} importing`);
  if (x.queued) parts.push(`${x.queued} queued`);
  return parts.length ? parts.join(', ') : null;
}

function episodeActivityText(ep: SonarrEpStatus, short = false): string {
  if (ep.state === 'downloading') {
    return [short ? null : 'Downloading', ep.progress != null ? `${Math.round(ep.progress * 100)}%` : null, fmtEta(ep.eta)]
      .filter(Boolean).join(' \u00b7 ') || 'Downloading';
  }
  if (ep.state === 'importing') return 'Importing';
  return ep.detail === 'paused' ? 'Paused' : 'Queued';
}

const MUTED = 'rgba(255,255,255,0.45)';

function ProgressBar({ value, width }: { value: number; width: number | string }) {
  return (
    <div style={{ width, height: 3, borderRadius: 3, overflow: 'hidden', background: 'rgba(255,255,255,0.12)' }}>
      <div style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%`, height: '100%', background: SONARR_PURPLE }} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pieces — only for missing content or active downloads
// ---------------------------------------------------------------------------

function Bookmark({ filled, half = false, size }: { filled: boolean; half?: boolean; size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={filled && !half ? 'currentColor' : 'none'}
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
      {half && <path d="M12 3H7a2 2 0 0 0-2 2v16l7-5z" fill="currentColor" stroke="none" />}
      <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
    </svg>
  );
}

/** Tappable Monitored / Unmonitored control (owner only — status is owner only). */
function MonitorToggle({
  monitored, onToggle, fontSize, partial = false, label,
}: {
  monitored: boolean; onToggle?: (next: boolean) => Promise<boolean>; fontSize: number;
  partial?: boolean; label?: string;
}) {
  const [busy, setBusy] = useState(false);
  const content = (
    <>
      <Bookmark filled={monitored} half={partial} size={fontSize} />
      <span>{label ?? (monitored ? 'Monitored' : 'Unmonitored')}</span>
    </>
  );
  const style: React.CSSProperties = {
    display: 'inline-flex', alignItems: 'center', gap: 5, fontSize, fontWeight: 500,
    color: monitored ? 'rgba(255,255,255,0.8)' : MUTED,
    background: 'transparent', border: 'none', padding: 0, margin: 0,
    fontFamily: 'inherit', lineHeight: 1.2,
    opacity: busy ? 0.5 : 1,
  };
  if (!onToggle) return <span style={style}>{content}</span>;
  return (
    <button
      type="button"
      data-sonarr-monitor={partial ? 'partial' : monitored ? 'on' : 'off'}
      disabled={busy}
      onClick={async (e) => {
        e.stopPropagation();
        setBusy(true);
        try { await onToggle(!monitored); } finally { setBusy(false); }
      }}
      onTouchEnd={(e) => e.stopPropagation()}
      style={{ ...style, fontSize, cursor: 'pointer', WebkitTapHighlightColor: 'transparent' }}
    >
      {content}
    </button>
  );
}

/** Show level: Monitored / Unmonitored (+ "2 downloading"), or "Not in Sonarr". */
export function SonarrShowStatus({ fontSize = 13, marginTop = 10 }: { fontSize?: number; marginTop?: number }) {
  const ctx = useContext(SonarrStatusContext);
  const [sheetOpen, setSheetOpen] = useState(false);
  const st = ctx.data;
  if (!st || !st.enabled) return null;
  const base: React.CSSProperties = {
    display: 'flex', alignItems: 'center', justifyContent: 'center', flexWrap: 'wrap',
    gap: 8, marginTop, fontSize, fontWeight: 500, color: MUTED,
  };
  if (!st.exists || !st.series) {
    return <div data-sonarr-status="show" style={{ ...base, color: 'rgba(255,255,255,0.35)' }}>Not in Sonarr</div>;
  }
  const s = st.series;
  const activity = activityText(s);
  // Monitored seasons, Specials excluded. Series flag off = nothing monitored.
  const regular = (st.seasons ?? []).filter((x) => x.season > 0);
  const on = s.monitored ? regular.filter((x) => x.monitored).length : 0;
  const total = regular.length;
  const all = s.monitored && (total === 0 || on === total);
  const partial = s.monitored && total > 0 && on > 0 && on < total;
  const label = all ? 'Monitored' : partial ? `Monitored ${on}/${total}` : 'Unmonitored';
  const canEdit = !!(ctx.owner && ctx.setSeriesMonitorMode);
  return (
    <div data-sonarr-status="show" data-monitored-seasons={`${on}/${total}`} style={base}>
      <MonitorToggle
        monitored={all || partial}
        partial={partial}
        label={label}
        fontSize={fontSize}
        onToggle={canEdit ? async () => { setSheetOpen(true); return true; } : undefined}
      />
      {sheetOpen && <ShowMonitorSheet onClose={() => setSheetOpen(false)} />}
      {activity && (
        <>
          <span style={{ color: 'rgba(255,255,255,0.3)' }}>{'\u00b7'}</span>
          <span style={{ color: SONARR_PURPLE, fontWeight: 600 }}>{activity}</span>
        </>
      )}
    </div>
  );
}

/** Season row suffix: purple " · 2 downloading" while Sonarr is busy. Text only. */
export function SonarrSeasonNote({ season, lead = true }: { season: number; lead?: boolean }) {
  const x = sonarrSeason(useSonarrStatusData(), season);
  if (!x) return null;
  const activity = activityText(x);
  if (!activity) return null;
  return (
    <span data-sonarr-note="season">
      {lead ? ' \u00b7 ' : null}
      <span style={{ color: SONARR_PURPLE, fontWeight: 600 }}>{activity}</span>
    </span>
  );
}

/** Season detail: "7/8 episodes · [Monitored]" with an owner toggle. */
export function SonarrSeasonLine({
  season, have, total, fontSize = 13,
}: { season: number; have: number; total: number; fontSize?: number }) {
  const ctx = useContext(SonarrStatusContext);
  const x = sonarrSeason(ctx.data, season);
  if (!x) return null;
  const activity = activityText(x);
  const sepEl = <span style={{ color: 'rgba(255,255,255,0.3)' }}>{'\u00b7'}</span>;
  return (
    <div data-sonarr-status="season" style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center', flexWrap: 'wrap',
      gap: 8, fontSize, fontWeight: 500, color: MUTED,
    }}>
      {total > 0 && <span>{have}/{total} episodes</span>}
      {total > 0 && sepEl}
      <MonitorToggle
        monitored={x.monitored}
        fontSize={fontSize}
        onToggle={ctx.setSeasonMonitored ? (m) => ctx.setSeasonMonitored!(season, m) : undefined}
      />
      {activity && (
        <>
          {sepEl}
          <span style={{ color: SONARR_PURPLE, fontWeight: 600 }}>{activity}</span>
        </>
      )}
    </div>
  );
}

/**
 * Episode: purple progress while Sonarr has it in the queue. The header
 * variant also carries the episode's bookmark badge.
 */
export function SonarrEpisodeNote({
  season, episode, variant = 'row', fontSize,
}: { season: number; episode: number; variant?: 'row' | 'header'; fontSize?: number }) {
  const ctx = useContext(SonarrStatusContext);
  const ep = sonarrEpisode(ctx.data, season, episode);
  if (!ep) return null;
  const fs = fontSize ?? (variant === 'header' ? 14 : 12);
  const header = variant === 'header';
  const inFlight = isInFlight(ep.state);
  if (!header && !inFlight) return null;
  return (
    <div data-sonarr-status="episode" data-state={ep.state} style={{
      display: 'flex', flexDirection: 'column', gap: 6,
      alignItems: header ? 'center' : 'flex-start', marginTop: header ? 12 : 0,
    }}>
      {inFlight && (
        <>
          <span style={{ fontSize: fs, fontWeight: 600, color: SONARR_PURPLE, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '100%' }}>
            {episodeActivityText(ep, !header)}
          </span>
          {ep.progress != null && <ProgressBar value={ep.progress} width={header ? 160 : '100%'} />}
        </>
      )}
      {header && ctx.owner && (
        <MonitorBadge
          monitored={ep.monitored}
          inFlight={inFlight}
          progress={ep.progress}
          size={40}
          onToggle={ctx.setEpisodeMonitored ? (m) => ctx.setEpisodeMonitored!(season, episode, m) : undefined}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Round badges (owner only): bookmark = monitor, arrow = Sources
// ---------------------------------------------------------------------------

/** Keep badge taps from reaching the row/card underneath. */
const stop = (e: React.SyntheticEvent) => e.stopPropagation();
const guard = {
  onTouchStart: stop, onTouchEnd: stop, onPointerDown: stop, onPointerUp: stop, onMouseDown: stop,
};

/** On artwork: darker glass so the badge reads over any poster. */
const OVERLAY: React.CSSProperties = {
  background: 'rgba(10,14,28,0.62)', border: '1px solid rgba(255,255,255,0.18)',
  backdropFilter: 'blur(10px)', WebkitBackdropFilter: 'blur(10px)',
};

function badgeStyle(size: number, extra?: React.CSSProperties): React.CSSProperties {
  return {
    position: 'relative', flexShrink: 0, width: size, height: size, borderRadius: '50%',
    display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 0, margin: 0,
    background: 'rgba(255,255,255,0.12)', border: '1px solid rgba(255,255,255,0.14)',
    color: '#fff', cursor: 'pointer', WebkitTapHighlightColor: 'transparent',
    ...extra,
  };
}

function ProgressRing({ size, value }: { size: number; value?: number }) {
  const r = size / 2 - 1.5;
  const c = 2 * Math.PI * r;
  const v = value != null ? Math.max(0.03, Math.min(1, value)) : 0.25;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}
      style={{ position: 'absolute', inset: -1, transform: 'rotate(-90deg)', pointerEvents: 'none',
        animation: value == null ? 'sonarrRingSpin 1.1s linear infinite' : undefined }}>
      <style>{'@keyframes sonarrRingSpin{from{transform:rotate(0)}to{transform:rotate(360deg)}}'}</style>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(167,139,250,0.25)" strokeWidth={2.5} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={SONARR_PURPLE} strokeWidth={2.5}
        strokeLinecap="round" strokeDasharray={`${c * v} ${c}`} />
    </svg>
  );
}

/** Bookmark badge: outline = unmonitored, filled = monitored, purple ring while downloading. */
export function MonitorBadge({
  monitored, onToggle, inFlight = false, progress, size = 36, overlay = false,
}: {
  monitored: boolean; onToggle?: (next: boolean) => Promise<boolean>;
  inFlight?: boolean; progress?: number; size?: number; overlay?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const icon = Math.round(size * 0.44);
  return (
    <button
      type="button"
      data-sonarr-badge="monitor"
      data-sonarr-monitor={monitored ? 'on' : 'off'}
      aria-label={monitored ? 'Unmonitor' : 'Monitor'}
      aria-pressed={monitored}
      disabled={busy || !onToggle}
      {...guard}
      onClick={async (e) => {
        e.stopPropagation();
        if (!onToggle || busy) return;
        setBusy(true);
        try { await onToggle(!monitored); } finally { setBusy(false); }
      }}
      style={badgeStyle(size, {
        ...(overlay ? OVERLAY : null),
        color: inFlight ? SONARR_PURPLE : monitored ? '#fff' : 'rgba(255,255,255,0.7)',
        ...(overlay && !inFlight && !monitored ? null : {
          background: inFlight ? 'rgba(167,139,250,0.16)' : monitored ? 'rgba(255,255,255,0.22)' : 'rgba(255,255,255,0.10)',
          border: inFlight ? '1px solid transparent' : monitored ? '1px solid rgba(255,255,255,0.28)' : '1px solid rgba(255,255,255,0.14)',
        }),
        ...(overlay && monitored && !inFlight ? { background: 'rgba(255,255,255,0.28)' } : null),
        opacity: busy ? 0.6 : 1,
        cursor: onToggle ? 'pointer' : 'default',
      })}
    >
      {inFlight && <ProgressRing size={size} value={progress} />}
      <Bookmark filled={monitored} size={icon} />
    </button>
  );
}

/** Sources badge (same download-arrow icon as the Sources buttons). */
export function SourcesBadge({ onOpen, size = 36, overlay = false }: { onOpen: () => void; size?: number; overlay?: boolean }) {
  const icon = Math.round(size * 0.44);
  return (
    <button
      type="button"
      data-sonarr-badge="sources"
      aria-label="Sources"
      {...guard}
      onClick={(e) => { e.stopPropagation(); onOpen(); }}
      style={badgeStyle(size, overlay ? OVERLAY : undefined)}
    >
      <svg width={icon} height={icon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
        <polyline points="7 10 12 15 17 10" />
        <line x1="12" y1="15" x2="12" y2="3" />
      </svg>
    </button>
  );
}

const badgeStack = (direction: 'column' | 'row'): React.CSSProperties => ({
  display: 'flex', flexDirection: direction, alignItems: 'center', gap: 8, flexShrink: 0,
});

/** Episode row: [bookmark] over [sources]. Owner only. */
export function SonarrEpisodeBadges({
  season, episode, onSources, size = 36, direction = 'column', overlay = false,
}: { season: number; episode: number; onSources?: () => void; size?: number; direction?: 'column' | 'row'; overlay?: boolean }) {
  const ctx = useContext(SonarrStatusContext);
  if (!ctx.owner) return null;
  const ep = sonarrEpisode(ctx.data, season, episode);
  if (!ep && !onSources) return null;
  return (
    <div data-sonarr-badges="episode" style={badgeStack(direction)} {...guard} onClick={stop}>
      {ep && (
        <MonitorBadge
          monitored={ep.monitored}
          inFlight={isInFlight(ep.state)}
          progress={ep.progress}
          size={size}
          overlay={overlay}
          onToggle={ctx.setEpisodeMonitored ? (m) => ctx.setEpisodeMonitored!(season, episode, m) : undefined}
        />
      )}
      {onSources && <SourcesBadge onOpen={onSources} size={size} overlay={overlay} />}
    </div>
  );
}

/** Season row: [bookmark] over [sources]. Owner only. */
export function SonarrSeasonBadges({
  season, onSources, size = 36, direction = 'column', overlay = false,
}: { season: number; onSources?: () => void; size?: number; direction?: 'column' | 'row'; overlay?: boolean }) {
  const ctx = useContext(SonarrStatusContext);
  if (!ctx.owner) return null;
  const x = sonarrSeason(ctx.data, season);
  if (!x && !onSources) return null;
  return (
    <div data-sonarr-status="season" data-sonarr-badges="season" style={badgeStack(direction)} {...guard} onClick={stop}>
      {x && (
        <MonitorBadge
          monitored={x.monitored}
          inFlight={seasonInFlight(x) > 0}
          size={size}
          overlay={overlay}
          onToggle={ctx.setSeasonMonitored ? (m) => ctx.setSeasonMonitored!(season, m) : undefined}
        />
      )}
      {onSources && <SourcesBadge onOpen={onSources} size={size} overlay={overlay} />}
    </div>
  );
}
