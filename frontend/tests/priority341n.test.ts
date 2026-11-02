import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { renderHook } from '@testing-library/react';
import { beginSheetPriority, sheetOpening, _resetSheetPriority } from '@/loading/sheetPriority';
import { useEndPriorityOnError } from '@/hooks/useOneReveal';

const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');

describe('v341n priority window: an errored sheet ends it; skipped warms are requeued', () => {
  it('useEndPriorityOnError ends the window at once', () => {
    _resetSheetPriority(); beginSheetPriority(); expect(sheetOpening()).toBe(true);
    const h = renderHook(({ f }) => useEndPriorityOnError(f), { initialProps: { f: false } });
    expect(sheetOpening()).toBe(true);
    h.rerender({ f: true }); expect(sheetOpening()).toBe(false);
    _resetSheetPriority();
  });
  it('every layout ends it only when the sheet has no data to reveal', () => {
    for (const [f, v] of [['components/media/MovieDetailModal/ExpandedLayout.tsx', 'liveDetail'], ['components/media/MovieDetailModal/CompactLayout.tsx', 'liveDetail'],
      ['components/media/TvDetailModal/ExpandedLayout.tsx', 'liveShow'], ['components/media/TvDetailModal/CompactLayout.tsx', 'liveShow']]) {
      expect(src(f)).toContain(`useEndPriorityOnError(!loading && !!error && !${v});`);
    }
  });
  it('a warm skipped during the hold is queued and flushed after it', () => {
    const s = src('services/modalPrefetch.ts');
    expect(s).toMatch(/if \(!opts\.open && sheetOpening\(\)\) \{ queueWarm\(kind, ratingKey, tmdbId, opts\); return; \}/);
    expect(s).toMatch(/afterSheetPriority\(flushSkippedWarms\)/);
    expect(s).toMatch(/MAX_SKIPPED_WARMS = 12/);
  });
});
