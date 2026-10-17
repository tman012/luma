/**
 * LUMA_MINIMAL=1 builds: the server marks the app page with
 * window.__LUMA_MINIMAL__ so only Get Started, sign-in and the main
 * screen mount, and no disabled feature is ever requested.
 */
declare global {
  interface Window {
    __LUMA_MINIMAL__?: boolean;
  }
}

export function isMinimal(): boolean {
  return typeof window !== 'undefined' && window.__LUMA_MINIMAL__ === true;
}
