import { readFileSync } from 'fs';
import { resolve } from 'path';
import { describe, expect, it } from 'vitest';
import { LUMA_BLUE, LOGO_GAP, LOGO_SRC, LOGO_WIDTH, accentBtn, plexBtn } from '@/components/start/StartUI';

const src = readFileSync(resolve(__dirname, '../src/pages/StartPage.tsx'), 'utf8');
const app = readFileSync(resolve(__dirname, '../src/App.tsx'), 'utf8');

describe('Start flow', () => {
  it('has no Get Started, Sign in with Plex or Done screens', () => {
    expect(src).not.toContain('Get Started');
    expect(src).not.toContain("{step === 'plex'");
    expect(src).not.toContain("{step === 'done'");
    expect(src).not.toContain("You're all set");
  });
  it('Create account: sentence case, logo 32px above, blue button', () => {
    const step = src.split('export function AccountStep')[1]!.split('\nconst pwLabel')[0]!;
    expect(step).not.toContain('Create Account');
    expect(step).toMatch(/<Logo \/>\s*<LogoGap \/>\s*<Title>Create account<\/Title>/);
    expect(step).toContain('style={accentBtn}');
    expect(LUMA_BLUE).toBe('#3b82f6');
    expect(accentBtn.background).toBe('#3b82f6');
    expect(plexBtn.background).toBe('#e5a00d');
  });
  it('finishing goes home through the URL base and marks the toast', () => {
    expect(src).toContain("markSetupJustFinished();\n    window.location.replace(assetUrl('/'));");
  });
  it('both routers use the URL base as basename and mount the toast', () => {
    expect(app.match(/<BrowserRouter basename=\{routerBasename\(\)\}>/g)?.length).toBe(2);
    expect(app.match(/<SetupToast \/>/g)?.length).toBe(2);
  });
});

describe('Logo', () => {
  it('uses the cropped wordmark at the old visible size with a 32px gap', () => {
    expect(LOGO_SRC).toBe('/static/images/luma_wordmark.webp');
    expect(LOGO_GAP).toBe(32);
    const r = 916 / 1520;
    expect(LOGO_WIDTH).toBe(`clamp(${240 * r}px, ${64 * r}vw, ${520 * r}px)`);
  });
});

describe('Plex pop-up', () => {
  it('has no return page; the opener closes the pop-up', () => {
    expect(app).not.toContain('plex-done');
    expect(app).not.toContain('PlexDonePage');
    expect(src).toContain('popupRef.current = popup;');
    expect(src).toMatch(/closePopup\(\);\s*const found = data.servers/);
  });
});
