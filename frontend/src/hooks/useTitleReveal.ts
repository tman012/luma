/** Detail popup title: hold the title area (empty, its space kept) until the
 * clearlogo has loaded and decoded, then show the logo. Fall back to the text
 * title, faded in, when there's no logo, it fails, or nothing is ready within
 * LOGO_WAIT_MS. Once the text title shows it stays (no late jump to a logo). */
import { useEffect, useState } from 'react';

export type TitleState = 'hold' | 'logo' | 'text';
export const LOGO_WAIT_MS = 2000;

export function useTitleReveal(logoUrl: string | null | undefined, key?: unknown): TitleState {
  const [state, setState] = useState<TitleState>('hold');
  const [timedOut, setTimedOut] = useState(false);

  // New title (another item opened): start over.
  useEffect(() => {
    setState('hold');
    setTimedOut(false);
    const t = window.setTimeout(() => setTimedOut(true), LOGO_WAIT_MS);
    return () => window.clearTimeout(t);
  }, [key]);

  useEffect(() => {
    if (state !== 'hold') return;
    if (!logoUrl) {
      // The logo URL may still be on its way; give up after the wait.
      if (timedOut) setState('text');
      return;
    }
    if (timedOut) { setState('text'); return; }
    let alive = true;
    const img = new Image();
    const done = (ok: boolean) => { if (alive) setState(s => (s === 'hold' ? (ok ? 'logo' : 'text') : s)); };
    img.onload = () => {
      const p = typeof img.decode === 'function' ? img.decode() : Promise.resolve();
      p.then(() => done(true), () => done(true));
    };
    img.onerror = () => done(false);
    img.src = logoUrl;
    return () => { alive = false; img.onload = null; img.onerror = null; };
  }, [logoUrl, timedOut, state]);

  return state;
}

/** Styles for the text title per state: hidden (space kept) while holding,
 * gone when the logo shows, faded in as the fallback. */
export function titleTextStyle(state: TitleState): React.CSSProperties {
  if (state === 'logo') return { display: 'none' };
  if (state === 'hold') return { display: 'block', visibility: 'hidden' };
  return { display: 'block', animation: 'lumaTitleIn 250ms ease-out' };
}
