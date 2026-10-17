import { readFileSync } from 'fs';
import { resolve } from 'path';
import { describe, expect, it } from 'vitest';
import { LUMA_BLUE, PRIMARY_BUTTON_CLASS, primaryButtonColors } from '@/ui/lumaButton';
import { accentBtn, plexBtn, primaryBtn } from '@/components/start/StartUI';

const read = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');

describe('shared primary button', () => {
  it('is Luma blue with white text', () => {
    expect(LUMA_BLUE).toBe('#3b82f6');
    expect(primaryButtonColors).toEqual({ background: '#3b82f6', color: '#fff' });
    expect(primaryBtn.background).toBe(LUMA_BLUE);
    expect(primaryBtn.color).toBe('#fff');
    expect(accentBtn).toBe(primaryBtn);
  });
  it('Plex stays amber', () => {
    expect(plexBtn.background).toBe('#e5a00d');
  });
  it('Tailwind class uses the accent token, which is Luma blue', () => {
    expect(PRIMARY_BUTTON_CLASS).toContain('bg-accent');
    expect(read('index.css')).toContain('--color-accent: #3b82f6;');
  });
  it('My Settings uses the shared colors', () => {
    expect(read('pages/MySettingsPage.tsx')).toContain('...primaryButtonColors');
  });
  it('login uses the shared primary button', () => {
    expect(read('pages/LoginPage.tsx')).toContain('{ ...primaryBtn,');
  });
  it('login and Get Started use sentence case', () => {
    expect(read('pages/LoginPage.tsx')).not.toContain('Create Account');
    expect(read('pages/StartPage.tsx')).not.toContain('Create Account');
  });
  it.each(['pages/settings/components/RoomAccordion.tsx', 'pages/settings/components/ListEditor.tsx'])(
    '%s saves use the accent', (p) => {
      const s = read(p);
      expect(s).not.toMatch(/bg-emerald-500 /);
      expect(s).toContain('bg-accent hover:bg-accent-hover');
    });
});
