// The TV release-name corpus shared with tests/test_tv_release.py: the popup's
// detectTvScope and app/tv_release.scope must agree on every line.
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { describe, expect, it } from 'vitest';
import { detectTvScope } from '@/components/media/TvDetailModal/useTvSourcesData';

const rows = readFileSync(resolve(__dirname, '../../tests/fixtures/tv_release_names.tsv'), 'utf-8')
  .split('\n').filter(l => l && !l.startsWith('#')).map(l => l.split('\t'));

describe('TV release corpus', () => {
  it('is the shared file', () => {
    expect(rows.length).toBeGreaterThanOrEqual(150);
    expect(rows.every(r => r.length === 4)).toBe(true);
  });
  it.each(rows)('%s', (name, packType, packEpisode) => {
    expect(detectTvScope(name!)).toEqual({
      packType, packEpisode: packEpisode ? Number(packEpisode) : null,
    });
  });
});
