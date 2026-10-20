// v216 (Designer): the wide movie popup's owned state has prod 1b4ea74's file-info (i) button, just left
// of the close X, in minimal builds too (it was gated on !isMinimal()).
import { execSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = resolve(__dirname, '../..');
const P = 'frontend/src/components/media/MovieDetailModal/ExpandedLayout.tsx';
const test = readFileSync(resolve(ROOT, P), 'utf8');
const prod = execSync(`git show 1b4ea74:${P}`, { cwd: ROOT, encoding: 'utf8', maxBuffer: 1 << 26 });
const fn = (c: string) => c.slice(c.indexOf('function FileInfoButton'), c.indexOf('\n}\n', c.indexOf('function FileInfoButton')));
const mount = (c: string) => { const a = c.indexOf('<FileInfoButton'); return c.slice(a, c.indexOf('/>', a)); };

describe('wide popup file-info button (v216)', () => {
  it('is not gated on minimal mode', () => {
    expect(test).toMatch(/\{canShowFileInfo && \(\s*<FileInfoButton/);
    expect(test).not.toMatch(/canShowFileInfo && !isMinimal\(\)/);
  });
  it('is prod 1b4ea74\'s button as is (same chip as the X, same place, same sheet)', () => {
    expect(fn(test)).toBe(fn(prod));
    expect(mount(test)).toBe(mount(prod));
    expect(test).toMatch(/const canShowFileInfo = authenticated && isOwned && !!detail\?\.ratingKey;/);
  });
  it('no minimal CSS rule hides it', () => {
    const css = readFileSync(resolve(ROOT, 'frontend/src/index.css'), 'utf8');
    expect(css).not.toMatch(/\[data-file-info-button\][^{]*\{\s*display:\s*none/);
  });
});
