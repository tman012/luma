import { describe, it, expect } from 'vitest';
import { mergeReleases } from '@/services/allSourcesApi';

const arr = (over: object) => ({ guid: 'a1', title: 'Dune Part Two 2024 1080p WEB-DL-FLUX', infoHash: 'abc', rejected: false, origin: 'arr', ...over });
const trk = (over: object) => ({ guid: 'luma-tracker:2:x', title: 'Dune Part Two 2024 1080p WEB-DL-FLUX', infoHash: 'ABC', rejected: false, origin: 'indexer', ...over });

describe('mergeReleases', () => {
  it('keeps the Arr copy of a release on a collision', () => {
    const out = mergeReleases([arr({})], [trk({})]);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ origin: 'arr' });
  });
  it('a rejected Arr copy does not hide the tracker copy', () => {
    const out = mergeReleases([arr({ rejected: true })], [trk({}), trk({ guid: 'luma-tracker:3:y' })]);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ origin: 'indexer', rejected: false });
  });
  it('appends tracker rows that collide with nothing, including copies from two trackers', () => {
    const out = mergeReleases([], [trk({}), trk({ guid: 'luma-tracker:3:y' })]);
    expect(out).toHaveLength(2);
  });
});
