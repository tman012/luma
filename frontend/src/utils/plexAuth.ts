/**
 * Plex sign-in window: a centered pop-up when possible, else the same tab
 * (blocked pop-up, iOS standalone / installed PWA).
 */
export type PlexAuthMode = 'popup' | 'redirect';

export const POPUP_W = 600;
export const POPUP_H = 700;

export function isStandalone(): boolean {
  if (typeof window === 'undefined') return false;
  const nav = window.navigator as Navigator & { standalone?: boolean };
  if (nav.standalone === true) return true;
  try {
    return window.matchMedia?.('(display-mode: standalone)').matches === true;
  } catch {
    return false;
  }
}

export function popupFeatures(win: Pick<Window, 'screenX' | 'screenY' | 'outerWidth' | 'outerHeight'>,
  w = POPUP_W, h = POPUP_H): string {
  const left = Math.max(0, Math.round(win.screenX + (win.outerWidth - w) / 2));
  const top = Math.max(0, Math.round(win.screenY + (win.outerHeight - h) / 2));
  return `popup=yes,width=${w},height=${h},left=${left},top=${top}`;
}

/** Must run synchronously inside the click handler so it isn't blocked. */
export function openPlexPopup(): Window | null {
  if (isStandalone()) return null;
  try {
    return window.open('about:blank', 'luma-plex-auth', popupFeatures(window));
  } catch {
    return null;
  }
}

export function plexAuthMode(popup: Window | null): PlexAuthMode {
  return popup ? 'popup' : 'redirect';
}
