/** v158c (Designer, PM priority 1–3): the TV top row is a 3-column grid (minmax(200px, 320px) 1fr auto):
 * search left, the week controls (with Today) centered in the middle column, the tabs right; under 1000px
 * of content the tabs take their own row above (right-aligned, 12px gap) with search + week below.
 * Week-grid captions wrap instead of clipping. */
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { describe, expect, it } from 'vitest';
import { TOP_COLUMNS, topRowCss } from '@/components/library/TvLibrarySettings';
import { EP_LINES, NAME_LINES } from '@/components/library/TvCalendar';

const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
const rule = (sel: string) => topRowCss.split('\n').find(l => l.startsWith(sel + '{')) || '';

describe('TV top row', () => {
  it('3 columns: search | week (centered) | tabs (end)', () => {
    expect(TOP_COLUMNS).toBe('minmax(200px, 320px) 1fr auto');
    expect(rule('.tvt-top')).toContain(`grid-template-columns:${TOP_COLUMNS}`);
    expect(rule('.tvt-top')).toContain('grid-template-areas:"search week tabs"');
    expect(rule('.tvt-top-search')).toContain('grid-area:search');
    expect(rule('.tvt-top-week')).toContain('grid-area:week;justify-self:center');
    expect(rule('.tvt-top-tabs')).toContain('grid-area:tabs;justify-self:end');
  });

  it('under 1000px of content: the tabs on their own row above, right-aligned, 12px gap', () => {
    expect(topRowCss).toContain('@container tvtop (max-width: 999px){.tvt-top{grid-template-columns:minmax(200px, 320px) 1fr;grid-template-areas:"tabs tabs" "search week"}}');
    expect(rule('.tvt-top')).toContain('row-gap:12px');
    expect(src('components/library/TvLibrarySettings.tsx')).toContain("containerType: 'inline-size', containerName: 'tvtop'");
  });

  it('Today sits in the week column beside the range (never over it)', () => {
    const s = src('components/library/TvLibrarySettings.tsx');
    const week = s.slice(s.indexOf('data-testid="tv-top-center"'), s.indexOf('data-testid="tv-top-right"'));
    expect(week).toContain('<WeekCenter');
    expect(week.indexOf('<WeekCenter')).toBeLessThan(week.indexOf('<TodayButton'));
    expect(s.slice(s.indexOf('data-testid="tv-top-right"'), s.indexOf('data-testid="tv-top-right"') + 120)).not.toContain('TodayButton');
  });

  it('week-grid captions wrap (name up to 5 lines, episode line up to 3), no one-line clip', () => {
    expect([NAME_LINES, EP_LINES]).toEqual([5, 3]);
    const s = src('components/library/TvCalendar.tsx');
    // only a phone-narrow calendar (under 600px) keeps 2 and 1 lines, as in v162; desktop 1100/1280 wraps
    expect(s).not.toContain('@container tvcal (max-width: 999px){.tvc-card [data-testid=tv-card-show]');
    expect(s).toContain('@container tvcal (max-width: 599px){.tvc-card [data-testid=tv-card-show]{-webkit-line-clamp:2!important}.tvc-card [data-testid=tv-card-title]{-webkit-line-clamp:1!important}}');
    const title = s.slice(s.indexOf('data-testid="tv-card-title"'), s.indexOf('</span>', s.indexOf('data-testid="tv-card-title"')));
    expect(title).toContain('WebkitLineClamp: EP_LINES');
    expect(title).not.toContain('...one');
  });
});
