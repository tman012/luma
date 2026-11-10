/** The wait for Home when the document splash is not on screen (sign-in just finished, the splash's hard cap passed,
 *  a resize across the kiosk width): the splash's own picture, the logo centered on the Luma background. No spinner,
 *  no text, no skeleton. On a cold start index.html's splash covers it, and the same logo box sits underneath, so
 *  taking the splash away reveals the same pixels. */
import { assetUrl } from '@/urlHelpers';
import { SPLASH_MOTION } from '@/theme/motion';
import { releaseSplash } from '@/startup/splash';

export const SPLASH_LOGO_SRC = '/static/images/luma_splash.webp';

export function StartupLoading() {
  return (
    <div className="luma-fullscreen" style={shell} data-step="startup-loading" data-testid="startup-loading" aria-busy="true">
      <img src={assetUrl(SPLASH_LOGO_SRC)} alt="" style={logo} draggable={false} />
    </div>
  );
}

/** Real UI is up (sign-in, a settings page, an error): hand the document splash off. Idempotent. */
export function signalContentReady(): void {
  if (typeof window === 'undefined') return;
  void releaseSplash();
}

const shell: React.CSSProperties = {
  position: 'fixed', top: 0, left: 0, right: 0, zIndex: 100, display: 'flex', alignItems: 'center', justifyContent: 'center',
  background: SPLASH_MOTION.bg, overflow: 'hidden',
};
const logo: React.CSSProperties = { width: SPLASH_MOTION.logoWidth, height: 'auto', aspectRatio: '916 / 416', userSelect: 'none' };
