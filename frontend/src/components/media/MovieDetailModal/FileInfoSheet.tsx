/**
 * FileInfoSheet — Plex-style "File Info" for the movie detail modal (ⓘ button).
 *
 * Data: GET /api/plex/file-info/<ratingKey> (app/plex_file_info.py) — Plex
 * Media → Part → Stream, with the file path rewritten from the Plex
 * container mount (/movies/...) to the NAS share path (/Media/Video/...).
 *
 * Styled in Luma's own theme (kiosk-black sheet, white/alpha text, green
 * accent) — not Plex grey. Compact: bottom sheet (drag the handle or tap the
 * scrim to dismiss). Expanded/tablet: centred panel.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { apiFetch } from '../../../apiClient';

interface FileInfoStream {
  id: number | null;
  display: string;
  codec?: string | null;
  language?: string | null;
  default: boolean;
  selected: boolean;
  forced: boolean;
  external: boolean;
}

interface FileInfoPart {
  id: number | null;
  path: string;
  size_bytes: number | null;
  duration_ms: number | null;
  container?: string | null;
  video_streams: FileInfoStream[];
  audio_streams: FileInfoStream[];
  subtitle_streams: FileInfoStream[];
}

interface FileInfoVersion {
  id: number | null;
  duration_ms: number | null;
  bitrate_kbps: number | null;
  resolution: string | null;
  width: number | null;
  height: number | null;
  container: string | null;
  size_bytes: number | null;
  parts: FileInfoPart[];
}

interface FileInfoData {
  rating_key: number;
  title: string;
  year: number | null;
  server: string | null;
  library: string | null;
  duration_ms: number | null;
  versions: FileInfoVersion[];
}

export interface FileInfoCopy {
  library: string;
  ratingKey: number;
}

interface FileInfoSheetProps {
  title: string;
  /** The item the modal is showing. */
  ratingKey: number;
  /** Other copies of this title (library → ratingKey); shows a switcher when >1. */
  copies?: FileInfoCopy[];
  variant: 'compact' | 'expanded';
  onClose: () => void;
  /** Owner only — opens the Fix Match sheet for the item shown. */
  onFixMatch?: (ratingKey: number) => void;
}

const GREEN = '#4ade80';
const DISMISS_THRESHOLD = 90;

// ---------------------------------------------------------------------------
// Formatting — mirrors Plex's File Info strings
// ---------------------------------------------------------------------------

function fmtDuration(ms: number | null | undefined): string {
  if (!ms || ms <= 0) return '—';
  const total = Math.round(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(h > 0 ? 2 : 1, '0');
  const ss = String(s).padStart(2, '0');
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function fmtBitrate(kbps: number | null | undefined): string {
  if (!kbps) return '—';
  return `${kbps.toLocaleString('en-US')} kbps`;
}

function fmtSize(bytes: number | null | undefined): string {
  if (!bytes) return '—';
  // Plex shows MiB labelled "MB" with two decimals (7,146.71 MB)
  const mb = bytes / 1048576;
  return `${mb.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} MB`;
}

function fmtResolution(v: FileInfoVersion): string {
  const r = (v.resolution || '').toLowerCase();
  if (!r) return v.height ? `${v.height}p` : '—';
  if (r === '4k') return '4K';
  if (r === 'sd') return 'SD';
  if (/^\d+$/.test(r)) return `${r}p`;
  return r.toUpperCase();
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the legacy path
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Small pieces
// ---------------------------------------------------------------------------

function Chip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        flexShrink: 0,
        padding: '7px 14px',
        borderRadius: 999,
        border: active ? '1px solid rgba(255,255,255,0.9)' : '1px solid rgba(255,255,255,0.14)',
        background: active ? '#fff' : 'rgba(255,255,255,0.06)',
        color: active ? '#111' : 'rgba(255,255,255,0.85)',
        fontSize: 13,
        fontWeight: 600,
        cursor: 'pointer',
        whiteSpace: 'nowrap',
      }}
    >
      {label}
    </button>
  );
}

function PathCard({ path }: { path: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const onCopy = async () => {
    const ok = await copyText(path);
    if (!ok) return;
    setCopied(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), 1600);
  };
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '12px 8px 12px 14px',
        borderRadius: 12,
        background: 'rgba(255,255,255,0.06)',
        border: '1px solid rgba(255,255,255,0.08)',
      }}
    >
      <span
        data-file-path
        style={{
          flex: 1,
          minWidth: 0,
          fontSize: 14,
          lineHeight: 1.4,
          color: 'rgba(255,255,255,0.9)',
          wordBreak: 'break-word',
          userSelect: 'text',
          WebkitUserSelect: 'text',
        }}
      >
        {path || '—'}
      </span>
      <button
        type="button"
        onClick={onCopy}
        aria-label={copied ? 'Copied' : 'Copy path'}
        style={{
          flexShrink: 0,
          width: 36,
          height: 36,
          borderRadius: '50%',
          border: 'none',
          background: copied ? 'rgba(74,222,128,0.14)' : 'rgba(255,255,255,0.06)',
          color: copied ? GREEN : 'rgba(255,255,255,0.75)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          cursor: 'pointer',
          transition: 'background 0.15s, color 0.15s',
        }}
      >
        {copied ? (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        ) : (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </svg>
        )}
      </button>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.45)', marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 500, color: 'rgba(255,255,255,0.92)', overflowWrap: 'anywhere' }}>{value}</div>
    </div>
  );
}

function StreamSection({ heading, streams, checkSelected, fallbackDefault }: { heading: string; streams: FileInfoStream[]; checkSelected?: boolean; fallbackDefault?: boolean }) {
  // Plex ticks the selected track; audio falls back to the default track when
  // nothing is flagged selected (subtitles don't — "default" ≠ switched on).
  const sel = streams.findIndex((s) => s.selected);
  const selIdx = !checkSelected ? -1 : sel >= 0 ? sel : fallbackDefault ? streams.findIndex((s) => s.default) : -1;
  return (
    <div style={{ marginTop: 22 }}>
      <h3
        style={{
          margin: 0,
          paddingBottom: 10,
          fontSize: 15,
          fontWeight: 600,
          color: 'rgba(255,255,255,0.95)',
          borderBottom: '1px solid rgba(255,255,255,0.08)',
        }}
      >
        {heading}
      </h3>
      {streams.length === 0 ? (
        <div style={{ padding: '14px 0 2px', fontSize: 14, color: 'rgba(255,255,255,0.4)' }}>No streams available</div>
      ) : (
        streams.map((s, i) => {
          const ticked = checkSelected && i === selIdx;
          const tags = [s.forced ? 'Forced' : null, s.external ? 'External' : null].filter(Boolean).join(' · ');
          return (
            <div
              key={s.id ?? i}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '13px 0',
                borderBottom: i < streams.length - 1 ? '1px solid rgba(255,255,255,0.05)' : 'none',
              }}
            >
              {checkSelected && (
                <span style={{ width: 18, flexShrink: 0, color: GREEN, display: 'flex' }}>
                  {ticked && (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  )}
                </span>
              )}
              <span style={{ flex: 1, minWidth: 0, fontSize: 15, color: ticked ? '#fff' : 'rgba(255,255,255,0.85)', fontWeight: ticked ? 500 : 400 }}>
                {s.display}
              </span>
              {tags && <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.4)' }}>{tags}</span>}
            </div>
          );
        })
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sheet
// ---------------------------------------------------------------------------

export function FileInfoSheet({ title, ratingKey, copies, variant, onClose, onFixMatch }: FileInfoSheetProps) {
  const isCompact = variant === 'compact';
  const { isTablet } = useLayoutMode();
  // Expanded layouts use much larger type (26px meta line), so scale the
  // sheet's content up to match instead of maintaining a second size table.
  const zoom = isCompact ? 1 : isTablet ? 1.3 : 1.12;
  // Library switcher only when this title lives in >1 library and the open
  // item is one of them (e.g. 300 → Movies | Full Rips).
  const copyList: FileInfoCopy[] =
    copies && copies.length > 1 && copies.some((c) => c.ratingKey === ratingKey) ? copies : [];
  const [activeKey, setActiveKey] = useState<number>(ratingKey);
  const [data, setData] = useState<FileInfoData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [versionIdx, setVersionIdx] = useState(0);

  const [entered, setEntered] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [dragOffset, setDragOffset] = useState(0);
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });

  useEffect(() => {
    const raf = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setVersionIdx(0);
    apiFetch(`/api/plex/file-info/${activeKey}`, { signal: controller.signal, credentials: 'same-origin' })
      .then(async (r) => {
        if (r.status === 401) throw new Error('Sign in to see file info');
        if (!r.ok) throw new Error('Could not load file info');
        return r.json();
      })
      .then((d: FileInfoData) => { setData(d); setLoading(false); })
      .catch((e: Error) => {
        if (e.name === 'AbortError') return;
        setError(e.message || 'Could not load file info');
        setLoading(false);
      });
    return () => controller.abort();
  }, [activeKey]);

  const handleDismiss = useCallback(() => {
    setDismissing(true);
    window.setTimeout(onClose, 250);
  }, [onClose]);

  // ESC closes this sheet only (capture phase so the movie modal doesn't also close)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      handleDismiss();
    }
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [handleDismiss]);

  // Drag from the handle only (content scrolls vertically)
  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const target = e.target as HTMLElement;
    if (!target.closest('[data-sheet-handle]') || e.touches.length === 0) return;
    const y = e.touches[0]!.clientY;
    dragRef.current = { active: true, startY: y, currentY: y };
  }, []);
  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragRef.current.active || e.touches.length === 0) return;
    const y = e.touches[0]!.clientY;
    if (y - dragRef.current.startY > 0) {
      dragRef.current.currentY = y;
      setDragOffset(y - dragRef.current.startY);
    }
  }, []);
  const onTouchEnd = useCallback(() => {
    if (!dragRef.current.active) return;
    const dy = dragRef.current.currentY - dragRef.current.startY;
    dragRef.current.active = false;
    if (dy > DISMISS_THRESHOLD) handleDismiss();
    else setDragOffset(0);
  }, [handleDismiss]);

  const versions = data?.versions ?? [];
  const version = versions[Math.min(versionIdx, Math.max(versions.length - 1, 0))];
  const parts = version?.parts ?? [];
  const multiPart = parts.length > 1;

  const header = (
    <div style={{ position: 'relative', flexShrink: 0, padding: isCompact ? '0 24px 14px' : '22px 64px 18px', textAlign: 'center', zoom }}>
      <div style={{ fontSize: isCompact ? 18 : 20, fontWeight: 700, color: '#fff', lineHeight: 1.25, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {data?.title || title}
      </div>
      <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.5)', marginTop: 2 }}>File info</div>
      {/* Phone closes by swipe/scrim tap — no ✕ there. */}
      {!isCompact && (
      <button
        type="button"
        onClick={handleDismiss}
        aria-label="Close file info"
        style={{
          position: 'absolute',
          right: isCompact ? 14 : 18,
          top: isCompact ? 2 : 22,
          width: 32,
          height: 32,
          borderRadius: '50%',
          border: 'none',
          background: 'rgba(255,255,255,0.06)',
          color: 'rgba(255,255,255,0.7)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          cursor: 'pointer',
        }}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
      )}
    </div>
  );

  const fixMatchRow = onFixMatch ? (
    <button
      type="button"
      data-fix-match-button
      onClick={() => onFixMatch(activeKey)}
      style={{
        width: '100%',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        marginTop: 10,
        padding: '13px 14px',
        borderRadius: 12,
        border: '1px solid rgba(255,255,255,0.08)',
        background: 'rgba(255,255,255,0.06)',
        color: '#fff',
        fontSize: 15,
        fontWeight: 600,
        textAlign: 'left',
        cursor: 'pointer',
        WebkitTapHighlightColor: 'transparent',
      }}
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M21 12a9 9 0 1 1-2.64-6.36" />
        <polyline points="21 4 21 10 15 10" />
      </svg>
      <span style={{ flex: 1 }}>Fix match</span>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.45)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <polyline points="9 18 15 12 9 6" />
      </svg>
    </button>
  ) : null;

  const body = (
    <div style={{ padding: isCompact ? '0 16px' : '0 28px', zoom }}>
      {copyList.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
          {copyList.map((c) => (
            <Chip key={c.ratingKey} label={c.library} active={c.ratingKey === activeKey} onClick={() => setActiveKey(c.ratingKey)} />
          ))}
        </div>
      )}

      {loading && (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '48px 0 64px' }}>
          <div style={{ width: 32, height: 32, borderRadius: '50%', border: '3px solid rgba(255,255,255,0.15)', borderTopColor: 'rgba(255,255,255,0.7)', animation: 'fileInfoSpin 0.8s linear infinite' }} />
        </div>
      )}

      {!loading && error && (
        <>
          <div style={{ padding: '40px 0 56px', textAlign: 'center', fontSize: 15, color: 'rgba(255,255,255,0.5)' }}>{error}</div>
          {fixMatchRow}
        </>
      )}

      {!loading && !error && data && !version && (
        <div style={{ padding: '40px 0 56px', textAlign: 'center', fontSize: 15, color: 'rgba(255,255,255,0.5)' }}>No media files found</div>
      )}

      {!loading && !error && data && version && (
        <>
          {versions.length > 1 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
              {versions.map((v, i) => (
                <Chip
                  key={v.id ?? i}
                  label={`Version ${i + 1} · ${fmtResolution(v)}`}
                  active={i === versionIdx}
                  onClick={() => setVersionIdx(i)}
                />
              ))}
            </div>
          )}

          {!multiPart && parts[0] && <PathCard path={parts[0].path} />}

          {/* Fix Match sits right under the file path, above the stats */}
          {fixMatchRow}

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
              columnGap: 12,
              rowGap: 18,
              marginTop: 18,
            }}
          >
            <Stat label="Server" value={data.server || '—'} />
            <Stat label="Library" value={data.library || '—'} />
            <Stat label="Duration" value={fmtDuration(version.duration_ms ?? data.duration_ms)} />
            <Stat label="Bitrate" value={fmtBitrate(version.bitrate_kbps)} />
            <Stat label="Resolution" value={fmtResolution(version)} />
            <Stat label="Parts" value={String(parts.length || 1)} />
            <Stat label="Size" value={fmtSize(version.size_bytes)} />
            {version.container && <Stat label="Container" value={version.container.toUpperCase()} />}
          </div>

          {parts.map((part, pi) => (
            <div key={part.id ?? pi} style={{ marginTop: multiPart ? 26 : 0 }}>
              {multiPart && (
                <>
                  <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 10 }}>
                    <span style={{ fontSize: 16, fontWeight: 700, color: '#fff' }}>Part {pi + 1}</span>
                    <span style={{ fontSize: 13, color: 'rgba(255,255,255,0.5)' }}>
                      {fmtDuration(part.duration_ms)} · {fmtSize(part.size_bytes)}
                    </span>
                  </div>
                  <PathCard path={part.path} />
                </>
              )}
              <StreamSection heading="Video streams" streams={part.video_streams} />
              <StreamSection heading="Audio streams" streams={part.audio_streams} checkSelected fallbackDefault />
              <StreamSection heading="Subtitle streams" streams={part.subtitle_streams} checkSelected />
            </div>
          ))}
        </>
      )}
      <div style={{ height: isCompact ? 'calc(env(safe-area-inset-bottom, 0px) + 20px)' : 28 }} />
    </div>
  );

  const scroller = (
    <div
      data-scroll-container
      className="luma-scroll-y"
      style={{
        flex: 1,
        minHeight: 0,
        overflowY: 'auto',
        overflowX: 'hidden',
        WebkitOverflowScrolling: 'touch',
        overscrollBehavior: 'contain',
        touchAction: 'pan-y',
      }}
    >
      {body}
    </div>
  );

  const scrimVisible = entered && !dismissing;

  const content = isCompact ? (
    <div
      data-file-info-sheet
      role="dialog"
      aria-modal="true"
      aria-label={`${title} file info`}
      onClick={(e) => { if (e.target === e.currentTarget) handleDismiss(); }}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10060,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'flex-end',
        background: scrimVisible ? 'rgba(0,0,0,0.45)' : 'transparent',
        transition: 'background 0.25s ease',
      }}
    >
      <div
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        style={{
          maxHeight: 'calc(100% - max(env(safe-area-inset-top, 0px), 20px) - 24px)',
          display: 'flex',
          flexDirection: 'column',
          background: 'var(--color-kiosk-black, #15102a)',
          borderRadius: '20px 20px 0 0',
          boxShadow: '0 -12px 40px rgba(0,0,0,0.45)',
          overflow: 'hidden',
          transform: `translateY(${dismissing ? '100%' : entered ? `${dragOffset}px` : '100%'})`,
          transition: dragOffset > 0 ? 'none' : 'transform 0.3s cubic-bezier(0.32, 0.72, 0, 1)',
          willChange: 'transform',
        }}
      >
        <div
          data-sheet-handle
          style={{ flexShrink: 0, height: 30, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'grab', touchAction: 'none' }}
        >
          <div style={{ width: 40, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)' }} />
        </div>
        {header}
        {scroller}
      </div>
      <style>{`@keyframes fileInfoSpin { to { transform: rotate(360deg); } }`}</style>
    </div>
  ) : (
    <div
      data-file-info-sheet
      role="dialog"
      aria-modal="true"
      aria-label={`${title} file info`}
      onClick={(e) => { if (e.target === e.currentTarget) handleDismiss(); }}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10060,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
        background: scrimVisible ? 'rgba(0,0,0,0.6)' : 'transparent',
        transition: 'background 0.2s ease',
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: Math.round(560 * zoom),
          maxHeight: 'min(86vh, 900px)',
          display: 'flex',
          flexDirection: 'column',
          background: 'var(--color-kiosk-black, #15102a)',
          borderRadius: 20,
          border: '1px solid rgba(255,255,255,0.08)',
          boxShadow: '0 24px 80px rgba(0,0,0,0.6)',
          overflow: 'hidden',
          opacity: scrimVisible ? 1 : 0,
          transform: scrimVisible ? 'scale(1)' : 'scale(0.97)',
          transition: 'opacity 0.2s ease, transform 0.2s ease',
        }}
      >
        {header}
        {scroller}
      </div>
      <style>{`@keyframes fileInfoSpin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );

  return createPortal(content, document.body);
}

// ---------------------------------------------------------------------------
// Circled-i trigger — same stroke style as the modal's other icons
// ---------------------------------------------------------------------------

export function FileInfoIcon({ size = 20, strokeWidth = 2, light = false }: { size?: number; strokeWidth?: number; light?: boolean }) {
  if (light) {
    // Thin variant for the modal's top-right corner: hairline circle, slim
    // stem and a small filled dot (a round-cap dot would vanish at this stroke).
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="10.75" x2="12" y2="16.75" />
        <circle cx="12" cy="7.6" r="0.95" fill="currentColor" stroke="none" />
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="16" x2="12" y2="11" />
      <line x1="12" y1="7.5" x2="12.01" y2="7.5" />
    </svg>
  );
}
