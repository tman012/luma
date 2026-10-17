/** A section's own tracker list (TV now, Movies later): a switch per tracker
 * and the search order, by drag or ArrowUp/ArrowDown on the handle. Nothing
 * is on until the owner turns it on. */
import { useEffect, useRef, useState } from 'react';
import { T } from '@/theme/tokens';
import { FONT } from '@/components/start/StartUI';
import { NoticeToast } from '@/components/start/SetupToast';
import { GripIcon } from '@/components/nav/AppNav';
import { move } from '@/components/home/homeApi';
import { Switch, trackersCss } from '@/components/trackers/trackersUi';
import {
  blockedReason, loadSectionTrackers, saveSectionTrackers, type Section, type SectionTracker,
} from './sectionTrackersApi';

const ROW_H = 64;

export function SectionTrackersList({ section, label }: { section: Section; label: string }) {
  const [list, setList] = useState<SectionTracker[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [toast, setToast] = useState('');
  const [drag, setDrag] = useState<{ id: number; from: number; startY: number; dy: number } | null>(null);
  const seq = useRef(0);
  const handles = useRef(new Map<number, HTMLButtonElement>());
  const refocus = useRef<number | null>(null);

  useEffect(() => {
    let live = true;
    loadSectionTrackers(section).then(r => {
      if (!live) return;
      if (r.ok) setList(r.trackers); else setLoadFailed(true);
    });
    return () => { live = false; };
  }, [section]);
  useEffect(() => {
    if (refocus.current !== null) { handles.current.get(refocus.current)?.focus(); refocus.current = null; }
  });

  const commit = (next: SectionTracker[]) => {
    const before = list;
    setList(next);
    const n = ++seq.current;
    saveSectionTrackers(section, next).then(r => {
      if (n !== seq.current) return;
      if (r.ok) setList(r.trackers);
      else { setList(before); setToast(r.error); }
    });
  };

  const target = (d: NonNullable<typeof drag>, count: number) =>
    Math.max(0, Math.min(count - 1, d.from + Math.round(d.dy / ROW_H)));
  const shiftFor = (i: number, count: number) => {
    if (!drag) return 0;
    const t = target(drag, count);
    if (drag.from < t && i > drag.from && i <= t) return -ROW_H;
    if (drag.from > t && i >= t && i < drag.from) return ROW_H;
    return 0;
  };

  return (
    <div data-testid={`section-trackers-${section}`} style={{ fontFamily: FONT }}>
      <style>{trackersCss}</style>
      {toast && <NoticeToast key={toast + seq.current} text={toast} onDone={() => setToast('')} />}
      <h3 style={sectionLabel}>{label}</h3>
      {list === null ? (
        loadFailed
          ? <div role="alert" style={{ ...card, ...emptyRow }}><span style={muted}>{"Couldn't load trackers"}</span></div>
          : <div style={card} data-testid="section-trackers-loading">
              {[0, 1].map(i => <div key={i} className="luma-shimmer" style={{ height: ROW_H }} />)}
            </div>
      ) : list.length === 0 ? (
        <div style={{ ...card, ...emptyRow }} data-testid="section-trackers-empty"><span style={muted}>No trackers</span></div>
      ) : (
        <ul style={{ ...card, listStyle: 'none', margin: 0, padding: 0, position: 'relative' }}>
          {list.map((t, i) => {
            const lifted = drag?.id === t.id;
            const y = lifted ? drag!.dy : shiftFor(i, list.length);
            const why = blockedReason(t, section);
            const dim = !t.enabled || !!why;
            return (
              <li key={t.id} data-testid="section-tracker-row" data-lifted={lifted ? 'true' : undefined}
                style={{
                  ...row, ...(i > 0 ? topBorder : null),
                  transform: `translateY(${y}px)${lifted ? ' scale(1.02)' : ''}`,
                  transition: lifted ? 'none' : 'transform 150ms',
                  ...(lifted ? liftedRow : null),
                }}>
                <button type="button" aria-label={`Reorder ${t.name}`} style={handle} className="lt-focus"
                  ref={el => { if (el) handles.current.set(t.id, el); else handles.current.delete(t.id); }}
                  onPointerDown={e => {
                    e.currentTarget.setPointerCapture?.(e.pointerId);
                    setDrag({ id: t.id, from: i, startY: e.clientY, dy: 0 });
                  }}
                  onPointerMove={e => { if (drag?.id === t.id) setDrag({ ...drag, dy: e.clientY - drag.startY }); }}
                  onPointerUp={() => {
                    if (drag?.id !== t.id) return;
                    const to = target(drag, list.length);
                    setDrag(null);
                    if (to !== drag.from) commit(move(list, drag.from, to));
                  }}
                  onPointerCancel={() => setDrag(null)}
                  onKeyDown={e => {
                    const to = e.key === 'ArrowUp' ? i - 1 : e.key === 'ArrowDown' ? i + 1 : null;
                    if (to === null) return;
                    e.preventDefault();
                    if (to < 0 || to >= list.length) return;
                    refocus.current = t.id;
                    commit(move(list, i, to));
                  }}>
                  <GripIcon />
                </button>
                <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <span data-testid="section-tracker-name" style={{ ...name, color: dim ? T.text3 : T.text }}>{t.name}</span>
                  <span style={meta} data-testid="section-tracker-meta">
                    {[t.type_name, why, t.enabled && !why && t.budget ? `${t.budget.used} of ${t.budget.cap} today` : '']
                      .filter(Boolean).join(' · ')}
                  </span>
                </div>
                <Switch on={t.enabled} label={t.name}
                  onChange={on => commit(list.map(x => (x.id === t.id ? { ...x, enabled: on } : x)))} />
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

const sectionLabel: React.CSSProperties = {
  margin: '0 4px 8px', fontSize: 13, fontWeight: 600, color: T.text3, fontFamily: FONT,
};
const card: React.CSSProperties = {
  borderRadius: 16, background: T.glass, border: `1px solid ${T.glass8}`, overflow: 'hidden',
};
const emptyRow: React.CSSProperties = { height: ROW_H, display: 'flex', alignItems: 'center', padding: '0 20px' };
const muted: React.CSSProperties = { color: T.text3, fontSize: 17, fontFamily: FONT };
const topBorder: React.CSSProperties = { borderTop: `1px solid ${T.glass}` };
const row: React.CSSProperties = {
  position: 'relative', display: 'flex', alignItems: 'center', gap: 8, height: ROW_H, padding: '0 16px 0 4px',
  boxSizing: 'border-box', touchAction: 'pan-y', background: 'transparent',
};
const liftedRow: React.CSSProperties = {
  zIndex: 2, background: T.glass12, borderRadius: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
};
const handle: React.CSSProperties = {
  width: 44, height: 44, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none',
  border: 'none', background: 'none', color: T.text4, cursor: 'grab', touchAction: 'none', borderRadius: 10, padding: 0,
};
const name: React.CSSProperties = {
  fontSize: 16, fontWeight: 500, fontFamily: FONT, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
};
const meta: React.CSSProperties = {
  fontSize: 13, color: T.text3, fontFamily: FONT, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
};
