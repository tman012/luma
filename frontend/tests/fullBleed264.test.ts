/**
 * v266 (Taylor/Designer): every phone page, sheet and modal reaches the bottom like Home.
 * Home uses minHeight 100dvh (in flow). Fixed inset:0 / negative-bottom / .luma-bleed::after
 * stay clipped on a real iPhone PWA. Full-screen fixed layers use .luma-fullscreen
 * (height: var(--luma-screen-h) = 100lvh/100dvh). Scroll content ends with --luma-end-pad.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(__dirname, '..', 'src');
/** file -> reason a fixed full-screen layer there needs no fullscreen/bleed class. */
const ALLOW: Record<string, string> = {
  'components/home/KioskHome.tsx': 'wall kiosk, no home indicator; flat html navy',
  'components/screensaver/DidYouKnowScreensaver.tsx': 'wall kiosk screensaver',
  'components/libraries/LibrariesSheet.tsx': 'transparent tap-catcher behind a filter menu',
  'components/search/KioskSearch.tsx': 'wall kiosk search',
  'pages/VibeEnginePage.tsx': 'z-0 decorative layer under a page with its own background',
  'pages/LibrariesPage.tsx': 'settings backdrop uses className luma-fullscreen (style top/left/right)',
  'components/calendar/TvCalendarSheet.tsx': 'uses className luma-fullscreen (style top/left/right)',
  'components/start/StartUI.tsx': 'uses className luma-fullscreen (style top/left/right)',
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
      const attrs = m[1];
      if (!/position:\s*'fixed'/.test(style)) continue;
      // Full-screen: inset:0, or top+bottom 0. (top/left/right alone is a header bar — skip.)
      // v266 roots use top/left/right + luma-fullscreen (no bottom) — count those too.
      const inset0 = /\binset:\s*0\b/.test(style);
      const topBottom = /\btop:\s*0\b/.test(style) && /\bbottom:\s*0\b/.test(style);
      const v266Root = /\btop:\s*0\b/.test(style) && /\bleft:\s*0\b/.test(style) && /\bright:\s*0\b/.test(style)
        && !/\bbottom:\s*/.test(style) && !inset0
        && /luma-fullscreen|luma-bleed(?!-panel)|luma-bleed-down/.test(attrs);
      if (!(inset0 || topBottom || v266Root)) continue;
      if (/luma-fullscreen/.test(attrs) || /luma-bleed-down/.test(attrs) || /luma-bleed(?!-panel)/.test(attrs)) continue;
      out.push(`${rel}:${t.slice(0, m.index).split('\n').length}`);
    }
    const cls = /className="([^"]*\bfixed\b[^"]*\binset-0\b[^"]*)"/g;
    while ((m = cls.exec(t))) {
      if (!/luma-fullscreen|luma-bleed-down|luma-bleed(?!-panel)/.test(m[1])) {
        out.push(`${rel}:${t.slice(0, m.index).split('\n').length}`);
      }
    }
  }
  return out;
}

describe('v266 full-bleed phone surfaces (Home match)', () => {
  it('index.css sizes full-screen fixed layers by --luma-screen-h (100dvh/lvh)', () => {
    const css = readFileSync(join(SRC, 'index.css'), 'utf8');
    expect(css).toMatch(/--luma-screen-h:\s*100dvh/);
    expect(css).toMatch(/@supports \(height: 100lvh\)\s*\{\s*:root\s*\{\s*--luma-screen-h:\s*100lvh/);
    expect(css).toMatch(/\.luma-fullscreen[\s\S]*?height:\s*var\(--luma-screen-h\)\s*!important/);
    expect(css).toMatch(/--luma-end-pad:\s*calc\(var\(--luma-bleed\) \+ env\(safe-area-inset-bottom, 0px\)\)/);
    // no negative-bottom / ::after workarounds
    expect(css).not.toMatch(/\.luma-bleed::after/);
    expect(css).not.toMatch(/\.luma-bleed-down\s*\{[^}]*bottom:\s*calc\(-1 \* var\(--luma-bleed\)\)/);
  });
  it('phone Settings does not spread homePage (no backgroundAttachment:fixed); uses luma-fullscreen + GlowLayer', () => {
    const lib = readFileSync(join(SRC, 'pages/LibrariesPage.tsx'), 'utf8');
    expect(lib).toMatch(/className="luma-fullscreen"/);
    const bd = lib.match(/const sheetBackdrop:[\s\S]*?};/)?.[0] ?? '';
    expect(bd).toMatch(/\.\.\.glowPage/);
    expect(bd).not.toMatch(/\.\.\.homePage/);
    expect(bd).not.toMatch(/backgroundAttachment/);
    expect(lib).toMatch(/SHEET_SCROLL_PAD = 'calc\(var\(--luma-end-pad\) \+ 24px\)'/);
    expect(lib).toMatch(/<GlowLayer \/>/);
    expect(lib).not.toMatch(/bottom: 'calc\(-1 \* var\(--luma-bleed\)\)'/);
  });
  it('TV calendar, search shelf and account menu use luma-fullscreen', () => {
    const cal = readFileSync(join(SRC, 'components/calendar/TvCalendarSheet.tsx'), 'utf8');
    expect(cal).toMatch(/className="luma-fullscreen"/);
    expect(cal).not.toMatch(/bottom: 'calc\(-1 \* var\(--luma-bleed\)\)'/);
    expect(cal).toMatch(/calc\(var\(--luma-end-pad\) \+ 12px\)/);
    const s = readFileSync(join(SRC, 'components/search/SearchShelf.tsx'), 'utf8');
    expect(s).toMatch(/luma-fullscreen/);
    expect(s).toMatch(/calc\(var\(--luma-end-pad\) \+ 24px\)/);
    const um = readFileSync(join(SRC, 'components/nav/UserMenu.tsx'), 'utf8');
    expect(um).toMatch(/luma-fullscreen/);
  });
  it('every fixed full-screen layer carries luma-fullscreen, luma-bleed-down, or luma-bleed', () => {
    expect(missing()).toEqual([]);
  });
  it('the search list and the detail cards run under the home indicator and end with --luma-end-pad', () => {
    const s = readFileSync(join(SRC, 'components/search/SearchShelf.tsx'), 'utf8');
    expect(s).toMatch(/luma-scroll-y luma-bleed-down/);
    expect(s).toMatch(/calc\(var\(--luma-end-pad\) \+ 24px\)/);
    for (const f of ['components/media/MovieDetailModal/CompactLayout.tsx', 'components/media/TvDetailModal/CompactLayout.tsx']) {
      const t = readFileSync(join(SRC, f), 'utf8');
      expect(t).toMatch(/className="[^"]*luma-bleed-down[^"]*"/);
      expect(t).toMatch(/var\(--luma-end-pad\) \+ 40px/);
    }
  });
});
