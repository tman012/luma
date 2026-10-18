// v167 (Designer regression): every "Add to watchlist" button on movie and TV detail, phone and desktop,
// is the #3b82f6 primary with white text, in sentence case. Fails if any reverts to the white pill.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const MEDIA = resolve(__dirname, '../src/components/media');
const walk = (d: string): string[] => readdirSync(d).flatMap(f => {
  const p = join(d, f);
  return statSync(p).isDirectory() ? walk(p) : /\.tsx?$/.test(f) ? [p] : [];
});
const read = (p: string) => readFileSync(join(MEDIA, p), 'utf8');

/** The JSX element a label sits in: from the last opening tag of a button/PillButton/heading before it. */
function opener(src: string, at: number): { tag: string; block: string } {
  const re = /<(button|PillButton|h1|h2|h3)\b/g;
  let m: RegExpExecArray | null; let last: RegExpExecArray | null = null;
  while ((m = re.exec(src)) && m.index < at) last = m;
  return last ? { tag: last[1], block: src.slice(last.index, at) } : { tag: '', block: '' };
}
const BLUE = (block: string) => /#3b82f6/i.test(block) || /tone=\{?[^}\n]*'blue'|tone="blue"/.test(block);
const WHITE = (block: string) => /#3b82f6/i.test(block) ? /color: [^,\n]*'#fff(fff)?'/i.test(block) : true;  // PillButton blue tone is white

describe('Add to watchlist buttons (v167)', () => {
  it('no Title Case "Add to Watchlist" and no bare "Watchlist" add label anywhere in movie or TV detail', () => {
    for (const f of walk(MEDIA)) {
      const code = readFileSync(f, 'utf8').split('\n').filter(l => !/^\s*(\/\/|\/?\*)/.test(l)).join('\n');
      expect(code, f).not.toMatch(/>\s*Add to Watchlist\s*</);
      expect(code, f).not.toMatch(/'Add to Watchlist'/);
      expect(code, f).not.toMatch(/: 'Watchlist'\}/);
    }
  });

  it('every button labelled "Add to watchlist" is the #3b82f6 primary with white text', () => {
    let buttons = 0;
    for (const f of walk(MEDIA)) {
      const src = readFileSync(f, 'utf8');
      const re = /Add (season )?to watchlist/g;
      let m: RegExpExecArray | null;
      while ((m = re.exec(src))) {
        const line = src.slice(src.lastIndexOf('\n', m.index) + 1, src.indexOf('\n', m.index));
        if (/^\s*(\/\/|\/?\*|\{\/\*)/.test(line) || /\/\/.*Add (season )?to watchlist/.test(line)) continue;  // comments
        const { tag, block } = opener(src, m.index);
        if (tag !== 'button' && tag !== 'PillButton') continue;                                  // headings
        buttons++;
        expect(BLUE(block), `${f}: ${line.trim()}`).toBe(true);
        expect(WHITE(block), `${f}: ${line.trim()}`).toBe(true);
        expect(block, f).not.toMatch(/background: '#ffffff',\s*color: '#000/);
      }
    }
    expect(buttons).toBeGreaterThanOrEqual(5);   // movie phone + desktop + Sources, TV phone + desktop (+ the monitoring sheet)
  });

  it('movie detail on phone: data-testid movie-add-watchlist, #3b82f6, white text, not the 50px pill', () => {
    const src = read('MovieDetailModal/CompactLayout.tsx');
    const btn = src.slice(src.indexOf('data-testid="movie-add-watchlist"'), src.indexOf('Add to watchlist', src.indexOf('data-testid="movie-add-watchlist"')));
    expect(btn).toContain("background: '#3b82f6'");
    expect(btn).toContain("color: '#ffffff'");
    expect(btn).not.toContain("borderRadius: '50px'");
  });

  it('movie detail on desktop: the watchlist pill is the blue tone (as TV), white text', () => {
    const src = read('MovieDetailModal/ExpandedLayout.tsx');
    expect(src).toContain("tone={inWatchlist ? undefined : 'blue'}");
    expect(src).toContain("label={inWatchlist ? 'In Watchlist' : 'Add to watchlist'}");
    expect(src).toMatch(/tone === 'blue'\s*\? \(hovered \? '#60a5fa' : '#3b82f6'\)/);
    expect(src).toContain("color: tone === 'blue' ? '#fff'");
  });

  it('TV detail on desktop and phone keep it', () => {
    const exp = read('TvDetailModal/ExpandedLayout.tsx');
    const pill = exp.slice(exp.indexOf('testid="tv-add-watchlist-desktop"') - 200, exp.indexOf('label="Add to watchlist"'));
    expect(pill).toContain('tone="blue"');
    const cmp = read('TvDetailModal/CompactLayout.tsx');
    const btn = cmp.slice(cmp.indexOf('data-testid="tv-add-watchlist"'), cmp.indexOf("'Add to watchlist'"));
    expect(btn).toContain("background: '#3b82f6'");
    expect(btn).toContain("color: '#ffffff'");
  });
});
