/** Detail popup title: hold the title area (empty, its space kept) until the
 * clearlogo has loaded and decoded, then show the logo. Fall back to the text
 * title, faded in, when there's no logo, it fails, or nothing is ready within
 * LOGO_WAIT_MS. Once the text title shows it stays (no late jump to a logo). */
import { useEffect, useState } from 'react';

export type TitleState = 'hold' | 'logo' | 'text';
export const LOGO_WAIT_MS = 2000;

/** Loads and decodes an image off-screen; true once it's ready or failed. */
function useDecoded(url: string | null | undefined): boolean {
  const [doneUrl, setDoneUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!url) return;
    let alive = true;
    const img = new Image();
    const done = () => { if (alive) setDoneUrl(url); };
    img.onload = () => {
      const p = typeof img.decode === 'function' ? img.decode() : Promise.resolve();
      p.then(done, done);
    };
    img.onerror = done;
    img.src = url;
    return () => { alive = false; img.onload = null; img.onerror = null; };
  }, [url]);
  return !url || doneUrl === url;
}

/**
 * Title state for the detail popup. With `backdropUrl`, the reveal also waits
 * for the backdrop to decode, so backdrop, color blur and title fade in
 * together (callers show all three once the state leaves 'hold'). After
 * LOGO_WAIT_MS everything shows regardless.
 */
export function useTitleReveal(
  logoUrl: string | null | undefined, key?: unknown, backdropUrl?: string | null,
): TitleState {
  const [logo, setLogo] = useState<TitleState>('hold');
  const [timedOut, setTimedOut] = useState(false);
  const backdropReady = useDecoded(backdropUrl);

  // New title (another item opened): start over.
  useEffect(() => {
    setLogo('hold');
    setTimedOut(false);
    const t = window.setTimeout(() => setTimedOut(true), LOGO_WAIT_MS);
    return () => window.clearTimeout(t);
  }, [key]);

  useEffect(() => {
    if (logo !== 'hold') return;
    if (!logoUrl) {
      // The logo URL may still be on its way; give up after the wait.
      if (timedOut) setLogo('text');
      return;
    }
    if (timedOut) { setLogo('text'); return; }
    let alive = true;
    const img = new Image();
    const done = (ok: boolean) => { if (alive) setLogo(s => (s === 'hold' ? (ok ? 'logo' : 'text') : s)); };
    img.onload = () => {
      const p = typeof img.decode === 'function' ? img.decode() : Promise.resolve();
      p.then(() => done(true), () => done(true));
    };
    img.onerror = () => done(false);
    img.src = logoUrl;
    return () => { alive = false; img.onload = null; img.onerror = null; };
  }, [logoUrl, timedOut, logo]);

  if (logo === 'hold') return timedOut ? 'text' : 'hold';
  return backdropReady || timedOut ? logo : 'hold';
}

/** Opacity + fade for the backdrop and color blur, in step with the title. */
export function revealStyle(state: TitleState): React.CSSProperties {
  return { opacity: state === 'hold' ? 0 : 1, transition: 'opacity 0.45s ease-out' };
}

/** Styles for the text title per state: hidden (space kept) while holding,
 * gone when the logo shows, faded in as the fallback. */
export function titleTextStyle(state: TitleState): React.CSSProperties {
  if (state === 'logo') return { display: 'none' };
  if (state === 'hold') return { display: 'block', visibility: 'hidden' };
  return { display: 'block', animation: 'lumaTitleIn 250ms ease-out' };
}
