/** v326 (Taylor): desktop TV Calendar: Home's chevron (the shared component, not a copy), no search, no thumbnails. */
import { describe, expect, it, afterEach } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { HomeChevron, HOME_CHEV_UP, HOME_CHEV_SIZE, homeChevCss } from '@/components/home/HomeChevron';
import { r3Css } from '@/components/search/HomeSearchR3';

const src = (f: string) => readFileSync(resolve(__dirname, '../src/' + f), 'utf8');
afterEach(cleanup);

describe('v326 desktop TV Calendar clean', () => {
  it('Home and the calendar both render the one HomeChevron component', () => {
    expect(src('components/search/HomeSearchR3.tsx')).toMatch(/<HomeChevron up=\{shown\}/);
    expect(src('pages/LibrariesPage.tsx')).toMatch(/<HomeChevron up withCss aria-label="Close TV"/);
    // no private copies of the glyph or its spot
    for (const f of ['components/search/HomeSearchR3.tsx', 'pages/LibrariesPage.tsx']) {
      expect(src(f)).not.toContain('M7.4 15.4');
      expect(src(f)).not.toContain('.a1-chev{');
      expect(src(f)).not.toContain('ChevronUpIcon');
    }
    expect(r3Css).toContain(homeChevCss);
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
