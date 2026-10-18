/** v165 (Designer polish): the calendar card's time line and the phone Downloads job row. */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { timeRange } from '@/components/library/tvCalendarApi';
import { AIR_FONT, tvCalendarCss } from '@/components/library/TvCalendar';

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

  it('never wraps on desktop: one line, shrinking with the day column from 12px to at least 9px', () => {
    expect(AIR_FONT).toBe('min(12px, max(9px, 12.5cqi))');
    const q = tvCalendarCss.slice(tvCalendarCss.indexOf('@container tvcal (min-width: 600px)'));
    const rule = q.slice(0, q.indexOf('\n'));
    expect(rule).toContain('.tvc-text{container-type:inline-size;container-name:tvcard}');
    expect(rule).toContain('[data-testid=tv-air]{display:block!important;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:min(12px, max(9px, 12.5cqi))!important}');
    const card = readFileSync(resolve(__dirname, '../src/components/library/TvCalendar.tsx'), 'utf8');
    expect(card).toContain('<div className="tvc-text"');
  });
});

describe('Downloads job row on a phone', () => {
  const since = Math.floor(new Date(2026, 9, 15, 9, 5).getTime() / 1000);
  let phone = true;
  beforeEach(() => {
    phone = true;
    (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__ = true;
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q === '(max-width: 599px)' ? phone : false, media: q,
      addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false }));
    const res = (b: unknown) => ({ ok: true, status: 200, json: async () => b }) as Response;
    vi.stubGlobal('fetch', vi.fn(async (u: string) => {
      const url = String(u);
      if (url.endsWith('/api/settings/library-paths')) return res({ paths: [], rules: [], fallback_path_id: null, path_map: [], transfer: 'copy',
        downloads: { path: '/downloads', status: 'ok' }, failed: [{ hash: 'f'.repeat(40), title: 'Heat (1995)', since, reason: 'no_space' }] });
      if (url.endsWith('/api/settings/tv/downloads')) return res({ failed: [
        { hash: '1'.repeat(40), name: 'Severance.S02E03.1080p.WEB-DL.DDP5.1.H.264-GRP', state: 'failed', error: 'insufficient space', at: since, retry: true },
        { hash: '2'.repeat(40), name: 'Slow.Horses.S05E04.1080p', state: 'grab_failed', error: "Couldn't create the qBittorrent category luma-tv", at: since - 2 * 86400, retry: false }] });
      return res({});
    }));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); delete (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__; });

  it('name alone on line 1 (one line, ellipsis); red status with the real failure date on line 2; Retry on the right', async () => {
    render(<LibraryPathsSettings wide={false} />);
    const tv = await screen.findAllByTestId('lp-tv-job');
    const movie = screen.getByTestId('lp-failed');
    for (const row of [movie, ...tv]) expect(row.getAttribute('data-layout')).toBe('phone');
    const name = within(tv[0]!).getByTestId('lp-tv-job-name');
    expect([name.textContent, name.style.whiteSpace, name.style.overflow, name.style.textOverflow]).toEqual(
      ['Severance.S02E03.1080p.WEB-DL.DDP5.1.H.264-GRP', 'nowrap', 'hidden', 'ellipsis']);
    const status = within(tv[0]!).getByTestId('lp-tv-job-status');
    expect(status.textContent).toBe('Not enough space \u00b7 Since Oct 15');
    expect(status.style.color).toBe('var(--color-danger)');
    expect(within(movie).getByTestId('lp-failed-status').textContent).toBe('Not enough space \u00b7 Since Oct 15');
    expect(within(tv[1]!).getByTestId('lp-tv-job-status').textContent).toBe("Couldn't create the qBittorrent category luma-tv \u00b7 Since Oct 13");
    // Retry is the row's last child (right), only on no-space rows
    expect(tv[0]!.lastElementChild!.getAttribute('data-testid')).toBe('lp-tv-job-retry');
    expect(movie.lastElementChild!.getAttribute('data-testid')).toBe('lp-failed-retry');
    expect(within(tv[1]!).queryByTestId('lp-tv-job-retry')).toBeNull();
    expect(movie.textContent).toBe('Heat (1995)Not enough space \u00b7 Since Oct 15Retry');
  });

  it('desktop is unchanged: name and date on the left, status, then Retry', async () => {
    phone = false;
    render(<LibraryPathsSettings wide />);
    const tv = await screen.findAllByTestId('lp-tv-job');
    expect(tv[0]!.getAttribute('data-layout')).toBeNull();
    expect(tv[0]!.textContent).toBe('Severance.S02E03.1080p.WEB-DL.DDP5.1.H.264-GRPSince Oct 15Not enough spaceRetry');
    expect(screen.getByTestId('lp-failed').textContent).toBe('Heat (1995)Since Oct 15Not enough spaceRetry');
  });
});
