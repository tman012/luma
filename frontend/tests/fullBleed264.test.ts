/**
 * v264 (Taylor): every phone page, sheet and modal runs to the very bottom of the screen like Home.
 * An installed iPhone PWA can stop a fixed full-screen layer short of the bottom edge (and some iOS
 * builds report a 0 inset while doing it), which showed the html color as a dark band. Every fixed
 * full-screen layer carries luma-bleed (repaints its background past its edge) or luma-bleed-down
 * (moves a scrolling layer's edge down; its scroll content ends with --luma-end-pad).
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(__dirname, '..', 'src');
/** file -> reason a fixed full-screen layer there needs no bleed. */
const ALLOW: Record<string, string> = {
  'components/home/KioskHome.tsx': 'wall kiosk, no home indicator; flat html navy',
  'components/screensaver/DidYouKnowScreensaver.tsx': 'wall kiosk screensaver',
  'components/libraries/LibrariesSheet.tsx': 'transparent tap-catcher behind a filter menu',
  'components/search/KioskSearch.tsx': 'wall kiosk search',
  'pages/VibeEnginePage.tsx': 'z-0 decorative layer under a page with its own background',
  'pages/LibrariesPage.tsx': 'settings backdrop reaches past the edge by style (bottom: -var(--luma-bleed))',
  'components/calendar/TvCalendarSheet.tsx': 'reaches past the edge by style (bottom: -var(--luma-bleed))',
  'components/start/StartUI.tsx': 'reaches past the edge by style (bottom: -var(--luma-bleed))',
};

function files(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...files(p));
    else if (/\.tsx$/.test(name) && !/\.test\./.test(name)) out.push(p);
  }
  return out;
}

function missing(): string[] {
  const out: string[] = [];
  for (const f of files(SRC)) {
    const rel = relative(SRC, f);
    if (ALLOW[rel]) continue;
    const t = readFileSync(f, 'utf8');
    const tag = /<div\b([^>]*?)style=\{\{([^}]*)\}\}/g;
    let m: RegExpExecArray | null;
    while ((m = tag.exec(t))) {
      const style = m[2];
      if (!/position:\s*'fixed'/.test(style)) continue;
      if (!(/\binset:\s*0\b/.test(style) || (/\btop:\s*0\b/.test(style) && /\bbottom:\s*0\b/.test(style)))) continue;
      if (/luma-bleed/.test(m[1])) continue;
      out.push(`${rel}:${t.slice(0, m.index).split('\n').length}`);
    }
    const cls = /className="([^"]*\bfixed\b[^"]*\binset-0\b[^"]*)"/g;
    while ((m = cls.exec(t))) if (!/luma-bleed/.test(m[1])) out.push(`${rel}:${t.slice(0, m.index).split('\n').length}`);
  }
  return out;
}

describe('v264 full-bleed phone surfaces', () => {
  it('index.css reaches at least 48px past the edge even when iOS reports a 0 inset', () => {
    const css = readFileSync(join(SRC, 'index.css'), 'utf8');
    expect(css).toMatch(/--luma-bleed:\s*max\(env\(safe-area-inset-bottom, 0px\), 48px\)/);
    expect(css).toMatch(/--luma-end-pad:\s*calc\(var\(--luma-bleed\) \+ env\(safe-area-inset-bottom, 0px\)\)/);
    expect(css).toMatch(/\.luma-bleed::after\s*\{[^}]*top:\s*100%[^}]*height:\s*var\(--luma-bleed\)[^}]*background:\s*inherit/);
    expect(css).toMatch(/\.luma-bleed-down\s*\{[^}]*bottom:\s*calc\(-1 \* var\(--luma-bleed\)\)/);
    expect(css).toMatch(/\.luma-bleed-panel\s*\{[^}]*box-shadow:\s*var\(--luma-bleed-shadow\)/);
  });
  it('the phone Settings sheet and TV calendar reach --luma-bleed past the edge; scroll content ends with --luma-end-pad', () => {
    const lib = readFileSync(join(SRC, 'pages/LibrariesPage.tsx'), 'utf8');
    expect(lib).toMatch(/bottom: 'calc\(-1 \* var\(--luma-bleed\)\)'/);
    expect(lib).toMatch(/SHEET_SCROLL_PAD = 'calc\(var\(--luma-end-pad\) \+ 24px\)'/);
    const cal = readFileSync(join(SRC, 'components/calendar/TvCalendarSheet.tsx'), 'utf8');
    expect(cal).toMatch(/bottom: 'calc\(-1 \* var\(--luma-bleed\)\)'/);
    expect(cal).toMatch(/calc\(var\(--luma-end-pad\) \+ 12px\)/);
  });
  it('every fixed full-screen layer carries luma-bleed or luma-bleed-down', () => {
    expect(missing()).toEqual([]);
  });
  it('the search list and the detail cards run under the home indicator and end with --luma-end-pad', () => {
    const s = readFileSync(join(SRC, 'components/search/SearchShelf.tsx'), 'utf8');
    expect(s).toMatch(/luma-scroll-y luma-bleed-down/);
    expect(s).toMatch(/calc\(var\(--luma-end-pad\) \+ 24px\)/);
    for (const f of ['components/media/MovieDetailModal/CompactLayout.tsx', 'components/media/TvDetailModal/CompactLayout.tsx']) {
      const t = readFileSync(join(SRC, f), 'utf8');
      expect(t).toMatch(/className="luma-bleed-down"/);
      expect(t).toMatch(/var\(--luma-end-pad\) \+ 40px/);
    }
  });
});
