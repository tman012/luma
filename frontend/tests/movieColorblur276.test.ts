/**
 * v276 (Taylor): phone movie sheets keep poster colorblur on minimal (:5002).
 * v207 had wrongly nulled ultraBlurColors when isMinimal() — flat navy behind title.
 * Prod 1b4ea74 always uses paletteColors → sheetBase / UltraBlurOverlay.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(__dirname, '..', 'src');

describe('v276 movie phone colorblur (prod match)', () => {
  it('CompactLayout does not null ultraBlurColors on minimal', () => {
    const t = readFileSync(join(SRC, 'components/media/MovieDetailModal/CompactLayout.tsx'), 'utf8');
    expect(t).not.toMatch(/isMinimal\(\)\s*\?\s*null/);
    expect(t).not.toMatch(/import \{ isMinimal \} from '@\/minimal'/);
    expect(t).toMatch(/const ultraBlurColors = paletteColors/);
    expect(t).toMatch(/sheetBase\(ultraBlurColors\)/);
    expect(t).toMatch(/<UltraBlurOverlay[^>]*colors=\{ultraBlurColors\}/);
  });
  it('TV CompactLayout was never gated (still uses palette)', () => {
    const t = readFileSync(join(SRC, 'components/media/TvDetailModal/CompactLayout.tsx'), 'utf8');
    expect(t).not.toMatch(/isMinimal\(\)\s*\?\s*null/);
    expect(t).toMatch(/sheetBase\(ultraBlurColors\)/);
  });
});
