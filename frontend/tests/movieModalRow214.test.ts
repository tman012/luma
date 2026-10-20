// v214 (PM, Taylor): the movie popup's pill row is prod 1b4ea74's in every state, in prod's order and labels;
// no Search automatically / Interactive search; the minimal hide never drops its pills.
import { execSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const DIR = 'frontend/src/components/media/MovieDetailModal';
const ROOT = resolve(__dirname, '../..');
const src = (f: string) => readFileSync(resolve(ROOT, DIR, f), 'utf8');

function row(code: string): string {
  const a = code.indexOf('{/* Action pills */}');
  return code.slice(a, code.indexOf('{/* end bottom group */}', a));
}
/** The row's pills, in order, for a state. Mirrors the JSX conditions of the row. */
function pills(code: string, state: 'notin' | 'in' | 'owned'): string[] {
  const r = row(code);
  const out: string[] = [];
  const primary = r.slice(0, r.indexOf('{trailerKey &&'));
  if (state === 'owned') out.push(/label="Play"/.test(primary) ? 'Play' : '?');
  else {
    const m = primary.match(/label=\{inWatchlist \? '([^']+)' : '([^']+)'\}/);
    out.push(m ? (state === 'in' ? m[1] : m[2]) : '?');
  }
  const re = /label=(?:"([^"]+)"|\{topReviews\.length > 0 \? '([^']+)')/g;
  let m: RegExpExecArray | null;
  const rest = r.slice(r.indexOf('{trailerKey &&'));
  while ((m = re.exec(rest))) {
    const lbl = m[1] ?? m[2];
    const before = rest.slice(0, m.index);
    const conds = before.match(/^\s*\{(\w+) && \(/gm) ?? [];
    const cond = conds[conds.length - 1] ?? '';
    if (/isOwned &&/.test(cond) && state !== 'owned') continue;   // the owner session (Sources) is assumed
    out.push(lbl);
  }
  return out;
}

describe('movie popup pill row matches prod 1b4ea74 (v214)', () => {
  const prod = execSync(`git -C ${ROOT} show 1b4ea74:${DIR}/ExpandedLayout.tsx`, { encoding: 'utf8' });
  const test = src('ExpandedLayout.tsx');
  for (const state of ['notin', 'in', 'owned'] as const) {
    it(`${state}: same pills, order and labels as prod`, () => {
      expect(pills(test, state)).toEqual(pills(prod, state));
    });
  }
  it('in watchlist: [In Watchlist] [Trailer] [Sources] [Similar] ... [Cast & Reviews]', () => {
    const p = pills(test, 'in');
    expect(p[0]).toBe('In Watchlist');
    expect(p[1]).toBe('Trailer');
    expect(p[2]).toBe('Sources');
    expect(p.indexOf('Similar')).toBeGreaterThan(2);
    expect(p[p.length - 1]).toBe('Cast & Reviews');
    expect(pills(test, 'notin')[0]).toBe('Watchlist');
    expect(pills(test, 'owned')[0]).toBe('Play');
  });
  it('no search buttons in any movie popup layout', () => {
    for (const f of ['ExpandedLayout.tsx', 'CompactLayout.tsx', 'index.tsx']) {
      expect(src(f), f).not.toMatch(/MovieSearchActions|Search automatically|Interactive search/);
    }
  });
  it('the minimal action hide does not apply to the row', () => {
    expect(row(test)).toMatch(/<div data-modal-actions data-modal-full-row=""/);
    const css = readFileSync(resolve(ROOT, 'frontend/src/index.css'), 'utf8');
    expect(css).toMatch(/\[data-modal-actions\]\[data-modal-full-row\]\[data-modal-full-row\] > button \{\s*display: inline-flex !important;/);
    expect(css).toMatch(/\[data-modal-full-row\]\[data-modal-full-row\] > span \{\s*display: contents !important;/);
  });
});
