/** Home's Luma wordmark (pure move out of MinimalHomePage, v319): same img, src, 64px height, test id.
 * Shared by Home's top bar and the desktop TV views' header so both render the one component. */
import { LOGO_SRC } from '@/components/start/StartUI';
import { assetUrl } from '@/urlHelpers';

export const HOME_LOGO_H = 64;

export function HomeLogo({ 'data-testid': testId = 'kiosk-logo' }: { 'data-testid'?: string }) {
  return <img src={assetUrl(LOGO_SRC)} alt="Luma" data-testid={testId} style={{ height: HOME_LOGO_H, width: 'auto' }} draggable={false} />;
}
