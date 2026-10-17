/** Folder picker for library paths: the allowed roots, then folders inside
 * them. Tap a folder to open it, "Use this folder" to pick it. */
import { useEffect, useState } from 'react';
import { T } from '@/theme/tokens';
import { FONT } from '@/components/start/StartUI';
import { ChevronIcon } from '@/components/nav/AppNav';
import { browseFolders, type Folder } from './libraryPathsApi';

export function FolderBrowser({ start, onPick, onClose }: {
  start: string | null; onPick: (path: string) => void; onClose: () => void;
}) {
  const [path, setPath] = useState<string | null>(start);
  const [parent, setParent] = useState<string | null>(null);
  const [dirs, setDirs] = useState<Folder[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    setDirs(null);
    browseFolders(path).then(r => {
      if (!live) return;
      if (r.ok) { setDirs(r.dirs); setParent(r.parent); setError(''); return; }
      if (path && path === start) { setPath(null); return; }     // a typed path outside the roots: start at the roots
      setError(r.error); setDirs([]);
    });
    return () => { live = false; };
  }, [path, start]);

  return (
    <div data-testid="lp-browser" style={box}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px' }}>
        {path !== null && (
          <button type="button" className="lp-focus" data-testid="lp-browser-up" aria-label="Back"
            onClick={() => setPath(parent)} style={iconBtn}>
            <span style={{ display: 'inline-flex', transform: 'rotate(180deg)' }}><ChevronIcon size={16} /></span>
          </button>
        )}
        <span data-testid="lp-browser-path" style={pathText} title={path || ''}>{path || 'Folders'}</span>
      </div>
      <div style={{ maxHeight: 260, overflowY: 'auto' }}>
        {error && <div role="alert" style={{ ...rowStyle, color: T.dangerText, cursor: 'default' }}>{error}</div>}
        {dirs?.map(d => (
          <button key={d.path} type="button" className="lp-row" data-testid="lp-browser-dir" onClick={() => setPath(d.path)} style={rowStyle}>
            <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textAlign: 'left' }}>{d.name}</span>
            <span style={{ display: 'inline-flex', color: T.text4 }}><ChevronIcon size={14} /></span>
          </button>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 12, padding: 12, justifyContent: 'flex-end' }}>
        <button type="button" className="lp-focus" data-testid="lp-browser-cancel" onClick={onClose} style={ghost}>Cancel</button>
        <button type="button" className="lp-focus" data-testid="lp-browser-use" disabled={!path}
          onClick={() => { if (path) onPick(path); }} style={{ ...primary, opacity: path ? 1 : 0.5 }}>Use this folder</button>
      </div>
    </div>
  );
}

const box: React.CSSProperties = {
  borderRadius: 12, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', fontFamily: FONT,
};
const pathText: React.CSSProperties = {
  flex: 1, minWidth: 0, fontSize: 14, color: T.text2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
};
const rowStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 10, width: '100%', minHeight: 44, padding: '0 14px', border: 'none',
  borderTop: '1px solid rgba(255,255,255,0.06)', background: 'none', color: T.text, fontSize: 15, fontFamily: FONT, cursor: 'pointer',
};
const iconBtn: React.CSSProperties = {
  width: 32, height: 32, borderRadius: 16, border: 'none', background: 'rgba(255,255,255,0.08)', color: T.text,
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', flex: 'none',
};
const ghost: React.CSSProperties = {
  height: 40, padding: '0 16px', borderRadius: 10, border: 'none', background: 'rgba(255,255,255,0.08)', color: T.text,
  fontSize: 15, fontWeight: 500, fontFamily: FONT, cursor: 'pointer',
};
const primary: React.CSSProperties = {
  height: 40, padding: '0 16px', borderRadius: 10, border: 'none', background: T.accent, color: T.white,
  fontSize: 15, fontWeight: 600, fontFamily: FONT, cursor: 'pointer',
};
