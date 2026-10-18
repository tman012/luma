// v169 (Designer): a guard by role and text, not testid. Every element a user can press (a <button>, a
// PillButton, or anything with role="button") whose label, text or aria-label is about ADDING to the
// watchlist must be the #3b82f6 primary with white text, labelled 'Add to watchlist' / 'Add season to
// watchlist' (sentence case). Anything else that says "Watchlist" must be navigation (home pills, top bar,
// side menu, the Watchlist page's tab), a state ('In Watchlist') or a removal; nothing in movie or TV
// detail may be a bare "Watchlist" button.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = resolve(__dirname, '../src');
const walk = (d: string): string[] => readdirSync(d).flatMap(f => {
  const p = join(d, f);
  return statSync(p).isDirectory() ? walk(p) : /\.tsx$/.test(f) ? [p] : [];
});

type Hit = { file: string; label: string; kind: 'add' | 'nav' | 'state' | 'remove' | 'bare'; block: string };
const ADD = /\badd\b[^'"<>{}]*\bwatchlist\b/i;
const NAV_FILES = ['pages/AuthenticatedHomePage.tsx', 'components/layout/TopBar.tsx', 'components/mobile/MobileSideMenu.tsx', 'pages/WatchlistPage.tsx'];
// a label a user reads: text between tags, or a quoted string with a capital or a space (not an id, path or key)
const LABEL = />\s*([^<>{}\n]*watchlist[^<>{}\n]*?)\s*<|(['"`])([^'"`\n]*watchlist[^'"`\n]*)\2/gi;
const OPENER = /<(button|PillButton|h[1-6]|p|span|label|option|a|div|li)\b/g;

/** Every label about the watchlist in *src*, with the element it sits in, classified. */
export function scan(src: string, file = 'x.tsx'): Hit[] {
  const code = src.replace(/\{\/\*[\s\S]*?\*\/\}|\/\*[\s\S]*?\*\/|(?<![:'"`\\])\/\/[^\n]*/g, m => ' '.repeat(m.length));
  const openers: { at: number; tag: string; pressable: boolean }[] = [];
  let o: RegExpExecArray | null;
  while ((o = OPENER.exec(code))) {
    const head = code.slice(o.index, code.indexOf('>', o.index) + 1);       // the opening tag (props up to its first '>')
    openers.push({ at: o.index, tag: o[1], pressable: o[1] === 'button' || o[1] === 'PillButton' || /role=["']button["']/.test(head) });
  }
  const hits: Hit[] = [];
  let m: RegExpExecArray | null;
  while ((m = LABEL.exec(code))) {
    const label = (m[1] ?? m[3] ?? '').trim();
    if (!label || !/[A-Z\s+]/.test(label) || /[(){}?:;=\\]/.test(label)) continue;   // ids and keys; code, not copy                 // 'watchlist', 'movie-add-watchlist': ids, keys
    if (/^\/|\/api\/|^[a-z0-9_-]+$/i.test(label) && !/\s/.test(label) && !/^\+?\s*Watchlist$/.test(label)) continue;
    const el = [...openers].reverse().find(x => x.at < m!.index);
    if (!el || !el.pressable) continue;                                    // headings, paragraphs: not controls
    const block = code.slice(el.at, m.index + label.length + 2);
    const kind = ADD.test(label) ? 'add' : /\bin watchlist$/i.test(label) ? 'state' : /remove/i.test(label) ? 'remove'
      : /^\+?\s*watchlist$/i.test(label) ? (NAV_FILES.some(f => file.endsWith(f)) ? 'nav' : 'bare') : 'nav';
    hits.push({ file, label, kind, block });
  }
  return hits;
}
// the background's resting value (the last branch of a ternary) is the accent, or the PillButton's blue tone
const blue = (b: string) => /(?<![\w-])background: [^,\n]*'#3b82f6'(?=\s*[,}\n])/i.test(b) || /tone=\{?[^}\n]*'blue'|tone="blue"/.test(b);
const white = (b: string) => /tone=\{?[^}\n]*'blue'|tone="blue"/.test(b) || /(?<![\w-])color: [^,\n]*'#fff(fff)?'(?=\s*[,}\n])/i.test(b);
const sentence = (l: string) => /^Add (season )?to watchlist$/.test(l) || /\? '.*' : /.test(l);

describe('watchlist button guard (v169): by role and text', () => {
  it('catches what a testid guard would miss (fixtures)', () => {
    const bad = [
      `<button style={{ background: '#ffffff', color: '#000' }}>Add to Watchlist</button>`,                  // white, Title Case, no testid
      `<div role="button" style={{ background: 'rgba(255,255,255,.08)' }}>Add to watchlist</div>`,             // a div pressed as a button
      `<button aria-label="Add to watchlist" style={{ background: '#fff' }}><svg /></button>`,                  // icon only
      `<PillButton primary label="Add to watchlist" />`,                                                       // the white primary pill
    ];
    for (const b of bad) {
      const h = scan(b, 'components/media/Fake.tsx');
      expect(h.length, b).toBeGreaterThan(0);
      expect(h.every(x => x.kind === 'add' && blue(x.block) && white(x.block) && sentence(x.label)), b).toBe(false);
    }
    const bare = scan(`<button style={{ background: 'rgba(255,255,255,0.08)' }}>Watchlist</button>`, 'components/media/Fake.tsx');
    expect(bare.map(x => x.kind)).toEqual(['bare']);
    const ok = scan(`<button style={{ background: '#3b82f6', color: '#ffffff' }}>Add to watchlist</button>`, 'components/media/Fake.tsx');
    expect(ok.every(x => x.kind === 'add' && blue(x.block) && white(x.block) && sentence(x.label))).toBe(true);
  });

  it('every add-to-watchlist control in the app is the #3b82f6 primary, white text, sentence case', () => {
    let adds = 0;
    for (const f of walk(SRC)) {
      const rel = relative(SRC, f);
      for (const h of scan(readFileSync(f, 'utf8'), rel)) {
        if (h.kind !== 'add') continue;
        adds++;
        expect(blue(h.block), `${rel}: ${h.label}`).toBe(true);
        expect(white(h.block), `${rel}: ${h.label}`).toBe(true);
        expect(sentence(h.label) || /^Add (season )?to watchlist$/.test(h.label.replace(/.*'(Add[^']*)'.*/, '$1')), `${rel}: ${h.label}`).toBe(true);
      }
    }
    expect(adds).toBeGreaterThanOrEqual(9);        // v169: 8, + the desktop movie Sources 'No sources' add
  });

  it("v169 (Tester): desktop movie Sources 'No sources' has the blue Add to watchlist, like the phone sheet", () => {
    for (const [file, testid] of [['components/media/MovieDetailModal/ExpandedLayout.tsx', 'sources-add-watchlist-desktop'],
                                  ['components/media/MovieDetailModal/SourcesPanel.tsx', 'sources-add-watchlist']]) {
      const src = readFileSync(join(SRC, file), 'utf8');
      const at = src.indexOf(`data-testid="${testid}"`);
      expect(at, file).toBeGreaterThan(0);
      const block = src.slice(src.lastIndexOf('<button', at), src.indexOf('</button>', at));
      expect(blue(block) && white(block), file).toBe(true);
      expect(block).toContain('Add to watchlist');
      const before = src.slice(Math.max(0, at - 1500), at);   // it sits in the empty-results branch
      expect(/sourcesReleases\.length === 0|noSources/.test(before), file).toBe(true);
    }
  });

  it('v169 (Tester): watchlist toasts are sentence case', () => {
    for (const f of walk(SRC)) {
      const code = readFileSync(f, 'utf8');
      expect(code, relative(SRC, f)).not.toMatch(/[Aa]dded to Watchlist/);
    }
    expect(readFileSync(join(SRC, 'components/media/TvDetailModal/CompactLayout.tsx'), 'utf8')).toContain('Added to watchlist');
    expect(readFileSync(join(SRC, 'components/upgrades/UpgradesSheet.tsx'), 'utf8')).toContain('added to watchlist`');
  });

  it('no bare "Watchlist" button outside navigation (the 390 one Designer saw is the home nav pill)', () => {
    for (const f of walk(SRC)) {
      const rel = relative(SRC, f);
      expect(scan(readFileSync(f, 'utf8'), rel).filter(h => h.kind === 'bare').map(h => `${rel}: ${h.label}`)).toEqual([]);
    }
    const home = readFileSync(join(SRC, 'pages/AuthenticatedHomePage.tsx'), 'utf8');
    expect(home).toContain("{ label: 'Watchlist', id: 'watchlist'");          // navigation: opens the Watchlist sheet
    expect(home).toContain('data-testid={`home-nav-${pill.id}`}');             // identifiable in shots and logs
    expect(home).toContain("pill.id === 'watchlist' ? onWatchlistOpen()");
  });
});
