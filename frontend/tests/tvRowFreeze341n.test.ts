import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const tv = readFileSync(resolve(__dirname, '../src/components/media/TvDetailModal/ExpandedLayout.tsx'), 'utf8');

describe('v341n TV button row is decided at the reveal (Designer D4)', () => {
  it('freezes Seasons and Cast with useRevealFreeze, from Plex childCount and cast', () => {
    expect(tv).toMatch(/const tvRow = useRevealFreeze\(\{ seasons: seasons\.length > 0 \|\| \(liveShow\?\.numberOfSeasons \?\? 0\) > 0,\s*cast: \(liveShow\?\.cast\?\.length \?\? 0\) > 0 \|\| \(liveShow\?\.creators\?\.length \?\? 0\) > 0 \}, reveal\.revealed, props\.openKey, \['seasons', 'cast'\] as const\)/);
  });
  it('Seasons and Cast pills read the frozen row, never the live lists alone', () => {
    expect(tv).toMatch(/\{!!tvRow\?\.seasons && \(/);
    expect(tv).toMatch(/\{!!tvRow\?\.cast && \(castForDisplay\.length > 0/);
    expect(tv).not.toMatch(/\{\(seasons\.length > 0 \|\| \(show\?\.numberOfSeasons \?\? 0\) > 0\) && \(/);
  });
  it('the title row carries data-modal-logo (as the movie sheet) for the QA probe', () => {
    expect(tv).toMatch(/ref=\{logoRef\}[\s\S]{0,900}data-modal-logo=""/);
  });
});
