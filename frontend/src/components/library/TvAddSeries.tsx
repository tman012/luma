/** v182 (Taylor): adding shows has its own flow. The TV toolbar search on every TV view finds only your own
 * shows (tv.db, no network search, no Add); the Series page's "Add series" button opens this dialog, which
 * searches TMDB, then Add asks Monitor (Future episodes by default), Quality profile and Path. Owner-only:
 * the find and add routes need the signed-in owner. */
import { useEffect, useRef, useState } from 'react';
import { FONT } from '@/components/start/StartUI';
import { Modal, glassBtn, primaryBtn } from './TvEdit';
import { SEARCH_LIST_ID, SEARCH_MIN, TvSearch, searchRowId, type SearchKeys } from './TvSearch';
import type { LibPath } from './libraryPathsApi';

export const ADD_SERIES = 'Add series';

export function AddSeriesButton({ paths, onAdded }: { paths: LibPath[]; onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="lp-focus" data-testid="tv-add-series" aria-haspopup="dialog" aria-expanded={open}
        onClick={() => setOpen(true)} style={{ ...primaryBtn(true), height: 32, padding: '0 14px', fontSize: 13, flex: 'none' }}>
        {ADD_SERIES}
      </button>
      {open && <AddSeriesModal paths={paths} onAdded={onAdded} onClose={() => setOpen(false)} />}
    </>
  );
}

export function AddSeriesModal({ paths, onAdded, onClose }: { paths: LibPath[]; onAdded: () => void; onClose: () => void }) {
  const [q, setQ] = useState('');
  const [active, setActive] = useState(-1);
  const keys = useRef<SearchKeys | null>(null);
  const shown = q.trim().length >= SEARCH_MIN;
  useEffect(() => { setActive(-1); }, [q]);
  useEffect(() => {
    if (active >= 0) document.getElementById(searchRowId(active))?.scrollIntoView?.({ block: 'nearest' });
  }, [active]);
  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    const n = keys.current?.count ?? 0;
    if (e.key === 'ArrowDown' && n) { e.preventDefault(); setActive(a => (a + 1) % n); }
    else if (e.key === 'ArrowUp' && n) { e.preventDefault(); setActive(a => (a <= 0 ? n - 1 : a - 1)); }
    else if (e.key === 'Enter' && shown && active >= 0) { e.preventDefault(); keys.current?.enter(active); }
  };
  return (
    <Modal title={ADD_SERIES} onClose={onClose} testid="tv-add-series-modal" width={640}
      footer={<button type="button" className="lp-focus tve-glass" data-testid="tv-add-series-done" onClick={onClose} style={{ ...glassBtn, marginLeft: 'auto' }}>Done</button>}>
      <input type="search" data-testid="tv-add-series-search" aria-label="Search TMDB" placeholder="Search TMDB" value={q} autoFocus
        role="combobox" aria-expanded={shown} aria-controls={SEARCH_LIST_ID} aria-autocomplete="list"
        aria-activedescendant={shown && active >= 0 ? searchRowId(active) : undefined}
        onChange={e => setQ(e.target.value)} onKeyDown={onKey} className="tvt-search"
        style={{ width: '100%', height: 40, boxSizing: 'border-box', padding: '0 14px', borderRadius: 10, outline: 'none',
          background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', color: '#fff', fontSize: 14,
          fontFamily: FONT, colorScheme: 'dark' }} />
      {shown && (
        <div style={{ borderRadius: 12, border: '1px solid rgba(255,255,255,0.08)', overflow: 'visible' }}>
          <TvSearch q={q} paths={paths} onAdded={onAdded} keys={keys} active={active} />
        </div>
      )}
    </Modal>
  );
}
