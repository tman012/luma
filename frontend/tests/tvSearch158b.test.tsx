/** v158b: manual episode/season search chips, Designer must-fixes (opaque overlays, glass cards and
 * chips, active bookmark #3b82f6, calendar bookmark hidden under 1000px, modal surface, the blue
 * desktop Add to watchlist with a +). */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EpisodeMark, LumaMonitorContext, MonitorMark, SearchMark } from '@/components/library/tvMonitor';
import { searchOutcome, type SeriesView } from '@/components/library/tvCalendarApi';
import { MODAL_SURFACE } from '@/components/library/TvEdit';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const calls: { url: string; method: string; body: unknown }[] = [];
let handler: (url: string, method: string, body: unknown) => Response | null;
beforeEach(() => {
  calls.length = 0;
  handler = () => null;
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    const url = String(u); const method = init?.method || 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    return handler(url, method, body) ?? res({ error: 'Not found' }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const VIEW: SeriesView = {
  id: 7, title: 'Severance', year: 2022, tmdb_id: 95396, monitored: true, monitor: 'future', monitor_new_seasons: 'all',
  monitor_specials: false, season_folder: true, series_type: 'standard', profile_id: 1, path_id: 'p_0000aaaa', folder: 'Severance (2022)',
  path: '/media/TV/Severance (2022)', seasons: [{ season: 2, monitored: true }],
  episodes: [{ id: 23, season: 2, episode: 3, monitored: true }],
};
const ctx = { view: VIEW, series: vi.fn(), season: vi.fn(), episode: vi.fn() };
const RESULT = { ok: true, wanted: 1, searched: 1, results: 3, grabs_allowed: false, error: null,
  picks: [{ title: 'Severance.S02E03.1080p.WEB-DL-B', quality: '1080p-web', episodes: [23], grabbed: false, error: 'Grabs are off on the test instance' }] };
const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');

describe('manual search', () => {
  it('the episode chip searches that episode and shows the outcome as a status', async () => {
    handler = (url, method) => (method === 'POST' && url === '/api/settings/tv/shows/7/search' ? res(RESULT) : null);
    render(<LumaMonitorContext.Provider value={ctx}><div style={{ position: 'relative' }}><EpisodeMark season={2} episode={3} /></div></LumaMonitorContext.Provider>);
    const chip = screen.getByTestId('tv-search-episode');
    expect(chip.getAttribute('aria-label')).toBe('Search: S02E03');
    const bare = chip.cloneNode(true) as HTMLElement; bare.querySelectorAll('style').forEach(n => n.remove());
    expect(bare.textContent).toBe('');                                                      // icon only
    fireEvent.click(chip);
    await waitFor(() => expect(screen.getByTestId('tv-search-note').textContent).toBe('Grabs are off on the test instance'));
    expect(calls).toEqual([{ url: '/api/settings/tv/shows/7/search', method: 'POST', body: { episode_id: 23 } }]);
  });

  it('the season chip searches the season', async () => {
    handler = () => res({ ...RESULT, picks: [{ ...RESULT.picks[0], grabbed: true, error: null }] });
    render(<LumaMonitorContext.Provider value={ctx}><SearchMark season={2} /></LumaMonitorContext.Provider>);
    fireEvent.click(screen.getByTestId('tv-search-season'));
    await waitFor(() => expect(screen.getByTestId('tv-search-note').textContent).toBe('Grabbed 1 release'));
    expect(calls[0]!.body).toEqual({ season: 2 });
  });

  it('outcome lines are sentence case', () => {
    expect(searchOutcome({ ok: true, data: { ...RESULT, wanted: 0, picks: [] } })).toBe('Nothing missing');
    expect(searchOutcome({ ok: true, data: { ...RESULT, picks: [] } })).toBe('No releases found');
    expect(searchOutcome({ ok: true, data: { ...RESULT, error: 'No TV trackers are on', picks: [] } })).toBe('No TV trackers are on');
    expect(searchOutcome({ ok: false, status: 401, error: 'Sign in' })).toBe('Sign in');
  });

  it('nothing outside a Luma TV show', () => {
    const { container } = render(<SearchMark season={2} />);
    expect(container.innerHTML).toBe('');
  });
});

describe('Designer must-fix', () => {
  it('bookmark chips: glass .10 off, #3b82f6 on', () => {
    render(<><MonitorMark testid="a" on={false} onToggle={() => {}} label="x" /><MonitorMark testid="b" on onToggle={() => {}} label="y" /></>);
    expect(screen.getByTestId('a').style.background).toBe('rgba(255, 255, 255, 0.1)');
    expect(screen.getByTestId('b').style.background).toBe('rgb(59, 130, 246)');
  });

  it('the Episodes and Seasons overlays are opaque #15102a; cards glass .06 with a .08 border; stills .04; chips .10', () => {
    const s = src('components/media/TvDetailModal/ExpandedLayout.tsx');
    expect(s).toContain("const OVERLAY_SURFACE = '#15102a';");
    expect((s.match(/background: OVERLAY_SURFACE,/g) || []).length).toBe(2);
    expect(s).not.toContain("background: 'rgba(0,0,0,0.88)'");
    expect(s).toContain("background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)'");
    expect(s).toContain("const PLACEHOLDER_GLASS = 'rgba(255,255,255,0.04)';");
    expect(s).toContain("background: 'rgba(255,255,255,0.10)', backdropFilter: 'blur(12px)'");
    expect(src('components/media/TvDetailModal/SonarrStatus.tsx')).toContain("background: 'rgba(255,255,255,0.10)', border: '1px solid rgba(255,255,255,0.08)'");
  });

  it('no #000/#111/#1a1a/#222/#252525/#0a0a left in the touched components', () => {
    for (const p of ['components/media/TvDetailModal/ExpandedLayout.tsx', 'components/media/TvDetailModal/SonarrStatus.tsx',
      'components/library/tvMonitor.tsx', 'components/library/TvEdit.tsx', 'components/library/TvCalendar.tsx']) {
      expect(src(p)).not.toMatch(/#(000|111|1a1a|222|252525|0a0a)\b/i);
    }
  });

  it('the calendar bookmark hides when the calendar is under 1000px wide', () => {
    const s = src('components/library/TvCalendar.tsx');
    expect(s).toContain("containerType: 'inline-size', containerName: 'tvcal'");
    expect(s).toContain('@container tvcal (max-width: 999px){.tvc-card .tvc-bm{display:none!important}}');
  });

  it('Edit and profile modals: rgba(21,16,42,.92) with a 40px blur', () => {
    expect(MODAL_SURFACE).toBe('rgba(21,16,42,0.92)');
    const s = src('components/library/TvEdit.tsx');
    expect(s).toContain("background: MODAL_SURFACE, border: '1px solid rgba(255,255,255,0.08)',\n          backdropFilter: 'blur(40px)'");
  });

  it('desktop: the blue Add to watchlist with a +, kept in minimal builds', () => {
    const s = src('components/media/TvDetailModal/ExpandedLayout.tsx');
    const i = s.indexOf('testid="tv-add-watchlist-desktop"');
    expect(i).toBeGreaterThan(0);
    const block = s.slice(s.lastIndexOf('<span data-modal-keep=""', i), s.indexOf('</span>', i));
    expect(block).toContain('tone="blue"');
    expect(block).toContain('label="Add to watchlist"');
    expect(block).toContain('<line x1="12" y1="5" x2="12" y2="19" />');
    expect(s).toContain("? (hovered ? '#60a5fa' : '#3b82f6')");
  });
});
