/** Settings > Trackers > Import from Prowlarr (v170, owner only, optional).
 *
 * Nothing happens until the owner asks: the button shows only while a
 * Prowlarr connection is set up, the sheet opens with a preview (one read of
 * Prowlarr's indexer list, credentials stay on the server), and Import adds
 * the ticked trackers turned off, each needing a passing Test before it can
 * be turned on. */
import { useEffect, useMemo, useState } from 'react';
import { previewImport, runImport, needsSignIn, UNREACHABLE, type ImportPreview, type ImportRow, type Tracker } from './trackersApi';
import {
  BLUE, CheckMark, CloseIcon, circle, circleHit, DANGER_TEXT, DIVIDER, FONT, Modal, primaryBtn, secondaryBtn, Spinner,
  TEXT, TEXT_3,
} from './trackersUi';

export const IMPORT_TITLE = 'Import from Prowlarr';
export const AMBER = '#fbbf24';
export const CHIP_TEXT: Record<string, string> = {
  needs_credentials: 'Enter credentials',
  exists: 'Already added',
  unsupported: 'Not supported yet',
  invalid: "Can't import",
};
export const NOTHING_TO_IMPORT = 'Nothing to import';
const PREVIEW_FAILED = "Couldn't reach Prowlarr";

const selectable = (r: ImportRow) => r.state === 'ready' || r.state === 'needs_credentials';

export function StatusChip({ text, amber, testId }: { text: string; amber?: boolean; testId?: string }) {
  return (
    <span data-testid={testId} style={{ flex: 'none', display: 'inline-flex', alignItems: 'center', height: 22, padding: '0 8px',
      borderRadius: 999, fontSize: 12, fontWeight: 500, whiteSpace: 'nowrap',
      color: amber ? AMBER : 'rgba(255,255,255,0.6)',
      background: amber ? 'rgba(251,191,36,0.12)' : 'rgba(255,255,255,0.06)',
      border: amber ? '1px solid rgba(251,191,36,0.3)' : '1px solid rgba(255,255,255,0.10)' }}>{text}</span>
  );
}

function Check({ on, disabled }: { on: boolean; disabled: boolean }) {
  return (
    <span aria-hidden="true" style={{ width: 20, height: 20, borderRadius: 6, flex: 'none', display: 'inline-flex',
      alignItems: 'center', justifyContent: 'center', boxSizing: 'border-box', color: '#fff',
      background: on ? BLUE : 'transparent', border: on ? `1px solid ${BLUE}` : '1px solid rgba(255,255,255,0.3)',
      opacity: disabled ? 0.35 : 1 }}>
      {on && <CheckMark size={14} />}
    </span>
  );
}

export function ProwlarrImport({ wide, onClose, onImported, onSignIn }: {
  wide: boolean; onClose: () => void; onImported: (created: Tracker[]) => void; onSignIn: () => void;
}) {
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [error, setError] = useState('');
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setError('');
    setPreview(null);
    const r = await previewImport();
    if (needsSignIn(r)) { onSignIn(); return; }
    if (!r.ok || !Array.isArray(r.data.indexers)) { setError(r.status === 0 ? UNREACHABLE : r.data.error || PREVIEW_FAILED); return; }
    setPreview(r.data);
    setPicked(new Set(r.data.indexers.filter(selectable).map(x => x.key)));
  };
  useEffect(() => { load(); }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  const rows = useMemo(() => preview?.indexers || [], [preview]);
  const flip = (k: number) => setPicked(p => { const n = new Set(p); if (n.has(k)) n.delete(k); else n.add(k); return n; });

  const doImport = async () => {
    if (!preview || picked.size === 0 || busy) return;
    setBusy(true);
    setError('');
    const r = await runImport(preview.preview_id, [...picked]);
    setBusy(false);
    if (needsSignIn(r)) { onSignIn(); return; }
    if (r.status === 410) { load(); return; }          // expired: show a fresh preview
    if (!r.ok || !Array.isArray(r.data.created)) { setError(r.status === 0 ? UNREACHABLE : r.data.error || PREVIEW_FAILED); return; }
    onImported(r.data.created);
  };

  const n = picked.size;
  const pad = wide ? 24 : 20;
  return (
    <Modal wide={wide} width={560} label={IMPORT_TITLE} onClose={onClose} testId="tracker-import">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: `16px 12px 8px ${pad}px` }}>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: TEXT }}>{IMPORT_TITLE}</h2>
        <button type="button" aria-label="Close" onClick={onClose} style={circleHit} className="lt-focus">
          <span style={circle}><CloseIcon size={18} /></span>
        </button>
      </div>
      <div style={{ overflowY: 'auto', minHeight: 120 }}>
        {!preview && !error && (
          <div style={{ height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center' }} data-testid="tracker-import-loading"><Spinner /></div>
        )}
        {preview && rows.length === 0 && (
          <div style={{ height: 56, display: 'flex', alignItems: 'center', padding: `0 ${pad}px`, color: TEXT_3, fontSize: 15 }}>{NOTHING_TO_IMPORT}</div>
        )}
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }} data-testid="tracker-import-list">
          {rows.map((r, i) => {
            const can = selectable(r);
            const on = can && picked.has(r.key);
            const chip = CHIP_TEXT[r.state];
            return (
              <li key={r.key} style={{ position: 'relative' }}>
                <button type="button" role="checkbox" aria-checked={on} aria-disabled={!can || undefined} disabled={!can}
                  className="lt-pick" onClick={() => can && flip(r.key)} data-testid="tracker-import-row" data-state={r.state}
                  style={{ width: '100%', minHeight: 56, display: 'flex', alignItems: 'center', gap: 12, padding: `0 ${pad}px`,
                    border: 'none', background: 'none', cursor: can ? 'pointer' : 'default', fontFamily: FONT, color: TEXT,
                    textAlign: 'left', boxSizing: 'border-box' }}>
                  <Check on={on} disabled={!can} />
                  <span style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'baseline', gap: 10 }}>
                    <span style={{ minWidth: 0, fontSize: 15, fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden',
                      textOverflow: 'ellipsis', color: can ? TEXT : TEXT_3 }}>{r.name}</span>
                    {r.type_name && <span style={{ flex: 'none', fontSize: 13, color: TEXT_3 }}>{r.type_name}</span>}
                  </span>
                  {chip && <StatusChip text={chip} amber={r.state === 'needs_credentials'} testId="tracker-import-chip" />}
                </button>
                {i < rows.length - 1 && (
                  <span aria-hidden="true" style={{ position: 'absolute', left: pad, right: pad, bottom: 0, height: 1, background: DIVIDER }} />
                )}
              </li>
            );
          })}
        </ul>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: `16px ${pad}px ${wide ? 20 : 16}px`,
        borderTop: `1px solid ${DIVIDER}`, flexDirection: wide ? 'row' : 'column-reverse' }}>
        {error && <span role="alert" data-testid="tracker-import-error" style={{ fontSize: 13, color: DANGER_TEXT, marginRight: 'auto' }}>{error}</span>}
        {error && !preview && (
          <button type="button" className="lt-btn lt-ghost" style={{ ...secondaryBtn, ...(wide ? null : { width: '100%' }) }} onClick={load}>Retry</button>
        )}
        <button type="button" className="lt-btn lt-ghost" onClick={onClose}
          style={{ ...secondaryBtn, ...(wide ? { marginLeft: error ? 0 : 'auto' } : { width: '100%' }) }}>Cancel</button>
        <button type="button" className="lt-btn lt-primary" onClick={doImport} disabled={!preview || n === 0 || busy}
          aria-busy={busy} data-testid="tracker-import-go"
          style={{ ...primaryBtn, position: 'relative', ...(wide ? null : { width: '100%' }) }}>
          <span style={{ visibility: busy ? 'hidden' : 'visible' }}>{n > 0 ? `Import ${n}` : 'Import'}</span>
          {busy && <span style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spinner /></span>}
        </button>
      </div>
    </Modal>
  );
}
