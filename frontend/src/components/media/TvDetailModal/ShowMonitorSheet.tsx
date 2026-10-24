/**
 * Show-level monitor prompt (owner only): Entire Series, Future Episodes,
 * Choose Seasons, Unmonitor. Bottom sheet on phone (swipe down to close),
 * centered panel on tablet/desktop.
 */
import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { SonarrStatusContext, type SeriesMonitorMode } from './SonarrStatus';
import { seasonCustomName, seasonNumberLabel, seasonShortLabel } from './seasonLabels';

const NAVY = '#141a2e';
const ROW_BG = 'rgba(255,255,255,0.06)';
const SEP = '1px solid rgba(255,255,255,0.07)';

function Icon({ d, size = 20 }: { d: React.ReactNode; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
      {d}
    </svg>
  );
}

const ICONS = {
  all: <><rect x="2" y="3" width="20" height="4" rx="1" /><rect x="2" y="10" width="20" height="4" rx="1" /><rect x="2" y="17" width="20" height="4" rx="1" /></>,
  future: <><circle cx="12" cy="12" r="9" /><polyline points="12 7 12 12 15.5 14" /></>,
  choose: <><polyline points="9 11 12 14 20 6" /><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9" /></>,
  off: <><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" /><line x1="3" y1="3" x2="21" y2="21" /></>,
};

export function ShowMonitorSheet({ onClose }: { onClose: () => void }) {
  const ctx = useContext(SonarrStatusContext);
  const { mode: layout } = useLayoutMode();
  const phone = layout === 'compact';
  const st = ctx.data;
  const [entered, setEntered] = useState(false);
  const [closing, setClosing] = useState(false);
  const [busy, setBusy] = useState<SeriesMonitorMode | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [drag, setDrag] = useState(0);
  const dragStart = useRef<number | null>(null);

  const seriesOn = !!st?.series?.monitored;
  const rows = useMemo(() => {
    const info = new Map((ctx.seasonInfo ?? []).map((x) => [x.season, x]));
    const list = (st?.seasons ?? []).map((x) => {
      const i = info.get(x.season);
      return {
        season: x.season,
        name: i?.name ?? null,
        have: i?.have ?? x.downloaded,
        total: i?.total ?? x.total,
        on: seriesOn && x.monitored,
      };
    });
    return [...list.filter((x) => x.season > 0), ...list.filter((x) => x.season === 0)];
  }, [ctx.seasonInfo, st?.seasons, seriesOn]);
  const initial = useMemo(() => new Set(rows.filter((r) => r.on).map((r) => r.season)), [rows]);
  const [picked, setPicked] = useState<Set<number>>(initial);
  const changed = picked.size !== initial.size || [...picked].some((n) => !initial.has(n));
  const anyMonitored = seriesOn;

  useEffect(() => { requestAnimationFrame(() => setEntered(true)); }, []);

  const dismiss = useCallback(() => {
    setClosing(true);
    setTimeout(onClose, 220);
  }, [onClose]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopImmediatePropagation(); dismiss(); }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [dismiss]);

  const run = async (m: SeriesMonitorMode, seasons?: number[]) => {
    if (!ctx.setSeriesMonitorMode || busy) return;
    setBusy(m);
    const ok = await ctx.setSeriesMonitorMode(m, seasons);
    setBusy(null);
    if (ok) dismiss();
  };

  const toggle = (n: number) => setPicked((prev) => {
    const next = new Set(prev);
    if (next.has(n)) next.delete(n); else next.add(n);
    return next;
  });

  const visible = entered && !closing;
  const title = ctx.showTitle || st?.series?.title || '';

  const row = (key: SeriesMonitorMode | 'choose', label: string, icon: React.ReactNode, onClick: () => void, opts: { danger?: boolean; last?: boolean; chevron?: boolean } = {}) => (
    <button
      key={key}
      type="button"
      data-monitor-option={key}
      disabled={!!busy}
      onClick={onClick}
      style={{
        width: '100%', display: 'flex', alignItems: 'center', gap: 14,
        padding: '15px 16px', background: 'transparent', border: 'none',
        borderBottom: opts.last ? 'none' : SEP,
        color: opts.danger ? '#ff6b6b' : '#fff', fontSize: 16, fontWeight: 500, fontFamily: 'inherit',
        textAlign: 'left', cursor: 'pointer', opacity: busy && busy !== key ? 0.45 : 1,
        WebkitTapHighlightColor: 'transparent',
      }}
    >
      <span style={{ color: opts.danger ? '#ff6b6b' : 'rgba(255,255,255,0.75)', display: 'flex' }}><Icon d={icon} /></span>
      <span style={{ flex: 1 }}>{label}</span>
      {busy === key && <Spinner />}
      {opts.chevron && busy !== key && (
        <span style={{ color: 'rgba(255,255,255,0.35)', display: 'flex', transform: choosing ? 'rotate(90deg)' : 'none', transition: 'transform 0.2s ease' }}>
          <Icon size={16} d={<polyline points="9 18 15 12 9 6" />} />
        </span>
      )}
    </button>
  );

  const content = (
    <>
      <h3 data-monitor-sheet-title style={{
        margin: '0 0 16px', textAlign: 'center', fontSize: 17, fontWeight: 700, color: '#fff',
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', padding: '0 8px',
      }}>
        {title}
      </h3>
      <div style={{ background: ROW_BG, borderRadius: 14, overflow: 'hidden' }}>
        {row('all', 'Entire Series', ICONS.all, () => run('all'))}
        {row('future', 'Future Episodes', ICONS.future, () => run('future'))}
        {row('choose', 'Choose Seasons', ICONS.choose, () => setChoosing((v) => !v), { last: !choosing, chevron: true })}
        {choosing && (
          <div data-season-checklist style={{ borderTop: SEP }}>
            <div style={{ maxHeight: phone ? '38vh' : 320, overflowY: 'auto', WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain' }}>
              {rows.map((r, i) => {
                const on = picked.has(r.season);
                const custom = seasonCustomName(r.name, r.season);
                return (
                  <button
                    key={r.season}
                    type="button"
                    data-season-check={r.season}
                    aria-pressed={on}
                    onClick={() => toggle(r.season)}
                    style={{
                      width: '100%', display: 'flex', alignItems: 'center', gap: 12,
                      padding: '11px 16px 11px 18px', background: 'transparent', border: 'none',
                      borderBottom: i === rows.length - 1 ? 'none' : SEP,
                      color: '#fff', fontFamily: 'inherit', textAlign: 'left', cursor: 'pointer',
                      WebkitTapHighlightColor: 'transparent',
                    }}
                  >
                    <span style={{
                      width: 22, height: 22, borderRadius: '50%', flexShrink: 0,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      background: on ? '#fff' : 'transparent',
                      border: on ? '1.5px solid #fff' : '1.5px solid rgba(255,255,255,0.35)',
                      color: NAVY, transition: 'all 0.15s ease',
                    }}>
                      {on && <Icon size={14} d={<polyline points="20 6 9 17 4 12" />} />}
                    </span>
                    <span style={{ flex: 1, minWidth: 0, fontSize: 15, fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {custom ? <><span style={{ color: 'rgba(255,255,255,0.5)', fontWeight: 600 }}>{seasonShortLabel(r.season)}</span>{'  '}{custom}</> : seasonNumberLabel(r.season)}
                    </span>
                    {r.total > 0 && (
                      <span style={{ fontSize: 13, fontWeight: 600, color: r.have >= r.total ? '#4ade80' : r.have > 0 ? '#fbbf24' : 'rgba(255,255,255,0.4)', flexShrink: 0 }}>
                        {r.have}/{r.total}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
            <div style={{ padding: '12px 16px 16px' }}>
              <button
                type="button"
                data-monitor-option="seasons"
                disabled={!changed || !!busy}
                onClick={() => run('seasons', [...picked].sort((a, b) => a - b))}
                style={{
                  width: '100%', padding: '13px', borderRadius: 999, border: 'none',
                  background: '#fff', color: '#0b0f1c', fontSize: 15, fontWeight: 700, fontFamily: 'inherit',
                  cursor: changed ? 'pointer' : 'default', opacity: changed && !busy ? 1 : 0.4,
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
                }}
              >
                {busy === 'seasons' && <Spinner dark />}
                Monitor
              </button>
            </div>
          </div>
        )}
      </div>
      {anyMonitored && (
        <div style={{ background: ROW_BG, borderRadius: 14, overflow: 'hidden', marginTop: 12 }}>
          {row('off', 'Unmonitor', ICONS.off, () => run('off'), { danger: true, last: true })}
        </div>
      )}
    </>
  );

  const backdrop = (
    <div className="luma-fullscreen luma-bleed"
      onClick={(e) => { e.stopPropagation(); dismiss(); }}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
        opacity: visible ? 1 : 0, transition: 'opacity 0.22s ease',
      }}
    />
  );

  const stop = (e: React.SyntheticEvent) => e.stopPropagation();

  if (phone) {
    return createPortal(
      <div className="luma-fullscreen luma-bleed" data-show-monitor-sheet style={{ position: 'fixed', inset: 0, zIndex: 10040 }} onClick={stop} onTouchStart={stop} onTouchEnd={stop}>
        {backdrop}
        <div className="luma-bleed-panel"
          onTouchStart={(e) => {
            const t = e.target as HTMLElement;
            if (t.closest('[data-season-checklist]')) return;
            dragStart.current = e.touches[0]?.clientY ?? null;
          }}
          onTouchMove={(e) => {
            if (dragStart.current == null) return;
            setDrag(Math.max(0, (e.touches[0]?.clientY ?? 0) - dragStart.current));
          }}
          onTouchEnd={() => {
            if (dragStart.current == null) return;
            dragStart.current = null;
            if (drag > 90) dismiss(); else setDrag(0);
          }}
          style={{
            position: 'absolute', left: 0, right: 0, bottom: 0,
            background: NAVY, borderRadius: '18px 18px 0 0', ['--luma-panel-bg' as string]: NAVY,
            padding: '8px 16px calc(env(safe-area-inset-bottom, 0px) + 20px)',
            boxShadow: '0 -12px 40px rgba(0,0,0,0.5), var(--luma-bleed-shadow)',
            transform: visible ? `translateY(${drag}px)` : 'translateY(100%)',
            transition: dragStart.current != null ? 'none' : 'transform 0.25s cubic-bezier(0.2, 0.8, 0.2, 1)',
            maxHeight: '88vh', overflowY: 'auto',
          }}
        >
          <div data-sheet-handle style={{ display: 'flex', justifyContent: 'center', padding: '4px 0 14px' }}>
            <div style={{ width: 36, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.25)' }} />
          </div>
          {content}
        </div>
      </div>,
      document.body,
    );
  }

  return createPortal(
    <div className="luma-fullscreen luma-bleed" data-show-monitor-sheet style={{ position: 'fixed', inset: 0, zIndex: 10040, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={stop}>
      {backdrop}
      <div style={{
        position: 'relative', width: 'min(420px, 92vw)', background: NAVY, borderRadius: 20,
        padding: '22px 18px 18px', boxShadow: '0 24px 60px rgba(0,0,0,0.55)',
        border: '1px solid rgba(255,255,255,0.08)',
        opacity: visible ? 1 : 0, transform: visible ? 'scale(1)' : 'scale(0.96)',
        transition: 'opacity 0.2s ease, transform 0.2s ease',
      }}>
        {content}
      </div>
    </div>,
    document.body,
  );
}

function Spinner({ dark = false }: { dark?: boolean }) {
  return (
    <span style={{
      width: 16, height: 16, borderRadius: '50%', flexShrink: 0,
      border: `2px solid ${dark ? 'rgba(0,0,0,0.2)' : 'rgba(255,255,255,0.2)'}`,
      borderTopColor: dark ? '#0b0f1c' : '#fff',
      animation: 'sonarrRingSpin 0.8s linear infinite',
      display: 'inline-block',
    }}>
      <style>{'@keyframes sonarrRingSpin{from{transform:rotate(0)}to{transform:rotate(360deg)}}'}</style>
    </span>
  );
}
