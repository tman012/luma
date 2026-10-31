/**
 * ActivityFeed — the last 30 completed downloads, badged by origin (decided
 * server-side from real grab evidence — see app/grab_origin.py), with a
 * tracker color bar + legend and click-through to the same detail modal the
 * home rails use.
 *
 * Used both in Settings → Automation (card) and the home Activity shelf.
 * Pass `embedded` in the shelf so it doesn't add its own scroll container
 * (the sheet scrolls).
 */
import { useState, useEffect, useCallback, useRef, type ReactNode } from 'react';
import { getBadgeColor } from '@/components/media/TvDetailModal/tvSourcesUtils';
import { useModalHost } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { posterUrl } from '@/utils/image';
import { ContentReveal, FadeImg } from '@/components/motion/ContentReveal';
import { apiFetch } from '../../apiClient';

/** A single completed download, badged by its origin. */
interface ActivityEntry {
  title: string;
  year: number | string;
  tmdbId: number | null;
  mediaType: 'movie' | 'tv';
  ts: number;           // completion time (ms)
  tracker: string | null;
  badge: string;        // server origin label: Manual | Genre Roulette | Requested | Auto | Upgrade | Other | automation name
  season: number | null;
  episode: number | null;
  episodeTitle: string | null;
  fileName: string | null;   // raw torrent/release file name
  posterPath: string | null; // TMDb poster path — thumb + row wash
}

/** A torrent currently downloading in qBittorrent (any origin, incl. manual). */
interface ActiveDownload {
  title: string;
  year: number | string;
  tmdbId: number | null;
  mediaType: 'movie' | 'tv';
  progress: number;      // 0..1
  eta: string | null;
  status: string;        // 'downloading' | 'stalled' | 'copying' | 'importing'
  tracker: string | null;
  name: string;
  hash: string;          // qBittorrent info-hash (for remove / delete+files)
  sizeTotal: number;
  sizeRemaining: number;
  speed: number;         // bytes/sec
  posterPath: string | null;
}

// ---------------------------------------------------------------------------
// Row artwork
// ---------------------------------------------------------------------------

const THUMB_W = 78;
const THUMB_H = 117;

/**
 * Blurred colour wash bleeding across the row behind the text.
 *
 * Sourced from the POSTER, not a backdrop: at this blur radius you read colour
 * fields rather than composition, so the two are indistinguishable — but the
 * poster is already loaded for the thumb (same URL, browser cache hit), so the
 * wash costs no extra bytes and never gaps out the way backdrop coverage does.
 */

/** DELETE /api/downloads/remove/<hash>?deleteFiles=… — qBit remove / delete+files. */
async function removeDownload(hash: string, deleteFiles: boolean): Promise<{ ok: boolean; error?: string }> {
  if (!hash) return { ok: false, error: 'Missing torrent hash' };
  try {
    const q = deleteFiles ? '?deleteFiles=true' : '?deleteFiles=false';
    const resp = await apiFetch(`/api/downloads/remove/${encodeURIComponent(hash)}${q}`, { method: 'DELETE' });
    const data = await resp.json().catch(() => ({})) as { success?: boolean; error?: string; message?: string };
    if (!resp.ok || data.success === false) {
      return { ok: false, error: data.error || `Remove failed (${resp.status})` };
    }
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'Network error' };
  }
}

function RowWash({ posterPath }: { posterPath: string | null }) {
  const [failed, setFailed] = useState(false);
  const src = posterUrl(posterPath, 'w185');
  if (!src || failed) return null;
  return (
    <div
      aria-hidden
      style={{
        position: 'absolute', inset: 0, pointerEvents: 'none',
        maskImage: 'linear-gradient(to right, black 0%, rgba(0,0,0,0.35) 45%, transparent 80%)',
        WebkitMaskImage: 'linear-gradient(to right, black 0%, rgba(0,0,0,0.35) 45%, transparent 80%)',
      }}
    >
      <FadeImg
        src={src}
        alt=""
        draggable={false}
        onError={() => setFailed(true)}
        opacity={0.38}
        style={{
          width: '100%', height: '100%', objectFit: 'cover',
          // Scale past the edges so the blur doesn't sample transparent pixels
          // and leave a pale halo around the row.
          transform: 'scale(1.6)',
          filter: 'blur(28px) saturate(1.7) brightness(0.62)',
        }}
      />
    </div>
  );
}

/**
 * Poster thumb. When `progress` is supplied (0..1) the poster doubles as the
 * progress indicator: dim greyscale above the fill line, full colour below, so
 * the art "develops" bottom-up and lands fully vivid exactly at 100%.
 */
function RowThumb({ posterPath, title, progress }: {
  posterPath: string | null;
  title: string;
  progress?: number;
}) {
  // A stale TMDb path 404s — fall back to the placeholder rather than leaving a
  // broken <img> in the row.
  const [failed, setFailed] = useState(false);
  const resolved = posterUrl(posterPath, 'w185');
  const src = failed ? null : resolved;
  const developing = progress != null && progress < 1;
  const pct = Math.max(0, Math.min(100, (progress ?? 1) * 100));

  const base: React.CSSProperties = {
    position: 'absolute', inset: 0,
    width: '100%', height: '100%', objectFit: 'cover',
  };

  return (
    <div
      style={{
        position: 'relative', flexShrink: 0,
        width: THUMB_W, height: THUMB_H,
        borderRadius: 9, overflow: 'hidden',
        background: 'rgba(255,255,255,0.06)',
        boxShadow: '0 2px 10px rgba(0,0,0,0.5)',
      }}
    >
      {src ? (
        <>
          {/* Undeveloped layer — only visible above the fill line */}
          {developing && (
            <FadeImg src={src} alt="" aria-hidden draggable={false}
              onError={() => setFailed(true)}
              style={{ ...base, filter: 'grayscale(1) brightness(0.42)' }} />
          )}
          {/* Full-colour layer, clipped to the completed portion */}
          <FadeImg
            src={src}
            alt={title}
            draggable={false}
            onError={() => setFailed(true)}
            style={{
              ...base,
              clipPath: developing ? `inset(${100 - pct}% 0 0 0)` : undefined,
              transition: 'clip-path 600ms cubic-bezier(0.16, 1, 0.3, 1)',
            }}
          />
          {/* Bright seam riding the fill line as it climbs */}
          {developing && pct > 1 && (
            <span
              aria-hidden
              style={{
                position: 'absolute', left: 0, right: 0, bottom: `${pct}%`, height: 1,
                background: 'rgba(255,255,255,0.75)',
                boxShadow: '0 0 6px rgba(255,255,255,0.5)',
                transition: 'bottom 600ms cubic-bezier(0.16, 1, 0.3, 1)',
              }}
            />
          )}
        </>
      ) : (
        <div style={{
          width: '100%', height: '100%', display: 'flex',
          alignItems: 'center', justifyContent: 'center',
          fontSize: 11, color: 'rgba(255,255,255,0.25)',
        }}>
          No art
        </div>
      )}
    </div>
  );
}

function fmtGb(bytes: number): string {
  if (!bytes) return '—';
  const gb = bytes / (1024 ** 3);
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${(bytes / (1024 ** 2)).toFixed(0)} MB`;
}

function fmtSpeed(bps: number): string {
  const mb = bps / (1024 ** 2);
  if (mb >= 1) return `${mb.toFixed(1)} MB/s`;
  return `${Math.max(0, Math.round(bps / 1024))} KB/s`;
}

interface HistoryApiItem {
  tmdb_title?: string;
  tmdb_year?: number | string;
  media_type?: string;
  completed_timestamp?: number;
  tmdb_id?: number | null;
  tracker?: string | null;
  source?: string;
  season?: number | null;
  episode?: number | null;
  episode_title?: string | null;
  torrent_name?: string | null;
  poster_path?: string | null;
  origin?: string;
  origin_label?: string;
}

/** Origin chip: one neutral gray pill; Genre Roulette gets a faint violet tint. */
function originChipStyle(badge: string): React.CSSProperties | undefined {
  if (badge === 'Genre Roulette') {
    return { background: 'rgba(167,139,250,0.12)', color: 'rgba(196,181,253,0.8)' };
  }
  return undefined;
}

/** The last answers (this session): a reopened sheet renders them on frame 0, then refreshes quietly. */
let feedCache: { entries: ActivityEntry[]; active: ActiveDownload[]; activeLoaded: boolean; historyLoaded: boolean } = { entries: [], active: [], activeLoaded: false, historyLoaded: false };
export function _clearActivityCache() { feedCache = { entries: [], active: [], activeLoaded: false, historyLoaded: false }; }

export function ActivityFeed({ embedded = false, onOpen, reveal }: { embedded?: boolean; onOpen?: () => void;
  /** In a sheet: the first load (history and the active list) settles in one ContentReveal. */
  reveal?: { id: string; landMs: number } }) {
  const [entries, setEntries] = useState<ActivityEntry[]>(() => feedCache.entries);
  const [active, setActive] = useState<ActiveDownload[]>(() => feedCache.active);
  const [activeLoaded, setActiveLoaded] = useState(() => feedCache.activeLoaded);
  // Per-row remove UI lives inside the filename expand panel (not a standalone
  // Remove… button). confirmHash = delete-files confirm; busyHash = in-flight.
  const [confirmHash, setConfirmHash] = useState<string | null>(null);
  const [busyHash, setBusyHash] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [loading, setLoading] = useState(() => !feedCache.historyLoaded);
  const [error, setError] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);
  // Which rows have their full torrent/file name expanded (keyed by a stable id).
  const [expandedNames, setExpandedNames] = useState<Set<string>>(new Set());
  const toggleName = useCallback((key: string) => {
    setExpandedNames((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
        // Collapse hides remove actions — reset confirm so it doesn't stick.
        setConfirmHash(null);
        setActionError(null);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

  const runRemove = useCallback(async (dl: ActiveDownload, deleteFiles: boolean) => {
    if (!dl.hash || busyHash) return;
    setBusyHash(dl.hash);
    setActionError(null);
    setConfirmHash(null);
    // Optimistic: drop the row immediately so the UI feels snappy; poll will reconcile.
    setActive((prev) => prev.filter((x) => x.hash !== dl.hash));
    const res = await removeDownload(dl.hash, deleteFiles);
    setBusyHash(null);
    if (!res.ok) {
      setActionError(res.error || 'Remove failed');
      // Force a refresh so a failed optimistic remove comes back.
      setReloadTick((t) => t + 1);
      // Also re-trigger active poll by bumping — loadActive is interval-bound;
      // kick it via a one-shot fetch below.
      apiFetch('/api/downloads/queue')
        .then((r) => (r.ok ? r.json() : { downloads: [] }))
        .catch(() => ({ downloads: [] }))
        .then((data: { downloads?: Record<string, unknown>[] }) => {
          const list: ActiveDownload[] = (data.downloads ?? []).map((d) => ({
            title: (d.title as string) || (d.name as string) || '',
            year: (d.year as number | string) || '',
            tmdbId: (d.tmdb_id as number) ?? null,
            mediaType: (d.media_type as string) === 'tv' ? 'tv' : 'movie',
            progress: (d.progress as number) ?? 0,
            eta: (d.eta as string) ?? null,
            status: (d.status as string) ?? 'downloading',
            tracker: (d.tracker as string) ?? null,
            name: (d.name as string) ?? '',
            hash: ((d.hash as string) || '').toLowerCase(),
            sizeTotal: (d.size_total as number) ?? 0,
            sizeRemaining: (d.size_remaining as number) ?? 0,
            speed: (d.speed as number) ?? 0,
            posterPath: (d.poster_path as string) ?? null,
          }));
          setActive(list);
        });
    }
  }, [busyHash]);

  const prevActiveKeysRef = useRef<string[]>([]);
  const { openMovie, openTv, openPerson } = useModalHost();

  // Cast/crew tap inside a modal opened from the activity shelf → open the
  // person modal (was a silent dead tap when no handler was passed through).
  const handlePersonSearch = useCallback((name: string, personId?: number) => {
    if (personId) {
      onOpen?.();
      openPerson({ personId, personName: name });
    }
  }, [openPerson, onOpen]);

  const openEntry = useCallback((entry: { tmdbId: number | null; mediaType: 'movie' | 'tv' }) => {
    if (!entry.tmdbId) return;
    onOpen?.();
    openModalForItem(
      { openMovie, openTv },
      { mediaType: entry.mediaType, tmdbId: entry.tmdbId },
      'now_available',
      { onPersonSearch: handlePersonSearch },
    );
  }, [openMovie, openTv, onOpen, handlePersonSearch]);

  // Poll qBittorrent for everything currently downloading (any origin, incl.
  // manual adds). Shown at the top of the feed with a live progress bar + ETA.
  useEffect(() => {
    let stop = false;
    const loadActive = () => {
      // Skip polling while hidden (screensaver/background) — resumes on return.
      if (typeof document !== 'undefined' && document.hidden) return;
      apiFetch('/api/downloads/queue')
        .then((r) => (r.ok ? r.json() : { downloads: [] }))
        .catch(() => ({ downloads: [] }))
        .then((data: { downloads?: Record<string, unknown>[] }) => {
          if (stop) return;
          const list: ActiveDownload[] = (data.downloads ?? []).map((d) => ({
            title: (d.title as string) || (d.name as string) || '',
            year: (d.year as number | string) || '',
            tmdbId: (d.tmdb_id as number) ?? null,
            mediaType: (d.media_type as string) === 'tv' ? 'tv' : 'movie',
            progress: (d.progress as number) ?? 0,
            eta: (d.eta as string) ?? null,
            status: (d.status as string) ?? 'downloading',
            tracker: (d.tracker as string) ?? null,
            name: (d.name as string) ?? '',
            hash: ((d.hash as string) || '').toLowerCase(),
            sizeTotal: (d.size_total as number) ?? 0,
            sizeRemaining: (d.size_remaining as number) ?? 0,
            speed: (d.speed as number) ?? 0,
            posterPath: (d.poster_path as string) ?? null,
          }));
          // When an item leaves the active list (finished copying/importing),
          // refresh the completed-history so it shows up immediately, no gap.
          const newKeys = list.map((d) => d.name || d.title);
          if (prevActiveKeysRef.current.some((k) => !newKeys.includes(k))) {
            setReloadTick((t) => t + 1);
          }
          prevActiveKeysRef.current = newKeys;
          feedCache.active = list; feedCache.activeLoaded = true;
          setActive(list); setActiveLoaded(true);
        });
    };
    loadActive();
    const id = setInterval(loadActive, 4000);
    return () => { stop = true; clearInterval(id); };
  }, []);

  const load = useCallback(() => {
    if (!feedCache.historyLoaded) setLoading(true);
    setError(false);
    apiFetch('/api/downloads/history?limit=30')
      .then((r) => (r.ok ? r.json() : { history: [], _error: true }))
      .catch(() => ({ history: [], _error: true }))
      .then((hist) => {
        const downloads = ((hist as { history?: HistoryApiItem[] }).history ?? []);
        const list: ActivityEntry[] = downloads.map((d) => {
          const badge = d.origin_label || 'Other';
          return {
            title: d.tmdb_title || '',
            year: d.tmdb_year || '',
            tmdbId: d.tmdb_id ?? null,
            mediaType: (d.media_type === 'tv' ? 'tv' : 'movie') as 'tv' | 'movie',
            ts: (d.completed_timestamp || 0) * 1000,
            tracker: d.tracker ?? null,
            badge,
            season: d.season ?? null,
            episode: d.episode ?? null,
            episodeTitle: d.episode_title ?? null,
            fileName: d.torrent_name ?? null,
            posterPath: d.poster_path ?? null,
          };
        }).sort((a, b) => b.ts - a.ts);
        if (!(list.length === 0 && (hist as { _error?: boolean })._error)) { feedCache.entries = list; feedCache.historyLoaded = true; }
        setEntries(list);
        // Only call it "empty" when the history actually loaded. A failed/timed-
        // out fetch must not masquerade as "no downloads".
        if (list.length === 0 && (hist as { _error?: boolean })._error) setError(true);
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load, reloadTick]);

  // Distinct trackers present (for the legend).
  const trackers = Array.from(
    new Set(entries.map((e) => e.tracker).filter((x): x is string => !!x))
  );

  const listClass = embedded
    ? 'space-y-3'
    : 'max-h-[60vh] lg:max-h-[420px] overflow-y-auto rounded-lg space-y-3';
  const listStyle = embedded ? undefined : { scrollbarWidth: 'thin' as const, WebkitOverflowScrolling: 'touch' as const, overscrollBehavior: 'contain' as const, touchAction: 'pan-y' as const };

  // Currently-downloading torrents (any origin) — always rendered on top.
  const activeSection = active.length > 0 ? (
    <div style={{ marginBottom: '26px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px', paddingLeft: '2px' }}>
        <span className="animate-pulse" style={{ width: '7px', height: '7px', borderRadius: '50%', background: '#3b82f6', boxShadow: '0 0 8px rgba(59,130,246,0.7)' }} />
        <span className="text-[13px] font-semibold uppercase tracking-wide text-white/50">
          Downloading now ({active.length})
        </span>
      </div>
      <div className="space-y-3">
        {active.map((dl, i) => {
          const c = dl.tracker ? getBadgeColor(dl.tracker) : null;
          const pct = Math.round(dl.progress * 100);
          const stalled = dl.status === 'stalled';
          const copying = dl.status === 'copying';
          const importing = dl.status === 'importing';
          const accent = copying || importing ? '#4ade80' : stalled ? '#fbbf24' : '#60a5fa';
          const barColor = copying || importing ? '#22c55e' : stalled ? '#f59e0b' : '#3b82f6';
          const statusLabel = importing
            ? 'Processing…'
            : copying ? `Copying to library… ${pct}%`
            : stalled ? 'Stalled'
            : dl.eta ? `${dl.eta} left` : 'Downloading…';
          const speedStr = dl.status === 'downloading' && dl.speed > 0 ? fmtSpeed(dl.speed) : null;
          const clickable = dl.tmdbId != null;
          const nameKey = `active:${dl.hash || dl.name || i}`;
          const nameOpen = expandedNames.has(nameKey);
          return (
            <div
              key={dl.hash || `${dl.name || dl.title}-${i}`}
              role={clickable ? 'button' : undefined}
              onClick={clickable ? () => openEntry({ tmdbId: dl.tmdbId, mediaType: dl.mediaType }) : undefined}
              className={clickable ? 'cursor-pointer transition-colors hover:brightness-125' : ''}
              style={{
                position: 'relative',
                overflow: 'hidden',
                background: '#111112',
                borderRadius: '18px',
                padding: '18px 20px',
                border: `1px solid ${c ? c.border.replace('0.3)', '0.15)') : 'rgba(255,255,255,0.06)'}`,
                borderLeft: `3px solid ${c ? c.fg : '#3b82f6'}`,
              }}
            >
              <RowWash posterPath={dl.posterPath} />
              <div className="relative flex items-start gap-4">
              <RowThumb posterPath={dl.posterPath} title={dl.title} progress={dl.progress} />
              <div className="flex-1 min-w-0">
              <div className="flex items-start gap-2 mb-2">
                {(() => {
                  const hasCleanTitle = !!dl.title && dl.title !== dl.name;
                  // When we have a clean matched title, show it here (the raw file
                  // name gets its own expandable line below). When we don't, the
                  // header IS the raw torrent name — make it tap-to-expand so the
                  // full name + remove actions are reachable.
                  if (hasCleanTitle) {
                    return (
                      <span className="flex-1 min-w-0 line-clamp-2 text-[15px] leading-snug text-white/85">
                        {dl.title}
                        {dl.year ? <span className="ml-1 text-white/30">({dl.year})</span> : null}
                      </span>
                    );
                  }
                  return (
                    <span
                      role="button"
                      onClick={(e) => { e.stopPropagation(); toggleName(nameKey); }}
                      title={dl.name}
                      className={`flex-1 min-w-0 text-[15px] leading-snug text-white/85 cursor-pointer ${nameOpen ? 'whitespace-normal break-all' : 'truncate'}`}
                    >
                      {dl.name || 'Unknown'}
                    </span>
                  );
                })()}
                {dl.tracker && (
                  <span style={{ background: c!.bg, color: c!.fg, border: `1px solid ${c!.border}` }}
                    className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold">
                    {dl.tracker}
                  </span>
                )}
                <span className="shrink-0 text-[14px] font-semibold tabular-nums" style={{ color: accent }}>
                  {pct}%
                </span>
              </div>
              {dl.name && dl.title && dl.name !== dl.title ? (
                  <p
                    role="button"
                    onClick={(e) => { e.stopPropagation(); toggleName(nameKey); }}
                    title={dl.name}
                    className={`mb-2 text-[12px] text-white/30 cursor-pointer hover:text-white/50 ${nameOpen ? 'whitespace-normal break-all' : 'truncate'}`}
                  >
                    {dl.name}
                  </p>
              ) : null}
              <div className="h-[5px] w-full overflow-hidden rounded-full bg-white/10">
                <div className="h-full rounded-full transition-[width] duration-500"
                  style={{ width: `${pct}%`, background: barColor }} />
              </div>
              <div className="mt-2 flex items-center justify-between gap-3 text-[11px] text-white/35">
                <span className="min-w-0 truncate" style={{ color: copying || importing || stalled ? accent : undefined }}>
                  {statusLabel}{speedStr ? ` · ↓ ${speedStr}` : ''}
                </span>
                <span className="shrink-0 tabular-nums">{fmtGb(dl.sizeTotal - dl.sizeRemaining)} / {fmtGb(dl.sizeTotal)}</span>
              </div>
              {/* Remove actions — only inside the expanded filename panel (tap name to open). */}
              {dl.hash && nameOpen ? (
                <div className="mt-2.5" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                  {confirmHash === dl.hash ? (
                    <div
                      className="rounded-xl px-3 py-2.5"
                      style={{ background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.35)' }}
                    >
                      <p className="text-[12px] leading-snug text-red-200/90">
                        Delete torrent <span className="font-semibold">and files</span> from disk? This cannot be undone.
                      </p>
                      <div className="mt-2 flex gap-2">
                        <button
                          type="button"
                          disabled={busyHash === dl.hash}
                          onClick={() => setConfirmHash(null)}
                          className="flex-1 rounded-lg px-2.5 py-2 text-[12px] font-medium text-white/70 active:bg-white/10"
                          style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)' }}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          disabled={busyHash === dl.hash}
                          onClick={() => void runRemove(dl, true)}
                          className="flex-1 rounded-lg px-2.5 py-2 text-[12px] font-semibold text-white active:opacity-80"
                          style={{ background: 'rgba(239,68,68,0.85)' }}
                        >
                          {busyHash === dl.hash ? 'Deleting…' : 'Delete torrent + files'}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      disabled={busyHash === dl.hash}
                      onClick={() => { setConfirmHash(dl.hash); setActionError(null); }}
                      className="w-full rounded-lg px-3 py-2 text-left text-[12px] font-medium text-red-300/90 active:bg-red-500/15"
                      style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.28)' }}
                    >
                      Delete torrent + files
                      <span className="mt-0.5 block text-[10px] font-normal text-red-300/50">Removes transfer and deletes files</span>
                    </button>
                  )}
                  {actionError && confirmHash !== dl.hash ? (
                    <p className="mt-1.5 text-[11px] text-red-400/90">{actionError}</p>
                  ) : null}
                </div>
              ) : null}
              </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  ) : null;

  // History section states (rendered below the active section).
  let historySection: ReactNode;
  // Only show the full-list "Loading…" on the very first load. Background
  // refreshes (fired every time a download finishes) keep the existing list
  // rendered so it doesn't blank/reflow.
  if (loading && entries.length === 0) {
    historySection = reveal ? null : <p className="text-[12px] text-white/30 px-1">Loading...</p>;
  } else if (error && entries.length === 0) {
    historySection = (
      <button onClick={load} className="text-[12px] text-white/45 px-1 underline underline-offset-2">
        Couldn't load activity — tap to retry
      </button>
    );
  } else if (entries.length === 0) {
    historySection = active.length > 0 ? null : <p className="text-[12px] text-white/30 px-1">No downloads yet.</p>;
  } else {
    historySection = (
    <>
      {/* Section header mirroring "Downloading now" — without it the two lists
          read as one continuous feed. The rule only appears when there's an
          active section above to separate from. */}
      <div
        style={{
          display: 'flex', alignItems: 'center', gap: '8px',
          marginBottom: '12px', paddingLeft: '2px',
          ...(active.length > 0
            ? { borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: '20px' }
            : null),
        }}
      >
        <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: 'rgba(74,222,128,0.85)', boxShadow: '0 0 8px rgba(74,222,128,0.45)' }} />
        <span className="text-[13px] font-semibold uppercase tracking-wide text-white/50">
          Completed ({entries.length})
        </span>
      </div>
      {/* Tracker legend — centered above the list, no container (like Sources).
          Hidden in the compact (embedded) shelf where it's just noise. */}
      {!embedded && trackers.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'center', gap: '8px', marginBottom: '14px' }}>
          {trackers.map((name) => {
            const c = getBadgeColor(name);
            return (
              <span
                key={name}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: '5px',
                  padding: '6px 12px', borderRadius: '10px',
                  fontSize: '11px', fontWeight: 700,
                  background: c.bg, color: c.fg, border: `1px solid ${c.border}`,
                  lineHeight: 1.4,
                }}
              >
                <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: c.fg, flexShrink: 0 }} />
                {name}
              </span>
            );
          })}
        </div>
      )}
      <div className={listClass} style={listStyle}>
        {entries.map((entry, i) => {
          const c = entry.tracker ? getBadgeColor(entry.tracker) : null;
          return (
            <div
              key={`${entry.tmdbId ?? entry.title}-${i}`}
              role="button"
              onClick={() => openEntry(entry)}
              className="cursor-pointer transition-colors hover:brightness-125"
              style={{
                position: 'relative',
                overflow: 'hidden',
                background: '#111112',
                borderRadius: '18px',
                padding: '18px 20px',
                border: `1px solid ${c ? c.border.replace('0.3)', '0.15)') : 'rgba(255,255,255,0.06)'}`,
                borderLeft: `3px solid ${c ? c.fg : 'rgba(255,255,255,0.18)'}`,
              }}
            >
              <RowWash posterPath={entry.posterPath} />
              <div className="relative flex items-start gap-4">
              <RowThumb posterPath={entry.posterPath} title={entry.title} />
              <div className="flex-1 min-w-0">
                <div className="flex items-start gap-2">
                  <p className="flex-1 min-w-0 text-[15px] text-white/85 line-clamp-2 leading-snug">
                    {entry.title}
                    {entry.year ? <span className="text-white/30 ml-1">({entry.year})</span> : null}
                  </p>
                  {/* Origin badge — was a 92px text column, now a chip, so the
                      poster costs no horizontal room from the title. */}
                  <span
                    className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide bg-white/[0.06] text-white/50 whitespace-nowrap"
                    style={originChipStyle(entry.badge)}
                  >
                    {entry.badge}
                  </span>
                </div>
                {entry.mediaType === 'tv' && entry.season != null && (
                  <p className="text-[12.5px] text-white/45 mt-1 truncate">
                    S{entry.season}
                    {entry.episode != null ? ` · E${entry.episode}` : ''}
                    {entry.episodeTitle ? ` — ${entry.episodeTitle}` : ''}
                  </p>
                )}
                {entry.fileName && entry.fileName !== entry.title && (() => {
                  const nameKey = `hist:${i}:${entry.fileName}`;
                  const open = expandedNames.has(nameKey);
                  return (
                    <p
                      role="button"
                      onClick={(e) => { e.stopPropagation(); toggleName(nameKey); }}
                      title={entry.fileName ?? undefined}
                      className={`text-[12px] text-white/30 mt-1.5 cursor-pointer hover:text-white/50 ${open ? 'whitespace-normal break-all' : 'truncate'}`}
                    >
                      {entry.fileName}
                    </p>
                  );
                })()}
                <p className="text-[12px] text-white/30 mt-1.5">
                  {entry.tracker && (
                    <>
                      <span className="font-semibold uppercase tracking-wide" style={{ color: c!.fg }}>
                        {entry.tracker}
                      </span>
                      <span className="mx-1.5 text-white/20">·</span>
                    </>
                  )}
                  {entry.ts ? new Date(entry.ts).toLocaleDateString(undefined, {
                    month: 'short', day: 'numeric', year: 'numeric',
                    hour: '2-digit', minute: '2-digit',
                  } as Intl.DateTimeFormatOptions) : ''}
                </p>
              </div>
              </div>
            </div>
          );
        })}
      </div>
    </>
    );
  }

  if (reveal) {
    return (
      <ContentReveal id={reveal.id} landMs={reveal.landMs} ready={!(loading && entries.length === 0) && activeLoaded} testId="activity-reveal">
        {activeSection}
        {historySection}
      </ContentReveal>
    );
  }
  return (
    <>
      {activeSection}
      {historySection}
    </>
  );
}
