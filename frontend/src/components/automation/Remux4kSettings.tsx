/** v359 Settings > Automation > 4K & upgrades > 4K Remux auto-grab (prod's Upgrade4kAutoGrabCard, native: Luma's
 * own trackers and qBittorrent, no Radarr). Same layout as Trending TV / Genre roulette. Owner only; changes,
 * Run now and Preview need the signed-in owner. Settings save as they change. */
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { FONT } from '@/components/start/StartUI';
import { Modal, Select, Toggle, SAVE_ERROR, glassBtn, primaryBtn, tvEditCss } from '@/components/library/TvEdit';
import { Field, NumField, grCss, stamp, when } from './GenreRouletteSettings';
import { loadFk, loadFkLive, previewFk, runFk, saveFk, type FkGrab, type FkPreview, type FkResult, type FkSettings, type FkStatus, type FkView } from './remux4kApi';
import { FoldDetails, FoldTitle, onFoldBarClick } from './AutomationFold';

const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';
const RED = '#fca5a5';
const GREEN = '#4ade80';
const panel: CSSProperties = { borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', boxSizing: 'border-box' };
const group: CSSProperties = { margin: '0 0 8px', fontSize: 11, fontWeight: 700, letterSpacing: '0.02em', color: '#a5b4fc' };
const tile: CSSProperties = { borderRadius: 12, background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.06)', padding: '10px 12px', minWidth: 0 };
const pill: CSSProperties = { borderRadius: 20, height: 40, padding: '0 20px' };

/** The server's limits (app/automation/remux4k.py LIMITS). */
const LIMITS = { min_seeders: [1, 5000], max_age_hours: [1, 168], scan_interval_minutes: [5, 120] } as const;
type NumKey = keyof typeof LIMITS;

/** "Checked 120, candidates 3, grabbed 2, already 4K 1, rejected 0" (prod's last-run line). */
export function counts(r: Partial<FkResult> | null | undefined): string {
  if (!r) return '—';
  if (r.error) return r.error;
  const parts = [`Checked ${r.checked ?? 0}`, `candidates ${r.candidates ?? 0}`, `${r.dry ? 'would grab' : 'grabbed'} ${(r.dry ? r.would_grab : r.grabbed) ?? 0}`];
  if (r.skipped_in_4k) parts.push(`already 4K ${r.skipped_in_4k}`);
  if (r.rejected) parts.push(`rejected ${r.rejected}`);
  return parts.join(', ');
}

const arr = <T,>(x: T[] | null | undefined): T[] => (Array.isArray(x) ? x : []);
const isObj = (x: unknown): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x);

export const FK_DEFAULTS: FkSettings = { enabled: false, min_seeders: 100, max_age_hours: 24, scan_interval_minutes: 15, plex_4k_section_id: 0 };

export function normFk(v: unknown): FkView | null {
  if (!isObj(v) || !isObj(v.settings) || !isObj(v.status)) return null;
  const d = v as unknown as FkView;
  const known = Object.fromEntries(Object.entries(d.settings).filter(([, x]) => x !== undefined && x !== null));
  return { ...d, settings: { ...FK_DEFAULTS, ...known } as FkSettings, libraries: arr(d.libraries), history: arr(d.history).filter(isObj), grabs: arr(d.grabs).filter(isObj) };
}

function StatusPanel({ status, grabs }: { status: FkStatus; grabs: FkGrab[] }) {
  const r = status.last_result;
  const state = status.running ? { text: 'Running', color: '#60a5fa' } : status.enabled ? { text: 'Idle', color: GREEN } : { text: 'Off', color: W50 };
  const tiles: [string, string][] = [
    ['Last ran', status.last_run ? when(new Date(status.last_run * 1000)) : 'Never'],
    ['Next scan', status.next_run ? when(new Date(status.next_run * 1000)) : '—'],
  ];
  return (
    <section aria-label="Status" data-testid="fk-status" className="gr-hpanel" style={{ ...panel, padding: '16px 16px 8px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: W95 }}>Status</h3>
        <span data-testid="fk-state" style={{ fontSize: 12, color: state.color }}>● {state.text}</span>
      </div>
      <div className="gr-f2" style={{ gap: 8, marginBottom: 8 }}>
        {tiles.map(([k, v]) => (
          <div key={k} style={tile} data-testid="fk-tile">
            <div style={{ fontSize: 12, color: W70, marginBottom: 4 }}>{k}</div>
            <div style={{ fontSize: 15, color: W95, overflowWrap: 'anywhere' }}>{v}</div>
          </div>
        ))}
      </div>
      <div style={{ ...tile, marginBottom: 18 }} data-testid="fk-last">
        <div style={{ fontSize: 12, color: W70, marginBottom: 4 }}>Last run</div>
        <div style={{ fontSize: 14, color: r?.error ? RED : W95, overflowWrap: 'anywhere' }}>{counts(r)}</div>
        {!!r?.grabbed_movies?.length && <div style={{ fontSize: 12, color: W50, marginTop: 4, overflowWrap: 'anywhere' }}>Grabbed: {r.grabbed_movies.join(', ')}</div>}
      </div>
      <h4 style={group}>Grabbed</h4>
      <div className="gr-hbox"><div role="list" data-testid="fk-history-list" className="gr-hscroll">
        {grabs.length === 0 && <div style={{ padding: '12px 0', fontSize: 13, color: W50 }}>Nothing grabbed yet</div>}
        {grabs.map(g => (
          <div role="listitem" key={g.hash} data-testid="fk-history" title={g.release}
            style={{ borderTop: '1px solid rgba(255,255,255,0.06)', minHeight: 44, display: 'flex', alignItems: 'center', gap: 10, padding: '0 4px', fontSize: 13 }}>
            <span style={{ flex: 1, minWidth: 0, color: W95, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{g.title}</span>
            {g.year && <span style={{ color: W50, fontSize: 12, flex: 'none' }}>{g.year}</span>}
            <span style={{ color: W70, fontSize: 12, flex: 'none' }}>{stamp(new Date(g.grabbed_at * 1000))}</span>
          </div>
        ))}
      </div></div>
    </section>
  );
}

function PreviewList({ preview }: { preview: FkPreview | null }) {
  const r = preview?.result;
  if (!r || preview?.running) return <div aria-busy="true" style={{ padding: '24px 0', fontSize: 14, color: W70 }}>Searching trackers…</div>;
  const rows = arr(r.releases);
  return (
    <div data-testid="fk-preview" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ fontSize: 14, color: r.error ? RED : W95 }}>{counts(r)}</div>
      {rows.length === 0 && <div style={{ fontSize: 13, color: W50 }}>No 2160p remux releases found</div>}
      <div role="list" style={{ maxHeight: 420, overflowY: 'auto' }}>
        {rows.map(x => {
          const good = x.decision === 'would_grab' || x.decision === 'grabbed';
          return (
            <div role="listitem" key={`${x.tracker}-${x.release}`} data-testid="fk-preview-row" data-decision={x.decision ?? ''}
              style={{ borderTop: '1px solid rgba(255,255,255,0.06)', padding: '8px 0', display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span style={{ fontSize: 13, color: W95, overflowWrap: 'anywhere' }}>{x.title ? `${x.title}${x.year ? ` (${x.year})` : ''}` : x.release}</span>
              <span style={{ fontSize: 12, color: W50, overflowWrap: 'anywhere' }}>{x.tracker} · {x.seeders} seeders{x.age_hours != null ? ` · ${x.age_hours} h old` : ''}</span>
              <span style={{ fontSize: 12, color: good ? GREEN : W70 }}>{x.reason}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function Remux4kSettings({ wide = true }: { wide?: boolean } = {}) {
  const [d, setD] = useState<FkView | null>(null);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<FkPreview | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const busy = useRef(false);

  useEffect(() => { void loadFk().then(r => { const v = r.ok ? normFk(r.data) : null; if (v) setD(v); else setFailed(true); }); }, []);
  const live = useCallback(() => {
    void loadFkLive().then(r => {
      if (!r.ok || !isObj(r.data) || !isObj(r.data.status)) return;
      const { status, preview: pv } = r.data;
      setD(cur => (cur ? { ...cur, status } : cur));
      setPreview(isObj(pv) ? pv : null);
      if (isObj(status) && !status.running) void loadFk().then(x => { const v = x.ok ? normFk(x.data) : null; if (v) setD(cur => (cur ? { ...cur, grabs: v.grabs, history: v.history } : v)); });
    });
  }, []);
  const fast = !!d?.status.running || !!preview?.running;
  useEffect(() => {
    if (!d) return undefined;
    const t = window.setInterval(live, fast ? 3000 : 30000);
    return () => window.clearInterval(t);
  }, [!!d, fast, live]); // eslint-disable-line react-hooks/exhaustive-deps

  if (failed) return <div role="alert" style={{ ...panel, height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, color: W50 }}>{"Couldn't load"}</div>;
  if (!d) return <div aria-busy="true" style={{ ...panel, height: 120 }} />;
  const s = d.settings;

  const put = (patch: Partial<FkSettings>) => {
    const before = d;
    setD({ ...d, settings: { ...s, ...patch } });
    setError('');
    void saveFk(patch).then(r => {
      const v = r.ok ? normFk(r.data) : null;
      if (v) { setD(v); return; }
      setD(before);
      setError(SAVE_ERROR);
    });
  };
  const start = (kind: 'run' | 'preview') => {
    if (busy.current) return;
    busy.current = true;
    setError('');
    if (kind === 'preview') { setPreview({ running: true, at: null, result: null }); setShowPreview(true); }
    void (kind === 'run' ? runFk() : previewFk()).then(r => {
      busy.current = false;
      if (!r.ok && r.status !== 409) { setError("Couldn't start"); if (kind === 'preview') setShowPreview(false); }
      live();
    });
  };
  const running = d.status.running;
  const sub = d.status.next_run && d.status.enabled ? `Next scan ${when(new Date(d.status.next_run * 1000))}` : '';
  const libs = [{ id: 0, label: 'Select your 4K library' }, ...d.libraries.map(x => ({ id: x.id, label: x.title }))];
  const num = (k: NumKey) => {
    const [lo, hi] = LIMITS[k];
    return <NumField id={`fk-${k}`} value={s[k]} grouped={k === 'min_seeders'} onCommit={v => (v >= lo && v <= hi ? put({ [k]: v }) : setError(`Must be ${lo} to ${hi}`))} />;
  };

  return (
    <div data-testid="remux-4k" data-open={open ? 'true' : 'false'} style={{ display: 'flex', flexDirection: 'column', gap: 16, fontFamily: FONT, color: W95, padding: wide ? 0 : '8px 16px 24px' }}>
      <style>{tvEditCss}{grCss}</style>
      <section className="gr-head" aria-label="4K Remux auto-grab" onClick={e => onFoldBarClick(e, () => setOpen(v => !v))} style={{ ...panel, display: 'flex', alignItems: 'center', gap: 16, padding: '12px 16px', minHeight: 64 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1, minWidth: 0 }}>
          <Toggle id="fk-enabled" on={s.enabled} onChange={v => put({ enabled: v })} label="4K Remux auto-grab" />
          <FoldTitle id="fk-fold" controls="fk-details" open={open} onToggle={() => setOpen(v => !v)}>
            <h3 id="fk-fold-title" style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>4K Remux auto-grab</h3>
            {sub && <span data-testid="fk-sub" style={{ fontSize: 12, color: W70 }}>{sub}</span>}
          </FoldTitle>
        </div>
        <div className="gr-actions" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button type="button" className="tve-glass lp-focus" data-testid="fk-preview-btn" disabled={running || !!preview?.running} onClick={() => start('preview')}
            style={{ ...glassBtn, ...pill, opacity: running || preview?.running ? 0.5 : 1 }}>Preview</button>
          <button type="button" className="tve-primary lp-focus" data-testid="fk-run" disabled={running} onClick={() => start('run')}
            style={{ ...primaryBtn(!running), ...pill }}>{running ? 'Running' : 'Run now'}</button>
        </div>
      </section>
      {error && <div role="alert" style={{ fontSize: 13, color: RED }}>{error}</div>}
      <FoldDetails id="fk-details" labelledBy="fk-fold-title" open={open}>
      <div className="gr-grid gr-hgrid">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
          <section aria-label="Releases" data-testid="fk-settings" style={{ ...panel, padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
            <Field id="fk-library" label="4K Plex library"><Select id="fk-library" value={s.plex_4k_section_id} options={libs} onChange={v => put({ plex_4k_section_id: Number(v) })} /></Field>
            <div className="gr-f3">
              <Field id="fk-min_seeders" label="Minimum seeders">{num('min_seeders')}</Field>
              <Field id="fk-max_age_hours" label="Max age (hours)">{num('max_age_hours')}</Field>
              <Field id="fk-scan_interval_minutes" label="Scan interval (minutes)">{num('scan_interval_minutes')}</Field>
            </div>
          </section>
        </div>
        <StatusPanel status={d.status} grabs={d.grabs} />
      </div>
      </FoldDetails>
      {showPreview && (
        <Modal title="Preview" testid="fk-preview-modal" onClose={() => setShowPreview(false)}
          footer={<button type="button" className="tve-glass lp-focus" style={glassBtn} onClick={() => setShowPreview(false)}>Close</button>}>
          <PreviewList preview={preview} />
        </Modal>
      )}
    </div>
  );
}
