/** v326 (Taylor): desktop TV Calendar: Home's chevron (the shared component, not a copy), no search, no thumbnails. */
import { describe, expect, it, afterEach } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { HomeChevron, HOME_CHEV_UP, HOME_CHEV_SIZE, homeChevCss } from '@/components/home/HomeChevron';
import { HomeLogo } from '@/components/home/HomeLogo';

const src = (f: string) => readFileSync(resolve(__dirname, '../src/' + f), 'utf8');
afterEach(cleanup);

describe('v326 desktop TV Calendar clean', () => {
  it('v319: Home keeps its HomeChevron; the TV views have no chevron and render the shared HomeLogo', () => {
    expect(src('components/search/HomeSearchR3.tsx')).toMatch(/<HomeChevron up=\{shown\}/);
    const lib = src('pages/LibrariesPage.tsx');
    expect(lib).not.toMatch(/HomeChevron|a1-chev|M7\.4 15\.4/);
    expect(lib).toMatch(/<Link to="\/" aria-label="Home" data-testid="tv-home-link"[^>]*>\s*<HomeLogo data-testid="tv-logo" \/>/);
    expect(lib).toContain("'TV Calendar'");
    const home = src('pages/MinimalHomePage.tsx');
    expect(home).toContain('<HomeLogo />');
    expect(home).not.toContain('data-testid="kiosk-logo"');
  });

  it('HomeLogo is the pure move of Home\'s img: same src, alt, 64px, kiosk-logo test id', () => {
    const { container } = render(<HomeLogo />);
    const img = container.querySelector('img')!;
    expect(img.getAttribute('data-testid')).toBe('kiosk-logo');
    expect(img.getAttribute('alt')).toBe('Luma');
    expect(img.getAttribute('src')).toContain('luma_wordmark.webp');
    expect(img.getAttribute('style')).toBe('height: 64px; width: auto;');
    expect(img.getAttribute('draggable')).toBe('false');
  });

  it('the up chevron: 48px glyph, fixed top 28px, centered, #e2e2e9, no circle or background', () => {
    const { container } = render(<HomeChevron up withCss aria-label="Close calendar" />);
    const b = container.querySelector('button.a1-chev')!;
    const svg = b.querySelector('svg')!;
    expect([svg.getAttribute('width'), svg.getAttribute('height')]).toEqual([String(HOME_CHEV_SIZE), String(HOME_CHEV_SIZE)]);
    expect(svg.querySelector('path')!.getAttribute('d')).toBe(HOME_CHEV_UP);
    expect(b.querySelector('span')).toBeNull();
    expect(homeChevCss).toContain('position:fixed;z-index:56;top:28px;left:50%;transform:translateX(-50%)');
    expect(homeChevCss).toContain('background:none');
    expect(homeChevCss).toContain('color:#e2e2e9');
    expect(container.querySelector('style')!.textContent).toBe(homeChevCss);
  });

  it('week cards have no thumbnail', () => {
    const cal = src('components/library/TvCalendar.tsx');
    const card = cal.slice(cal.indexOf('function GridCard'), cal.indexOf('export function Agenda'));
    expect(card).not.toMatch(/Poster|poster_path|tv-poster-initial/);
  });
});
