// v217 (PM): test builds stop hiding prod 1b4ea74 features behind isMinimal(): reviews fetch ('Cast & Reviews'),
// the RT score fill, TV Seasons / Cast (wide) and Episodes / Seasons (phone).
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const S = resolve(__dirname, '../src');
const read = (f: string) => readFileSync(resolve(S, f), 'utf8');

describe('minimal builds match prod 1b4ea74 (v217)', () => {
  it('reviews are fetched in minimal builds', () => {
    expect(read('components/media/TopReviews.tsx')).not.toMatch(/isMinimal/);
    expect(read('hooks/useReviews.ts')).not.toMatch(/isMinimal/);
    expect(read('components/media/TopReviews.tsx')).toContain('/api/reviews/top');
  });
  it('the wide movie popup labels the pill Cast & Reviews when reviews load', () => {
    expect(read('components/media/MovieDetailModal/ExpandedLayout.tsx')).toContain("label={topReviews.length > 0 ? 'Cast & Reviews' : 'Cast'}");
  });
  it('the RT fill runs in minimal builds; OMDb stays off (no connector)', () => {
    const s = read('hooks/useMovieDetail.ts');
    expect(s).not.toMatch(/noRatings/);
    expect(s).toMatch(/if \(missingRtCritic \|\| missingRtAudience\) \{/);
    expect(s).toMatch(/isMinimal\(\) \? Promise\.resolve\(null\)/);
  });
  it('phone TV Episodes / Seasons are kept', () => {
    expect(read('components/media/TvDetailModal/CompactLayout.tsx')).toMatch(/<span data-modal-keep="" style=\{\{ display: 'contents' \}\}>\{activeSeasonData \? \(/);
  });
});
