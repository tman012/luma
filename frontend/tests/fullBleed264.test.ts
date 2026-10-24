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
  // LibrariesPage phone Settings is in-flow like Home (no fixed root) — v276
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
    expect(css).toMatch(/\.luma-fullscreen\s*\{[\s\S]*?height:\s*var\(--luma-screen-h\)\s*!important/);
    expect(css).toMatch(/--luma-end-pad:\s*calc\(var\(--luma-bleed\) \+ env\(safe-area-inset-bottom, 0px\)\)/);
    // no negative-bottom / ::after workarounds
    expect(css).not.toMatch(/\.luma-bleed::after/);
    expect(css).not.toMatch(/\.luma-bleed-down\s*\{[^}]*bottom:\s*calc\(-1 \* var\(--luma-bleed\)\)/);
    // bleed-down must NOT share the fullscreen top:0 / height override (search + compact card)
    expect(css).not.toMatch(/\.luma-fullscreen\s*,\s*\.luma-bleed-down/);
    expect(css).not.toMatch(/\.luma-bleed-down\s*,\s*\.luma-fullscreen/);
    expect(css).not.toMatch(/\.luma-bleed-down\s*\{[^}]*top:\s*0\s*!important/);
    expect(css).not.toMatch(/\.luma-bleed-down\s*\{[^}]*height:\s*var\(--luma-screen-h\)/);
  });
  it('phone Settings matches Home shell (in-flow minHeight 100dvh; no fixed overflow sheet)', () => {
    const lib = readFileSync(join(SRC, 'pages/LibrariesPage.tsx'), 'utf8');
    const page = lib.match(/const sheetPage:[\s\S]*?};/)?.[0] ?? '';
    expect(page).toMatch(/minHeight:\s*'100dvh'/);
    expect(page).toMatch(/position:\s*'relative'/);
    expect(page).toMatch(/background:\s*'var\(--color-kiosk-black\)'/);
    expect(page).not.toMatch(/position:\s*'fixed'/);
    expect(page).not.toMatch(/backgroundAttachment/);
    const sheet = lib.match(/const sheet: React\.CSSProperties = \{[\s\S]*?\n\};/)?.[0] ?? '';
    expect(sheet).not.toMatch(/overflowY:\s*'auto'/);
    expect(sheet).not.toMatch(/position:\s*'absolute'/);
    expect(sheet).not.toMatch(/position:\s*'fixed'/);
    expect(lib).toMatch(/SHEET_SCROLL_PAD = 'calc\(var\(--luma-end-pad\) \+ 24px\)'/);
    // phone root must not use luma-fullscreen (that is the failed v266 approach)
    expect(lib).toMatch(/style=\{sheetPage\}/);
    expect(lib).not.toMatch(/data-layout="phone"[^>]*luma-fullscreen/);
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
  it('SearchShelf results keep top: headerH (not forced to 0 by .luma-bleed-down)', () => {
    const s = readFileSync(join(SRC, 'components/search/SearchShelf.tsx'), 'utf8');
    // results scroller is absolute under the measured header — must keep top: headerH
    expect(s).toMatch(/data-scroll-container className="luma-scroll-y luma-bleed-down"/);
    expect(s).toMatch(/top:\s*headerH/);
    expect(s).toMatch(/position:\s*'absolute'/);
    // must NOT also carry luma-fullscreen (that class forces top:0)
    const scroller = s.match(/data-scroll-container className="([^"]+)"/)?.[1] ?? '';
    expect(scroller).toMatch(/luma-bleed-down/);
    expect(scroller).not.toMatch(/luma-fullscreen/);
  });
  it('CompactLayout cards keep safe-area top (not forced to 0 by .luma-bleed-down)', () => {
    for (const f of ['components/media/MovieDetailModal/CompactLayout.tsx', 'components/media/TvDetailModal/CompactLayout.tsx']) {
      const t = readFileSync(join(SRC, f), 'utf8');
      expect(t).toMatch(/className="luma-bleed-down"/);
      expect(t).toMatch(/top:\s*'max\(env\(safe-area-inset-top, 0px\), 6px\)'/);
      // card itself must not also be luma-fullscreen
      expect(t).not.toMatch(/className="[^"]*luma-fullscreen[^"]*luma-bleed-down[^"]*"/);
      expect(t).not.toMatch(/className="[^"]*luma-bleed-down[^"]*luma-fullscreen[^"]*"/);
    }
  });
});
