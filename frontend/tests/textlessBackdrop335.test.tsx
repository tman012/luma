import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';

const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('text-free backdrop over Plex art', () => {
  it('useTvDetail offers the TMDb text-free backdrop (backdropTextlessUrl) only when TMDb reports a logo and a text-free pick', () => {
    const s = src('hooks/useTvDetail.ts');
    expect(s).toMatch(/tmdbData\.has_logo && tmdbData\.backdrop_textless \? parsed\.backdropUrl : null/);
    expect(s).toMatch(/backdropTextlessUrl: textless && textless !== prev\.backdropUrl \? textless : null/);
  });

  it('phone and desktop TV sheets pick the text-free backdrop as their one backdrop', () => {
    for (const f of ['CompactLayout', 'ExpandedLayout']) {
      expect(src(`components/media/TvDetailModal/${f}.tsx`)).toMatch(/useSheetHero\(finalBackdropUrl\(liveShow\?\.backdropTextlessUrl \?\? liveShow\?\.backdropUrl\), !!liveShow && !liveShow\.backdropPending/);
    }
  });
});
