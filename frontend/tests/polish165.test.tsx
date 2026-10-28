/** v165 (Designer polish): the calendar card's time line and the phone Downloads job row. */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { timeRange, timeRangeParts } from '@/components/library/tvCalendarApi';
import { AIR_SIZE, tvCalendarCss } from '@/components/library/TvCalendar';

vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ authenticated: true, loading: false, implicit: false, logout: vi.fn(), user: { id: 1, name: 'taylor', is_admin: true } }) }));
import { LibraryPathsSettings } from '@/components/library/LibraryPathsSettings';

describe('calendar time line', () => {
  it('drops the first AM/PM when both times share it; keeps both across noon or midnight', () => {
    expect(timeRange('2026-10-16T03:00:00Z', 58, 'America/New_York')).toBe('11:00 \u2013 11:58 PM');
    expect(timeRange('2026-10-15T14:00:00Z', 30, 'America/New_York')).toBe('10:00 \u2013 10:30 AM');
    expect(timeRange('2026-10-15T15:30:00Z', 45, 'America/New_York')).toBe('11:30 AM \u2013 12:15 PM');
    expect(timeRange('2026-10-16T03:30:00Z', 40, 'America/New_York')).toBe('11:30 PM \u2013 12:10 AM');
    expect(timeRange('2026-10-16T03:00:00Z', null, 'America/New_York')).toBe('11:00 PM');
    expect(timeRange(null, 30)).toBeNull();
  });

  it('v166: 12px at every width, never shrunk; v179: the card shows the start time only, one nowrap line', () => {
    expect(AIR_SIZE).toBe(12);
    expect(tvCalendarCss).not.toMatch(/cqi|tv-air|tvcard/);                 // no shrinking, no ellipsis rule
    expect(timeRangeParts('2026-10-16T03:00:00Z', 58, 'America/New_York')).toEqual(['11:00 \u2013', '11:58 PM']);
    expect(timeRangeParts('2026-10-15T15:30:00Z', 45, 'America/New_York')).toEqual(['11:30 AM \u2013', '12:15 PM']);
    expect(timeRangeParts('2026-10-16T03:00:00Z', null, 'America/New_York')).toEqual(['11:00 PM']);
    expect(timeRangeParts(null, 30)).toBeNull();
    const card = readFileSync(resolve(__dirname, '../src/components/library/TvCalendar.tsx'), 'utf8');
    expect(card).toMatch(/data-testid="tv-air" style=\{\{ display: 'block', fontSize: AIR_SIZE, lineHeight: '16px', color: W50, whiteSpace: 'nowrap' \}\}/);
    expect(card).not.toContain('tv-air-part');                              // v179: no range on the card, so nothing to split
  });
});

describe('Downloads section (v320)', () => {
  beforeEach(() => {
    (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__ = true;
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: false, media: q,
      addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false }));
    const res = (b: unknown) => ({ ok: true, status: 200, json: async () => b }) as Response;
    vi.stubGlobal('fetch', vi.fn(async (u: string) => {
      const url = String(u);
      if (url.endsWith('/api/settings/library-paths')) return res({ paths: [], rules: [], fallback_path_id: null, path_map: [], transfer: 'copy',
        downloads: { path: '/downloads', status: 'ok' },
        failed: [{ hash: 'f'.repeat(40), title: 'Heat (1995)', since: 1, reason: 'no_space' }],
        not_found: [{ title: 'Ronin', since: 1 }] });
      if (url.endsWith('/api/settings/tv/downloads')) return res({ failed: [
        { hash: '1'.repeat(40), name: 'Severance', state: 'failed', error: 'insufficient space', at: 1, retry: true }] });
      return res({});
    }));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); delete (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__; });

  it('shows only the Downloads folder control — no history rows', async () => {
    render(<LibraryPathsSettings wide={false} />);
    const box = await screen.findByTestId('lp-downloads');
    expect((within(box).getByTestId('lp-downloads-path') as HTMLInputElement).value).toBe('/downloads');
    expect(within(box).queryByTestId('lp-tv-job')).toBeNull();
    expect(within(box).queryByTestId('lp-failed')).toBeNull();
    expect(within(box).queryByTestId('lp-not-found')).toBeNull();
  });
});
