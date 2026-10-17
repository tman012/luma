import { readFileSync } from 'fs';
import { resolve } from 'path';
import { afterEach, describe, expect, it } from 'vitest';
import { img } from '@/components/home/homeApi';
import { NAVY_BG } from '@/components/start/StartUI';

const read = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');

describe('image URLs', () => {
  afterEach(() => { delete (window as { __LUMA_URL_BASE__?: string }).__LUMA_URL_BASE__; });
  it('uses the server URL as is (no second URL base)', () => {
    (window as { __LUMA_URL_BASE__?: string }).__LUMA_URL_BASE__ = '/luma';
    expect(img('/luma/api/img/plex?url=a&w=300')).toBe('/luma/api/img/plex?url=a&w=300');
    expect(img(null)).toBeNull();
  });
});

describe('navy everywhere', () => {
  it('exports one navy backdrop token', () => {
    expect(NAVY_BG).toBe('radial-gradient(ellipse at 50% 30%, #262a66 0%, #15102a 55%, #0a0818 100%)');
  });
  it.each(['pages/LoginPage.tsx', 'pages/LandingPage.tsx', 'pages/UserSetupPage.tsx', 'pages/MySettingsPage.tsx',
    'pages/MinimalHomePage.tsx', 'pages/UnlockPage.tsx', 'pages/AuthenticatedHomePage.tsx'])('%s has no plum or purple', (p) => {
    const src = read(p);
    for (const c of ['#3a1a3a', '#1a0a1e', '#0a0410', '168,85,247', '147,112,219', '26,10,30', '28,22,56']) {
      expect(src.replace(/\s/g, '')).not.toContain(c);
    }
  });
  it.each(['pages/LandingPage.tsx', 'pages/UserSetupPage.tsx', 'pages/MySettingsPage.tsx', 'pages/MinimalHomePage.tsx'])(
    '%s reuses NAVY_BG', (p) => { expect(read(p)).toContain('NAVY_BG'); });
});
