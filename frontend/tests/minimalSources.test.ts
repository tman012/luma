import { readFileSync } from 'fs';
import { resolve } from 'path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(__dirname, '../src/index.css'), 'utf8');

describe('Sources stays in the minimal popup', () => {
  it('keeps the marked Sources button and hides the other actions in its row', () => {
    expect(css).toMatch(/html\.luma-minimal \[data-modal-actions\]:has\(> \[data-modal-sources\]\)\s*\{\s*display: flex !important;/);
    expect(css).toMatch(/html\.luma-minimal \[data-modal-actions\]:has\(> \[data-modal-sources\]\) > :not\(\[data-modal-sources\]\)\s*\{\s*display: none !important;/);
  });
});
