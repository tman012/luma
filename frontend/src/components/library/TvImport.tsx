/** Settings > TV Import (Designer spec): the Import row and its progress card,
 * the Review card (no match, duplicate and likely folders, each with a show
 * picker and a checkbox) with Import all, and the show picker that "Wrong
 * show?" opens too. Progress polls its own small endpoint, so only the card
 * re-renders while an import runs. */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { T, NAVY_GLASS_POPOVER, NAVY_GLASS_BORDER } from '@/theme/tokens';
import { FONT } from '@/components/start/StartUI';
import { ChevronIcon } from '@/components/nav/AppNav';
import { posterUrl } from '@/utils/image';
import { RATING_ICONS } from '@/utils/ratingIcons';
import {
  lastImportText, loadProgress, reviewTier, searchShows, showLabel, sortReview, startingPick, startsChecked,
  type Candidate, type ImportProgress, type Unmatched,
} from './tvLibraryApi';

export const PROGRESS_POLL_MS = 1000;
export const COLLAPSE_MS = 180;
export const FADE_MS = 200;
export const PICKER_MAX = 5;
export const GREEN = '#22c55e';
export const AMBER = '#f59e0b';
export const RED = '#ef4444';
const GLASS_10 = 'rgba(255,255,255,0.10)';
const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';

export const importCss = `
@keyframes tvi-spin{to{transform:rotate(360deg)}}
.tvi-spin{width:16px;height:16px;border-radius:50%;border:2px solid rgba(255,255,255,.35);border-top-color:#fff;animation:tvi-spin .8s linear infinite;display:inline-block;flex:none}
.tvi-bar{transition:width .3s ease}
.tvi-opt:hover{background:rgba(255,255,255,.05)}
.tvi-trigger:hover{background:rgba(255,255,255,.09)}
@media (prefers-reduced-motion: reduce){.tvi-bar{transition:none}.tvi-spin{animation-duration:2s}}
`;

// ---------------------------------------------------------------- Import row / progress

/** Height follows the content (≤200ms), so the progress card collapses into the Import row. */
function Collapse({ children, testId }: { children: React.ReactNode; testId: string }) {
  const inner = useRef<HTMLDivElement>(null);
  const [h, setH] = useState<number | undefined>(undefined);
  useLayoutEffect(() => {
    const el = inner.current;
    if (!el) return undefined;
    const measure = () => setH(el.offsetHeight);
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  });
  return (
    <div data-testid={testId} style={{ height: h, overflow: 'hidden', transition: `height ${COLLAPSE_MS}ms ease` }}>
      <div ref={inner}>{children}</div>
    </div>
  );
}

export function ImportSection({ wide, running, error, lastImport, onStart, onFinish }: {
  wide: boolean; running: boolean; error: string | null; lastImport: number | null;
  onStart: () => void; onFinish: (p: ImportProgress) => void;
}) {
  return (
    <Collapse testId="tv-import">
      {running ? <ImportProgressCard onFinish={onFinish} />
        : error ? <ImportFailed onRetry={onStart} />
          : (
            <div data-testid="tv-import-idle" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <button type="button" className="lp-focus" data-testid="tv-import-start" onClick={onStart}
                style={{ ...primaryBtn, height: wide ? 40 : 44 }}>Import</button>
              {lastImport ? (
                <span data-testid="tv-last-import" style={{ fontSize: 13, color: W50, fontFamily: FONT }}>
                  {lastImportText(lastImport)}
                </span>
              ) : null}
            </div>
          )}
    </Collapse>
  );
}

function ImportFailed({ onRetry }: { onRetry: () => void }) {
  return (
    <div data-testid="tv-import-error" role="alert" style={{ ...glassCard, display: 'flex', alignItems: 'center', gap: 12 }}>
      <span style={{ flex: 1, fontSize: 13, color: RED, fontFamily: FONT }}>Import failed</span>
      <button type="button" className="lp-focus" data-testid="tv-import-retry" onClick={onRetry} style={retryPill}>Retry</button>
    </div>
  );
}

/** Polls the progress endpoint on its own; the page doesn't re-render until it ends. */
export function ImportProgressCard({ onFinish, initial }: { onFinish: (p: ImportProgress) => void; initial?: ImportProgress }) {
  const [p, setP] = useState<ImportProgress | null>(initial || null);
  const finish = useRef(onFinish);
  finish.current = onFinish;
  useEffect(() => {
    let alive = true;
    let t: number | undefined;
    const tick = async () => {
      const r = await loadProgress();
      if (!alive) return;
      if (r.ok) {
        setP(r.data);
        if (!r.data.running) { finish.current(r.data); return; }
      }
      t = window.setTimeout(tick, PROGRESS_POLL_MS);
    };
    void tick();
    return () => { alive = false; window.clearTimeout(t); };
  }, []);
  const total = p?.total || 0;
  const done = Math.min(p?.done || 0, total);
  const pct = total ? Math.round((done / total) * 1000) / 10 : 0;
  return (
    <div data-testid="tv-import-progress" role="status" aria-live="polite"
      style={{ ...glassCard, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
        <span style={{ flex: 1, fontSize: 15, fontWeight: 600, color: W95, fontFamily: FONT }}>Importing</span>
        <span data-testid="tv-import-count"
          style={{ fontSize: 13, color: W70, fontVariantNumeric: 'tabular-nums', fontFamily: FONT }}>
          {total ? `${done} of ${total}` : ''}
        </span>
      </div>
      <div style={{ height: 4, borderRadius: 999, background: GLASS_10, overflow: 'hidden' }}>
        <div className="tvi-bar" data-testid="tv-import-bar"
          style={{ height: '100%', width: `${pct}%`, borderRadius: 999, background: T.accent }} />
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Chip color={GREEN} label="Matched" n={p?.matched || 0} testId="tv-chip-matched" />
        <Chip color={AMBER} label="Review" n={p?.review || 0} testId="tv-chip-review" />
        <Chip color="rgba(255,255,255,0.3)" label="No match" n={p?.none || 0} testId="tv-chip-none" />
      </div>
    </div>
  );
}

function Chip({ color, label, n, testId }: { color: string; label: string; n: number; testId: string }) {
  return (
    <span data-testid={testId} style={{
      display: 'inline-flex', alignItems: 'center', gap: 6, height: 24, padding: '0 10px', borderRadius: 999,
      background: T.glass, border: `1px solid ${T.glass8}`, fontSize: 12, fontWeight: 600, color: W95, fontFamily: FONT,
      fontVariantNumeric: 'tabular-nums', boxSizing: 'border-box',
    }}>
      <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: 4, background: color, flex: 'none' }} />
      {`${label} ${n}`}
    </span>
  );
}

// ---------------------------------------------------------------- Review

export interface ReviewState {
  checked: Record<number, boolean>; picks: Record<number, Candidate | null>;
  /** Each row's tier when its state was set: a row that changes tier (Import all found its show in another
   * folder: now a duplicate) starts over, unchecked. */
  tiers?: Record<number, string>;
}

export function initialReview(rows: Unmatched[], prev?: ReviewState): ReviewState {
  const checked: Record<number, boolean> = {};
  const picks: Record<number, Candidate | null> = {};
  const tiers: Record<number, string> = {};
  for (const u of rows) {
    const tier = reviewTier(u);
    const had = prev && u.id in prev.picks && (!prev.tiers || prev.tiers[u.id] === undefined || prev.tiers[u.id] === tier);
    picks[u.id] = had ? prev!.picks[u.id]! : startingPick(u);
    checked[u.id] = had ? !!prev!.checked[u.id] : startsChecked(u);
    tiers[u.id] = tier;
  }
  return { checked, picks, tiers };
}

export function ReviewSection({ wide, rows, state, setState, busy, fading, onImportAll }: {
  wide: boolean; rows: Unmatched[]; state: ReviewState; setState: (f: (s: ReviewState) => ReviewState) => void;
  busy: boolean; fading: Set<number>; onImportAll: () => void;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const sorted = sortReview(rows);
  const nChecked = sorted.filter(u => state.checked[u.id] && state.picks[u.id]).length;
  const off = busy || nChecked === 0;
  return (
    <div data-testid="tv-review">
      <h3 style={{ margin: '0 4px 8px', display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, fontWeight: 600, color: W50, fontFamily: FONT }}>
        Review
        <span data-testid="tv-review-count" style={{
          display: 'inline-flex', alignItems: 'center', height: 20, padding: '0 7px', borderRadius: 999, boxSizing: 'border-box',
          background: 'rgba(245,158,11,0.16)', color: AMBER, fontSize: 12, fontWeight: 600, fontVariantNumeric: 'tabular-nums',
        }}>{sorted.length}</span>
      </h3>
      <div style={{ borderRadius: 16, background: T.glass, border: `1px solid ${T.glass8}` }}>
        {sorted.map((u, i) => (
          <ReviewRow key={u.id} u={u} wide={wide} first={i === 0} checked={!!state.checked[u.id]} pick={state.picks[u.id] || null}
            fading={fading.has(u.id)} disabled={busy} open={open === u.id}
            onOpen={() => setOpen(open === u.id ? null : u.id)} onClose={() => setOpen(null)}
            onCheck={on => setState(s => ({ ...s, checked: { ...s.checked, [u.id]: on } }))}
            onPick={c => { setState(s => ({ checked: { ...s.checked, [u.id]: true }, picks: { ...s.picks, [u.id]: c } })); setOpen(null); }} />
        ))}
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
        <button type="button" className="lp-focus" data-testid="tv-import-all" disabled={off} aria-busy={busy}
          onClick={onImportAll}
          style={{
            ...primaryBtn, height: wide ? 40 : 48, minWidth: wide ? 160 : undefined, width: wide ? undefined : '100%',
            opacity: nChecked === 0 ? 0.4 : 1, cursor: off ? 'default' : 'pointer',
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8,
          }}>
          {busy && <span className="tvi-spin" data-testid="tv-import-all-spinner" aria-hidden="true" />}
          Import all
        </button>
      </div>
    </div>
  );
}

function ReviewRow({ u, wide, first, checked, pick, fading, disabled, open, onOpen, onClose, onCheck, onPick }: {
  u: Unmatched; wide: boolean; first: boolean; checked: boolean; pick: Candidate | null; fading: boolean; disabled: boolean;
  open: boolean; onOpen: () => void; onClose: () => void; onCheck: (on: boolean) => void; onPick: (c: Candidate) => void;
}) {
  const dup = reviewTier(u) === 'duplicate';
  const anchor = useRef<HTMLButtonElement>(null);
  const box = (
    <Checkbox on={checked} label={u.folder} disabled={disabled || !pick} onChange={onCheck} big={!wide} />
  );
  const trigger = (
    <PickerTrigger ref={anchor} pick={pick} wide={wide} open={open} disabled={disabled} onClick={onOpen} />
  );
  const rowStyle: React.CSSProperties = {
    position: 'relative', ...(first ? null : { borderTop: '1px solid rgba(255,255,255,0.06)' }),
    opacity: fading ? 0 : 1, transition: `opacity ${FADE_MS}ms ease`,
  };
  const picker = open && (
    <ShowPicker wide={wide} anchor={anchor} initial={u.guess_title || u.folder} year={u.guess_year}
      suggestions={u.candidates} current={pick?.tmdb_id ?? null} onPick={onPick} onClose={onClose} />
  );
  if (wide) {
    return (
      <div data-testid="tv-review-row" data-tier={reviewTier(u)} style={{ ...rowStyle, display: 'flex', alignItems: 'center', gap: 16, minHeight: 64, padding: '12px 16px', boxSizing: 'border-box' }}>
        {box}
        <span data-testid="tv-review-folder" title={u.folder} style={{
          width: 280, flex: 'none', fontSize: 14, fontWeight: 500, color: W95, fontFamily: FONT,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>{u.folder}</span>
        <span style={{ flex: 1, minWidth: 0, maxWidth: 520 }}>{trigger}</span>
        {dup && <span data-testid="tv-duplicate" style={{ fontSize: 12, color: AMBER, fontFamily: FONT, flex: 'none', marginLeft: 'auto' }}>Duplicate</span>}
        {picker}
      </div>
    );
  }
  return (
    <div data-testid="tv-review-row" data-tier={reviewTier(u)} style={{ ...rowStyle, padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, minWidth: 0 }}>
        {box}
        <span data-testid="tv-review-folder" title={u.folder} style={{
          flex: 1, minWidth: 0, fontSize: 13, color: W70, fontFamily: FONT, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>{u.folder}</span>
        {dup && <span data-testid="tv-duplicate" style={{ fontSize: 12, color: AMBER, fontFamily: FONT, flex: 'none' }}>Duplicate</span>}
      </div>
      {trigger}
      {picker}
    </div>
  );
}

function Checkbox({ on, label, disabled, onChange, big }: {
  on: boolean; label: string; disabled: boolean; onChange: (on: boolean) => void; big: boolean;
}) {
  const hit = big ? 44 : 32;
  return (
    <button type="button" role="checkbox" aria-checked={on} aria-label={label} className="lp-focus" data-testid="tv-review-check"
      disabled={disabled} onClick={() => onChange(!on)}
      style={{
        width: hit, height: hit, margin: big ? '-12px 0 -12px -12px' : -6, padding: 0, border: 'none', background: 'none',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none',
        cursor: disabled ? 'default' : 'pointer', borderRadius: 8,
      }}>
      <span aria-hidden="true" style={{
        width: 20, height: 20, borderRadius: 6, boxSizing: 'border-box', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        background: on ? T.accent : 'transparent', border: on ? 'none' : '1.5px solid rgba(255,255,255,0.3)',
      }}>
        {on && (
          <svg width="12" height="12" viewBox="0 0 12 12"><path d="M2.5 6.2l2.3 2.3 4.7-5" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
        )}
      </span>
    </button>
  );
}

/** A poster; without one, a glass tile (with the TMDB logo when tmdbLogo). */
export function Poster({ path, w, h, tmdbLogo }: { path?: string | null; w: number; h: number; tmdbLogo?: boolean }) {
  const src = path ? posterUrl(path, 'w185') : null;
  if (src) return <img src={src} alt="" width={w} height={h} loading="lazy" style={{ width: w, height: h, borderRadius: 4, objectFit: 'cover', flex: 'none', background: GLASS_10 }} />;
  return (
    <span aria-hidden="true" data-testid="tv-poster-tile"
      style={{ width: w, height: h, borderRadius: 4, flex: 'none', background: GLASS_10, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
      {tmdbLogo && <img src={RATING_ICONS.tmdb} alt="" style={{ width: Math.round(w * 0.7), height: 'auto', opacity: 0.8 }} />}
    </span>
  );
}

const PickerTrigger = ({ ref, pick, wide, open, disabled, onClick }: {
  ref: React.Ref<HTMLButtonElement>; pick: Candidate | null; wide: boolean; open: boolean; disabled: boolean; onClick: () => void;
}) => (
  <button ref={ref} type="button" className="tvi-trigger lp-focus" data-testid="tv-picker-trigger" aria-haspopup="dialog" aria-expanded={open}
    disabled={disabled} onClick={onClick}
    style={{
      display: 'flex', alignItems: 'center', gap: 10, width: '100%', height: wide ? 40 : 44, padding: '0 10px 0 8px',
      borderRadius: 10, background: T.glass, border: `1px solid ${T.glass12}`, color: W95, fontFamily: FONT,
      cursor: disabled ? 'default' : 'pointer', boxSizing: 'border-box', textAlign: 'left', overflow: 'hidden',
    }}>
    {pick ? <Poster path={pick.poster_path} w={24} h={36} /> : null}
    <span style={{
      flex: 1, minWidth: 0, fontSize: 14, fontWeight: pick ? 500 : 400, color: pick ? W95 : W50,
      whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', paddingLeft: pick ? 0 : 4,
    }}>{pick ? showLabel(pick) : 'Choose show'}</span>
    <span aria-hidden="true" style={{ display: 'inline-flex', color: W50, transform: 'rotate(90deg)', flex: 'none' }}><ChevronIcon size={14} /></span>
  </button>
);

// ---------------------------------------------------------------- picker

/** Desktop: a 480px popover under its trigger. Phone: a bottom sheet (≤70% tall).
 * Up to 5 shows; a search (name or tmdb:/tvdb:/imdb:) replaces them. Esc or a
 * tap outside closes it. */
export function ShowPicker({ wide, anchor, initial, year, suggestions, current, onPick, onClose }: {
  wide: boolean; anchor: React.RefObject<HTMLElement | null>; initial: string; year: number | null;
  suggestions: Candidate[]; current: number | null; onPick: (c: Candidate) => void; onClose: () => void;
}) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState<Candidate[]>(suggestions);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const panel = useRef<HTMLDivElement>(null);
  const seq = useRef(0);
  const search = useCallback(async (text: string, y: number | null) => {
    const t = text.trim();
    if (!t) return;
    const mine = ++seq.current;
    setBusy(true);
    const r = await searchShows(t, y);
    if (mine !== seq.current) return;
    setBusy(false);
    if (r.ok) { setResults(r.data.results); setError(''); } else setError(r.error);
  }, []);
  useEffect(() => {
    if (!suggestions.length) void search(initial, year);
    // first open only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
    const down = (e: PointerEvent) => {
      const t = e.target as Node;
      if (panel.current?.contains(t) || anchor.current?.contains(t)) return;
      onClose();
    };
    document.addEventListener('keydown', key, true);
    document.addEventListener('pointerdown', down, true);
    return () => { document.removeEventListener('keydown', key, true); document.removeEventListener('pointerdown', down, true); };
  }, [onClose, anchor]);
  useEffect(() => {                       // typing searches after a short pause
    if (!q.trim()) return undefined;
    const t = window.setTimeout(() => void search(q, null), 350);
    return () => window.clearTimeout(t);
  }, [q, search]);

  const [up, setUp] = useState(false);     // desktop: opens upward when there's no room below
  useLayoutEffect(() => {
    if (!wide || !panel.current) return;
    const b = panel.current.getBoundingClientRect();
    const a = anchor.current?.getBoundingClientRect();
    if (b.bottom > window.innerHeight && a && a.top - b.height - 8 > 0) setUp(true);
  }, [wide, anchor, results]);
  const list = results.slice(0, PICKER_MAX);
  const body = (
    <>
      <input data-testid="tv-picker-search" aria-label="Search" placeholder="Search" value={q} autoFocus
        onChange={e => setQ(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); void search(q, null); } }}
        style={{
          width: '100%', height: 40, boxSizing: 'border-box', padding: '0 12px', borderRadius: 10, outline: 'none',
          background: 'rgba(255,255,255,0.06)', border: `1px solid ${T.glass12}`, color: '#fff', fontSize: 15, fontFamily: FONT,
          colorScheme: 'dark',
        }} />
      {error && <div role="alert" style={{ fontSize: 13, color: RED, fontFamily: FONT, padding: '0 4px' }}>{error}</div>}
      <div role="listbox" aria-label="Shows" style={{ display: 'flex', flexDirection: 'column', overflowY: 'auto', minHeight: 0 }}>
        {list.map(c => {
          const on = c.tmdb_id === current;
          return (
            <button key={c.tmdb_id} type="button" role="option" aria-selected={on} className="tvi-opt lp-focus" data-testid="tv-picker-option"
              onClick={() => onPick(c)}
              style={{
                display: 'flex', alignItems: 'center', gap: 12, width: '100%', minHeight: 60, padding: '6px 8px', border: 'none',
                background: 'none', borderRadius: 10, cursor: 'pointer', textAlign: 'left', fontFamily: FONT, boxSizing: 'border-box',
              }}>
              <Poster path={c.poster_path} w={32} h={48} />
              <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
                <span style={{ fontSize: 14, fontWeight: 500, color: W95, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.title}</span>
                {c.year ? <span style={{ fontSize: 13, color: W50 }}>{c.year}</span> : null}
              </span>
              {on && (
                <svg data-testid="tv-picker-current" width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" style={{ flex: 'none' }}>
                  <path d="M4 9.4l3.3 3.3L14 6" fill="none" stroke="#3b82f6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
            </button>
          );
        })}
        {busy && !list.length && <span className="tvi-spin" style={{ margin: '16px auto' }} aria-hidden="true" />}
      </div>
    </>
  );
  if (!wide) {
    return createPortal(
      <div className="luma-bleed" data-testid="tv-picker-sheet-backdrop" style={{ position: 'fixed', inset: 0, zIndex: 900, background: 'rgba(0,0,0,0.45)' }}>
        <div className="luma-bleed" ref={panel} role="dialog" aria-label="Choose show" data-testid="tv-picker"
          style={{
            position: 'absolute', left: 0, right: 0, bottom: 0, maxHeight: '70vh', display: 'flex', flexDirection: 'column', gap: 12,
            padding: '16px 16px calc(16px + env(safe-area-inset-bottom, 0px))', boxSizing: 'border-box',
            borderRadius: '20px 20px 0 0', background: NAVY_GLASS_POPOVER, border: NAVY_GLASS_BORDER, borderBottom: 'none',
            backdropFilter: 'blur(24px) saturate(160%)', WebkitBackdropFilter: 'blur(24px) saturate(160%)',
          }}>
          {body}
        </div>
      </div>,
      document.body,
    );
  }
  return (
    <div ref={panel} role="dialog" aria-label="Choose show" data-testid="tv-picker"
      style={{
        position: 'absolute', zIndex: 50, left: popoverLeft(anchor.current),
        ...(up ? { bottom: '100%', marginBottom: -6 } : { top: '100%', marginTop: -6 }),
        width: POPOVER_W, maxWidth: 'calc(100vw - 32px)', display: 'flex', flexDirection: 'column', gap: 8, padding: 12, boxSizing: 'border-box',
        borderRadius: 14, background: NAVY_GLASS_POPOVER, border: NAVY_GLASS_BORDER, boxShadow: '0 16px 48px rgba(0,0,0,0.5)',
        backdropFilter: 'blur(24px) saturate(160%)', WebkitBackdropFilter: 'blur(24px) saturate(160%)',
      }}>
      {body}
    </div>
  );
}

/** Under its trigger, kept inside the row (a trigger on the right opens leftward). */
export function popoverLeft(a: HTMLElement | null): number {
  if (!a) return 0;
  const room = (a.offsetParent as HTMLElement | null)?.clientWidth ?? 0;
  return Math.max(0, room ? Math.min(a.offsetLeft, room - POPOVER_W) : a.offsetLeft);
}
export const POPOVER_W = 480;

// ---------------------------------------------------------------- styles

const glassCard: React.CSSProperties = {
  padding: 16, borderRadius: 16, background: T.glass, border: `1px solid ${T.glass8}`, boxSizing: 'border-box',
};
const primaryBtn: React.CSSProperties = {
  padding: '0 22px', borderRadius: 10, border: 'none', background: T.accent, color: '#fff',
  fontSize: 15, fontWeight: 600, fontFamily: FONT, cursor: 'pointer', flex: 'none',
};
const retryPill: React.CSSProperties = {
  height: 32, padding: '0 14px', borderRadius: 999, background: T.glass, border: `1px solid ${T.glass12}`,
  color: '#fff', fontSize: 13, fontWeight: 600, fontFamily: FONT, cursor: 'pointer', flex: 'none',
};
