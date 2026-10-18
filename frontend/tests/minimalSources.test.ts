import { readFileSync } from 'fs';
import { resolve } from 'path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(__dirname, '../src/index.css'), 'utf8');

describe('Sources stays in the minimal popup', () => {
  it('keeps the marked Sources button and hides the other actions in its row', () => {
    // v158a: rows holding a data-modal-keep item (Luma TV's monitor toggle, the Seasons pill) behave the same way
    expect(css).toMatch(/html\.luma-minimal \[data-modal-actions\]:has\(> \[data-modal-sources\], > \[data-modal-keep\]\)\s*\{\s*display: flex !important;/);
    expect(css).toMatch(/html\.luma-minimal \[data-modal-actions\]:has\(> \[data-modal-sources\], > \[data-modal-keep\]\) > :not\(\[data-modal-sources\]\):not\(\[data-modal-keep\]\)\s*\{\s*display: none !important;/);
    expect(css).toMatch(/html\.luma-minimal \[data-modal-actions\]\s*\{\s*display: none !important;/);   // everything else still hidden
  });
});
