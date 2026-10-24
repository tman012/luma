/**
 * FixMatchSheet — owner-only Plex "Fix Match" (opened from the File Info sheet).
 *
 * Data: app/plex_match.py via
 *   GET  /api/plex/match/<rk>/search?title=&year=
 *   POST /api/plex/match/<rk>/apply   { guid, name, year }
 *   POST /api/plex/match/<rk>/unmatch
 *
 * Compact: bottom sheet, drag the handle/header down to close.
 * Expanded/tablet: centred panel, tap outside (or drag the header) to close.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { apiFetch } from '../../../apiClient';

export interface FixMatchResult {
  rating_key: number;
  type: 'movie' | 'show';
  title: string;
  year: number | null;
  guid: string;
  tmdb_id: number | null;
  changed?: boolean;
  unmatched?: boolean;
}

interface Candidate {
  guid: string;
  name: string;
  year: number | null;
  thumb: string | null;
  score: number | null;
  summary: string;
  current: boolean;
}

interface FixMatchSheetProps {
  ratingKey: number;
  title: string;
  year?: number | null;
  variant: 'compact' | 'expanded';
  onClose: () => void;
  onMatched: (r: FixMatchResult) => void;
}

const BG = 'var(--color-kiosk-black, #15102a)';
const RED = '#ff6b6b';
const DISMISS_THRESHOLD = 90;

function Spinner({ size = 28 }: { size?: number }) {
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        border: `${size > 20 ? 3 : 2}px solid rgba(255,255,255,0.15)`,
        borderTopColor: 'rgba(255,255,255,0.75)',
        animation: 'fixMatchSpin 0.8s linear infinite',
        flexShrink: 0,
      }}
    />
  );
}

function Poster({ src, w }: { src: string | null; w: number }) {
  const [failed, setFailed] = useState(false);
  const h = Math.round(w * 1.5);
  return (
    <div style={{ width: w, height: h, borderRadius: 8, overflow: 'hidden', flexShrink: 0, background: 'rgba(255,255,255,0.07)' }}>
      {src && !failed && (
        <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
      )}
    </div>
  );
}

export function FixMatchSheet({ ratingKey, title, year, variant, onClose, onMatched }: FixMatchSheetProps) {
  const isCompact = variant === 'compact';
  const { isTablet } = useLayoutMode();
  const zoom = isCompact ? 1 : isTablet ? 1.25 : 1.1;

  const [qTitle, setQTitle] = useState(title);
  const [qYear, setQYear] = useState(year ? String(year) : '');
  const [results, setResults] = useState<Candidate[] | null>(null);
  const [searching, setSearching] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [applying, setApplying] = useState<string | null>(null);
  const [confirmUnmatch, setConfirmUnmatch] = useState(false);

  const [entered, setEntered] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [dragOffset, setDragOffset] = useState(0);
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });
  const searchSeq = useRef(0);
  const busy = applying !== null;

  useEffect(() => {
    const raf = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  const runSearch = useCallback((t: string, y: string) => {
    const seq = ++searchSeq.current;
    setSearching(true);
    setError(null);
    const params = new URLSearchParams({ title: t.trim() });
    if (/^\d{4}$/.test(y.trim())) params.set('year', y.trim());
    apiFetch(`/api/plex/match/${ratingKey}/search?${params}`, { credentials: 'same-origin' })
      .then(async (r) => {
        if (r.status === 401 || r.status === 403) throw new Error('Owner only');
        if (!r.ok) throw new Error('Search failed');
        return r.json() as Promise<{ results: Candidate[] }>;
      })
      .then((d) => {
        if (seq !== searchSeq.current) return;
        setResults(d.results || []);
        setSearching(false);
      })
      .catch((e: Error) => {
        if (seq !== searchSeq.current) return;
        setError(e.message || 'Search failed');
        setSearching(false);
      });
  }, [ratingKey]);

  // Auto-search on open, then debounce while editing
  const firstRun = useRef(true);
  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false;
      runSearch(qTitle, qYear);
      return;
    }
    if (!qTitle.trim()) return;
    const t = window.setTimeout(() => runSearch(qTitle, qYear), 550);
    return () => window.clearTimeout(t);
  }, [qTitle, qYear, runSearch]);

  const handleDismiss = useCallback(() => {
    if (busy) return;
    setDismissing(true);
    window.setTimeout(onClose, 250);
  }, [onClose, busy]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      handleDismiss();
    }
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [handleDismiss]);

  const apply = useCallback((c: Candidate) => {
    if (busy) return;
    setApplying(c.guid);
    setError(null);
    apiFetch(`/api/plex/match/${ratingKey}/apply`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ guid: c.guid, name: c.name, year: c.year }),
    })
      .then(async (r) => {
        if (!r.ok) throw new Error('Couldn’t match');
        return r.json() as Promise<FixMatchResult>;
      })
      .then((res) => onMatched(res))
      .catch((e: Error) => {
        setApplying(null);
        setError(e.message || 'Couldn’t match');
      });
  }, [busy, ratingKey, onMatched]);

  const doUnmatch = useCallback(() => {
    if (busy) return;
    setApplying('__unmatch__');
    setError(null);
    apiFetch(`/api/plex/match/${ratingKey}/unmatch`, { method: 'POST', credentials: 'same-origin' })
      .then(async (r) => {
        if (!r.ok) throw new Error('Couldn’t unmatch');
        return r.json() as Promise<FixMatchResult>;
      })
      .then((res) => onMatched({ ...res, unmatched: true }))
      .catch((e: Error) => {
        setApplying(null);
        setConfirmUnmatch(false);
        setError(e.message || 'Couldn’t unmatch');
      });
  }, [busy, ratingKey, onMatched]);

  // Drag down from the handle/header to close
  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const target = e.target as HTMLElement;
    if (!target.closest('[data-sheet-handle]') || target.closest('input,button') || e.touches.length === 0) return;
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
    if (dy > DISMISS_THRESHOLD && !busy) handleDismiss();
    else setDragOffset(0);
  }, [handleDismiss, busy]);

  const inputStyle: React.CSSProperties = {
    minWidth: 0,
    height: 42,
    padding: '0 14px',
    borderRadius: 12,
    border: '1px solid rgba(255,255,255,0.1)',
    background: 'rgba(255,255,255,0.07)',
    color: '#fff',
    fontSize: 16, // ≥16px keeps iOS from zooming on focus
    outline: 'none',
    WebkitAppearance: 'none',
  };

  const header = (
    <div data-sheet-handle style={{ flexShrink: 0, padding: isCompact ? '0 16px 12px' : '20px 24px 14px', zoom, touchAction: 'none' }}>
      <div style={{ textAlign: 'center', fontSize: 17, fontWeight: 700, color: '#fff', marginBottom: 14 }}>Fix match</div>
      <form
        onSubmit={(e) => { e.preventDefault(); (document.activeElement as HTMLElement | null)?.blur(); runSearch(qTitle, qYear); }}
        style={{ display: 'flex', gap: 8 }}
      >
        <input
          aria-label="Title"
          value={qTitle}
          onChange={(e) => setQTitle(e.target.value)}
          enterKeyHint="search"
          autoCorrect="off"
          autoCapitalize="words"
          spellCheck={false}
          style={{ ...inputStyle, flex: 1 }}
        />
        <input
          aria-label="Year"
          value={qYear}
          onChange={(e) => setQYear(e.target.value.replace(/\D/g, '').slice(0, 4))}
          inputMode="numeric"
          enterKeyHint="search"
          placeholder="Year"
          style={{ ...inputStyle, width: 78, flex: 'none', textAlign: 'center', padding: '0 8px' }}
        />
        <button type="submit" style={{ display: 'none' }} aria-hidden="true" tabIndex={-1} />
      </form>
    </div>
  );

  const list = (
    <div style={{ padding: isCompact ? '0 8px' : '0 12px', zoom }}>
      {error && (
        <div style={{ padding: '10px 8px 4px', textAlign: 'center', fontSize: 14, color: RED }}>{error}</div>
      )}
      {searching && !results && (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '44px 0 60px' }}><Spinner /></div>
      )}
      {results && results.length === 0 && !searching && (
        <div style={{ padding: '40px 0 56px', textAlign: 'center', fontSize: 15, color: 'rgba(255,255,255,0.5)' }}>No matches</div>
      )}
      {results && results.length > 0 && (
        <div style={{ opacity: searching ? 0.55 : 1, transition: 'opacity 0.2s' }}>
          {results.map((c) => {
            const isApplying = applying === c.guid;
            return (
              <button
                key={c.guid}
                type="button"
                data-match-row
                disabled={busy}
                onClick={() => apply(c)}
                style={{
                  width: '100%',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 14,
                  padding: '10px 8px',
                  border: 'none',
                  borderRadius: 14,
                  background: c.current ? 'rgba(255,255,255,0.07)' : 'transparent',
                  color: '#fff',
                  textAlign: 'left',
                  cursor: busy ? 'default' : 'pointer',
                  opacity: busy && !isApplying ? 0.45 : 1,
                  WebkitTapHighlightColor: 'transparent',
                  marginBottom: 2,
                }}
              >
                <Poster src={c.thumb} w={isCompact ? 48 : 52} />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: 'block', fontSize: 16, fontWeight: 600, lineHeight: 1.3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</span>
                  <span style={{ display: 'block', fontSize: 14, color: 'rgba(255,255,255,0.55)', marginTop: 2 }}>
                    {c.year ?? '—'}
                    {c.current && <span style={{ color: '#4ade80', fontWeight: 600 }}>{'  ·  Current'}</span>}
                  </span>
                </span>
                <span style={{ width: 24, display: 'flex', justifyContent: 'center', flexShrink: 0 }}>
                  {isApplying ? <Spinner size={20} /> : c.current ? (
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#4ade80" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12" /></svg>
                  ) : null}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {/* Unmatch — two-step confirm */}
      <div style={{ display: 'flex', justifyContent: 'center', gap: 10, padding: '18px 8px 6px' }}>
        {!confirmUnmatch ? (
          <button
            type="button"
            data-unmatch
            disabled={busy}
            onClick={() => setConfirmUnmatch(true)}
            style={{ border: 'none', background: 'transparent', color: RED, fontSize: 15, fontWeight: 600, padding: '10px 18px', borderRadius: 999, cursor: 'pointer', opacity: busy ? 0.4 : 1 }}
          >
            Unmatch
          </button>
        ) : (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirmUnmatch(false)}
              style={{ border: 'none', background: 'rgba(255,255,255,0.08)', color: '#fff', fontSize: 15, fontWeight: 600, padding: '10px 20px', borderRadius: 999, cursor: 'pointer' }}
            >
              Cancel
            </button>
            <button
              type="button"
              data-unmatch-confirm
              disabled={busy}
              onClick={doUnmatch}
              style={{ border: 'none', background: RED, color: '#fff', fontSize: 15, fontWeight: 700, padding: '10px 20px', borderRadius: 999, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8 }}
            >
              {applying === '__unmatch__' && <Spinner size={16} />}
              Unmatch
            </button>
          </>
        )}
      </div>
      <div style={{ height: isCompact ? 'calc(env(safe-area-inset-bottom, 0px) + 16px)' : 18 }} />
    </div>
  );

  const scroller = (
    <div
      data-scroll-container
      className="luma-scroll-y"
      style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden', WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}
    >
      {list}
    </div>
  );

  const scrimVisible = entered && !dismissing;
  const keyframes = <style>{`@keyframes fixMatchSpin { to { transform: rotate(360deg); } }`}</style>;

  const content = isCompact ? (
    <div className="luma-bleed"
      data-fix-match-sheet
      role="dialog"
      aria-modal="true"
      aria-label="Fix match"
      onClick={(e) => { if (e.target === e.currentTarget) handleDismiss(); }}
      style={{
        position: 'fixed', inset: 0, zIndex: 10070, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
        background: scrimVisible ? 'rgba(0,0,0,0.45)' : 'transparent', transition: 'background 0.25s ease',
      }}
    >
      <div className="luma-bleed-panel"
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        style={{
          height: 'calc(100% - max(env(safe-area-inset-top, 0px), 20px) - 24px)',
          display: 'flex', flexDirection: 'column', background: BG, borderRadius: '20px 20px 0 0',
          boxShadow: '0 -12px 40px rgba(0,0,0,0.45), var(--luma-bleed-shadow)', overflow: 'hidden',
          transform: `translateY(${dismissing ? '100%' : entered ? `${dragOffset}px` : '100%'})`,
          transition: dragOffset > 0 ? 'none' : 'transform 0.3s cubic-bezier(0.32, 0.72, 0, 1)',
          willChange: 'transform',
        }}
      >
        <div data-sheet-handle style={{ flexShrink: 0, height: 26, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'grab', touchAction: 'none' }}>
          <div style={{ width: 40, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)' }} />
        </div>
        {header}
        {scroller}
      </div>
      {keyframes}
    </div>
  ) : (
    <div className="luma-bleed"
      data-fix-match-sheet
      role="dialog"
      aria-modal="true"
      aria-label="Fix match"
      onClick={(e) => { if (e.target === e.currentTarget) handleDismiss(); }}
      style={{
        position: 'fixed', inset: 0, zIndex: 10070, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
        background: scrimVisible ? 'rgba(0,0,0,0.6)' : 'transparent', transition: 'background 0.2s ease',
      }}
    >
      <div
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        style={{
          width: '100%', maxWidth: Math.round(540 * zoom), height: 'min(82vh, 860px)', display: 'flex', flexDirection: 'column',
          background: BG, borderRadius: 20, border: '1px solid rgba(255,255,255,0.08)', boxShadow: '0 24px 80px rgba(0,0,0,0.6)', overflow: 'hidden',
          opacity: scrimVisible ? 1 : 0,
          transform: scrimVisible ? `translateY(${dragOffset}px) scale(1)` : 'scale(0.97)',
          transition: dragOffset > 0 ? 'none' : 'opacity 0.2s ease, transform 0.2s ease',
        }}
      >
        {header}
        {scroller}
      </div>
      {keyframes}
    </div>
  );

  return createPortal(content, document.body);
}
