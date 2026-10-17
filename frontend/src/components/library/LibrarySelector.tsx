/** Pick Library paths for a section (TV now; Radarr-style Movies later).
 * Lists every Library path; tapping one selects or deselects it. Folders are
 * only added in Settings > Library paths: with none there, only a link-style
 * button to it shows. Changes need the signed-in owner: a 401 with sign_in
 * shows a sign-in button, anything else a notice. */
import { useCallback, useContext, useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router';
import { T } from '@/theme/tokens';
import { FONT } from '@/components/start/StartUI';
import { CheckIcon } from '@/components/nav/AppNav';
import { NoticeToast } from '@/components/start/SetupToast';
import { WideCtx, card, primary, rowBtn, sectionLabel, subText, titleText, topBorder } from './LibraryPathsSettings';
import { loadSelection, saveSelection, toggled, type SelectableLibrary, type SelectionView } from './librarySelectionApi';

export const LIBRARY_PATHS_HREF = '/settings/library-paths';
export const LIBRARY_PATHS_LINK = 'Library paths';
export const SIGN_IN_TO_CHOOSE = 'Sign in to choose libraries';
const STATUS_TEXT: Record<string, string> = { missing: 'Missing', read_only: 'Read-only' };

export function LibrarySelector({ url, label, testId, onChange }: {
  url: string; label: string; testId: string;
  /** The selected Library paths after a load or a save. */
  onChange?: (selected: SelectableLibrary[]) => void;
}) {
  const wide = useContext(WideCtx);
  const navigate = useNavigate();
  const location = useLocation();
  const [view, setView] = useState<SelectionView | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [signIn, setSignIn] = useState(false);
  const [toast, setToast] = useState({ text: '', n: 0 });

  const take = useCallback((v: SelectionView) => {
    setView(v);
    onChange?.(v.libraries.filter(l => l.selected));
  }, [onChange]);
  useEffect(() => {
    loadSelection(url).then(r => (r.ok ? take(r.data) : setFailed(true)));
    // load once per section
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  if (!view) {
    return failed ? <div role="alert" data-testid={testId} style={{ color: T.text3, fontSize: 17, fontFamily: FONT }}>{"Couldn't load Library paths"}</div> : null;
  }
  const toggle = async (id: string) => {
    if (busy) return;
    setBusy(true);
    const r = await saveSelection(url, toggled(view.selected, id));
    setBusy(false);
    if (r.ok) { take(r.data); return; }
    if (r.signIn) { setSignIn(true); return; }
    setToast(t => ({ text: r.error, n: t.n + 1 }));
  };

  return (
    <div data-testid={testId}>
      {toast.text && <NoticeToast key={toast.n} text={toast.text} onDone={() => setToast(t => ({ ...t, text: '' }))} />}
      <h3 style={sectionLabel}>{label}</h3>
      {view.libraries.length === 0 ? (
        <Link to={LIBRARY_PATHS_HREF} className="lp-focus" data-testid="lib-select-link" style={linkBtn}>{LIBRARY_PATHS_LINK}</Link>
      ) : (
        <div role="group" aria-label={label} style={card}>
          {view.libraries.map((l, i) => (
            <button key={l.id} type="button" role="checkbox" aria-checked={l.selected} className="lp-row"
              data-testid="lib-select-row" data-status={l.status} disabled={busy} onClick={() => void toggle(l.id)}
              style={{ ...rowBtn, ...(i === 0 ? null : topBorder), cursor: busy ? 'default' : 'pointer' }}>
              <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2, textAlign: 'left' }}>
                <span style={titleText(wide)}>{l.name}</span>
                <span style={subText(wide)} title={l.path}>{l.path}</span>
              </span>
              {STATUS_TEXT[l.status] && (
                <span data-testid="lib-select-status" style={{ fontSize: 13, fontWeight: 500, flex: 'none',
                  color: l.status === 'missing' ? T.danger : T.warning }}>{STATUS_TEXT[l.status]}</span>
              )}
              <span aria-hidden="true" data-testid="lib-select-check"
                style={{ ...check, ...(l.selected ? checkOn : null) }}>{l.selected && <CheckIcon />}</span>
            </button>
          ))}
        </div>
      )}
      {signIn && (
        <button type="button" className="lp-focus" data-testid="lib-select-sign-in"
          onClick={() => navigate('/login', { state: { from: location.pathname + location.search, signIn: true } })}
          style={{ ...primary, marginTop: 12, width: wide ? undefined : '100%' }}>{SIGN_IN_TO_CHOOSE}</button>
      )}
    </div>
  );
}

const linkBtn: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', minHeight: 44, padding: '0 4px', color: T.accent, fontSize: 15,
  fontWeight: 500, fontFamily: FONT, textDecoration: 'none', borderRadius: 8,
};
const check: React.CSSProperties = {
  width: 24, height: 24, borderRadius: 12, flex: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
  border: '1.5px solid rgba(255,255,255,0.3)', boxSizing: 'border-box', color: T.white,
};
const checkOn: React.CSSProperties = { background: T.accent, border: `1.5px solid ${T.accent}` };
