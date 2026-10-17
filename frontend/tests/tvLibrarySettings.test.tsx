/** Settings > TV: library paths, Scan, shows with "Wrong show?", unmatched
 * folders with "Match", and Upgrades (Replace / Keep both). */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SECTION_GAP, TvLibrarySettings, UPGRADE_OPTIONS } from '@/components/library/TvLibrarySettings';
import { episodeCount, lastScanText, needsReview, reviewCount, showLabel, showSubtitle } from '@/components/library/tvLibraryApi';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const PATH = { id: 't_0000aaaa', name: 'TV', path: '/media/TV', status: 'ok' };
const SEV = { id: 1, tmdb_id: 95396, title: 'Severance', year: 2022, path_id: PATH.id, path_name: 'TV', folder: 'Severance (2022)',
  poster_path: null, monitored: false, files: 6, episodes: 4, linked: 3, mismatch: 2, unrecognized: 1,
  review: ['S03E01 is in Season 2', "S02E40 isn't an episode of this show", '1 file not recognized'] };
const DARK = { ...SEV, id: 3, tmdb_id: 70523, title: 'Dark', year: 2017, folder: 'Dark', files: 1, episodes: 1, linked: 1, mismatch: 0, unrecognized: 0 };
const MYS = { id: 7, path_id: PATH.id, path_name: 'TV', folder: 'Mystery Show', guess_title: 'Mystery Show', guess_year: null,
  candidates: [{ tmdb_id: 1001, title: 'Mystery Show', year: 2015 }, { tmdb_id: 1002, title: 'Mystery Show', year: 2019 }],
  reason: 'not_exact' };
let settings: Record<string, unknown>;
let library: Record<string, unknown>;
const calls: { url: string; method: string; body: unknown }[] = [];

beforeEach(() => {
  calls.length = 0;
  settings = { paths: [PATH], upgrades: { mode: 'keep_both' }, rescan_interval_h: 6, naming: {}, automation: {} };
  library = { shows: [SEV, DARK], unmatched: [MYS], scan: { running: false, error: null, last_scan: null } };
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    const url = String(u);
    const method = init?.method || 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (url === '/api/settings/tv' && method === 'GET') return res(settings);
    if (url === '/api/settings/tv' && method === 'PUT') { settings = { ...settings, ...body }; return res(settings); }
    if (url === '/api/settings/tv/paths' && method === 'POST') {
      if (body.path === '/etc') return res({ errors: { path: 'Not allowed' } }, 400);
      settings = { ...settings, paths: [PATH, { id: 't_0000bbbb', ...body, status: 'ok' }] };
      return res(settings);
    }
    if (url === '/api/settings/tv/library') return res(library);
    if (url === '/api/settings/tv/scan') {
      library = { ...library, scan: { running: false, error: null, last_scan: 1_760_000_000 } };
      return res({ scan: { running: true, error: null, last_scan: null } }, 202);
    }
    if (url.startsWith('/api/settings/tv/search')) return res({ results: [{ tmdb_id: 2316, title: 'The Office', year: 2005 }, { tmdb_id: 95396, title: 'Severance', year: 2022 }] });
    if (url === '/api/settings/tv/unmatched/7/match') {
      library = { ...library, unmatched: [], shows: [SEV, DARK, { ...SEV, id: 2, tmdb_id: body.tmdb_id, title: 'Mystery Show', year: 2019, folder: 'Mystery Show' }] };
      return res({ ok: true, series_id: 2, ...library });
    }
    if (url === '/api/settings/tv/shows/1/match') {
      if (body.tmdb_id === 95396) return res({ error: 'That show is already matched to another folder' }, 409);
      library = { ...library, shows: [{ ...SEV, tmdb_id: body.tmdb_id, title: 'The Office', year: 2005 }, DARK] };
      return res({ ok: true, series_id: 1, ...library });
    }
    return res({ error: 'Not found' }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('Settings > TV', () => {
  it('lists the paths, the shows with their files, the unmatched folders and Upgrades', async () => {
    render(<TvLibrarySettings wide />);
    await screen.findByText('Severance (2022)');
    expect(within(screen.getByTestId('tv-paths')).getByText('/media/TV')).toBeTruthy();
    const rows = screen.getAllByTestId('tv-show');
    expect(within(rows[0]!).getByTestId('tv-show-subtitle').textContent).toBe('4 episodes \u00b7 3 need review');
    expect(within(rows[0]!).getByTestId('tv-review-dot').getAttribute('aria-label')).toBe('Needs review');
    expect(within(rows[1]!).getByTestId('tv-show-subtitle').textContent).toBe('1 episode');
    expect(within(rows[1]!).queryByTestId('tv-review-dot')).toBeNull();
    expect(screen.queryByText(/linked|mismatch|unrecognized/)).toBeNull();
    expect(within(screen.getByTestId('tv-unmatched')).getByText('Mystery Show')).toBeTruthy();
    expect(screen.getByTestId('tv-scan-status').textContent).toBe('Not scanned yet');
    expect(screen.getByTestId('tv-upgrades-keep_both').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByTestId('tv-upgrades-replace_verified').getAttribute('aria-checked')).toBe('false');
  });

  it('adds a TV path and shows field errors', async () => {
    render(<TvLibrarySettings wide={false} />);
    fireEvent.click(await screen.findByTestId('tv-add-path'));
    fireEvent.change(screen.getByTestId('lp-name'), { target: { value: 'Anime' } });
    fireEvent.change(screen.getByTestId('lp-dir'), { target: { value: '/etc' } });
    fireEvent.click(screen.getByTestId('lp-save'));
    await screen.findByText('Not allowed');
    fireEvent.change(screen.getByTestId('lp-dir'), { target: { value: '/media/Anime' } });
    fireEvent.click(screen.getByTestId('lp-save'));
    await screen.findByText('/media/Anime');
    expect(calls.filter(c => c.url === '/api/settings/tv/paths').map(c => c.body)).toEqual([
      { name: 'Anime', path: '/etc' }, { name: 'Anime', path: '/media/Anime' }]);
  });

  it('Scan starts a scan and shows the last scan', async () => {
    render(<TvLibrarySettings wide />);
    fireEvent.click(await screen.findByTestId('tv-scan'));
    await waitFor(() => expect(screen.getByTestId('tv-scan-status').textContent).toMatch(/^Last scan /));
    expect(calls.some(c => c.url === '/api/settings/tv/scan' && c.method === 'POST')).toBe(true);
  });

  it('a scan error shows instead of the time', async () => {
    library = { ...library, scan: { running: false, error: 'Add a TMDB key in Connectors', last_scan: null } };
    render(<TvLibrarySettings wide />);
    await waitFor(() => expect(screen.getByTestId('tv-scan-status').textContent).toBe('Add a TMDB key in Connectors'));
  });

  it('Match offers the candidates and matches the folder', async () => {
    render(<TvLibrarySettings wide />);
    fireEvent.click(await screen.findByTestId('tv-match'));
    const picker = screen.getByTestId('tv-picker');
    expect(within(picker).getAllByTestId('tv-candidate').map(b => b.textContent)).toEqual(['Mystery Show (2015)', 'Mystery Show (2019)']);
    expect(calls.some(c => c.url.startsWith('/api/settings/tv/search'))).toBe(false);   // candidates first
    await act(async () => fireEvent.click(within(picker).getByText('Mystery Show (2019)')));
    await waitFor(() => expect(screen.queryByTestId('tv-unmatched')).toBeNull());
    expect(calls.find(c => c.url === '/api/settings/tv/unmatched/7/match')?.body).toEqual({ tmdb_id: 1002 });
    expect(screen.getAllByTestId('tv-show')).toHaveLength(3);
  });

  it('Wrong show? searches and rematches, without offering the current show', async () => {
    render(<TvLibrarySettings wide />);
    fireEvent.click((await screen.findAllByTestId('tv-wrong-show'))[0]!);
    await waitFor(() => expect(screen.getAllByTestId('tv-candidate').map(b => b.textContent)).toEqual(['The Office (2005)']));
    expect(calls.find(c => c.url.startsWith('/api/settings/tv/search'))?.url).toBe('/api/settings/tv/search?q=Severance');
    fireEvent.change(screen.getByTestId('tv-search'), { target: { value: 'office' } });
    fireEvent.click(screen.getByTestId('tv-search-go'));
    await waitFor(() => expect(calls.some(c => c.url === '/api/settings/tv/search?q=office')).toBe(true));
    await act(async () => fireEvent.click(screen.getByText('The Office (2005)')));
    await screen.findByText('The Office (2005)', { selector: 'span' });
    expect(calls.find(c => c.url === '/api/settings/tv/shows/1/match')?.body).toEqual({ tmdb_id: 2316 });
  });

  it('Upgrades saves Replace and Keep both', async () => {
    render(<TvLibrarySettings wide />);
    fireEvent.click(await screen.findByTestId('tv-upgrades-replace_verified'));
    await waitFor(() => expect(screen.getByTestId('tv-upgrades-replace_verified').getAttribute('aria-checked')).toBe('true'));
    expect(calls.find(c => c.url === '/api/settings/tv' && c.method === 'PUT')?.body).toEqual({ upgrades: { mode: 'replace_verified' } });
    expect(UPGRADE_OPTIONS.map(o => o.label)).toEqual(['Replace', 'Keep both']);
  });

  it('the review dot is 8px amber, and Scan is its own section with the section gap', async () => {
    render(<TvLibrarySettings wide />);
    const dot = await screen.findByTestId('tv-review-dot');
    expect([dot.style.width, dot.style.height, dot.style.background]).toEqual(['8px', '8px', 'var(--color-warning)']);
    const section = screen.getByTestId('tv-section');
    expect(section.style.gap).toBe(`${SECTION_GAP}px`);
    const kids = Array.from(section.children).map(c => c.getAttribute('data-testid')).filter(Boolean);
    expect(kids.slice(0, 4)).toEqual(['tv-paths', 'tv-scan-row', 'tv-unmatched', 'tv-shows']);
    expect(screen.getByTestId('tv-scan-row').style.marginTop).toBe('');
  });

  it('no helper text, sentence case', () => {
    const src = readFileSync(resolve(__dirname, '../src/components/library/TvLibrarySettings.tsx'), 'utf8');
    expect(src).not.toMatch(/hint=|note=|helper/i);
    for (const label of ['Library paths', 'Unmatched', 'Shows', 'Upgrades', 'Wrong show?', 'Match', 'Scan', 'Add path', 'Keep both'])
      expect(src).toContain(label);
  });
});

describe('labels', () => {
  it('formats shows, counts and the last scan', () => {
    expect(showLabel({ title: 'Dark', year: null })).toBe('Dark');
    expect(episodeCount(SEV)).toBe('4 episodes');
    expect(episodeCount({ ...SEV, episodes: 0 })).toBe('0 episodes');
    expect(episodeCount(DARK)).toBe('1 episode');
    expect(needsReview(SEV)).toBe(true);
    expect(needsReview({ ...SEV, mismatch: 0 })).toBe(true);           // unrecognized alone
    expect(needsReview({ ...SEV, unrecognized: 0 })).toBe(true);       // mismatch alone
    expect(needsReview(DARK)).toBe(false);
    const now = new Date(2026, 9, 17, 17, 0).getTime();
    expect(lastScanText(new Date(2026, 9, 17, 15, 5).getTime() / 1000, now)).toBe('Last scan 3:05 PM');
    expect(lastScanText(new Date(2026, 9, 16, 9, 0).getTime() / 1000, now)).toBe('Last scan Oct 16');
  });
});


describe('needs review, in words (Tester tv1-8646479 M7/M8)', () => {
  it('subtitle counts what needs review, singular and plural', () => {
    expect(showSubtitle(SEV)).toBe('4 episodes \u00b7 3 need review');
    expect(reviewCount({ ...SEV, mismatch: 0 })).toBe('1 needs review');
    expect(showSubtitle(DARK)).toBe('1 episode');
  });

  it('tapping the subtitle lists what needs review; shows with nothing to review have no toggle', async () => {
    render(<TvLibrarySettings wide />);
    await screen.findByText('Severance (2022)');
    const rows = screen.getAllByTestId('tv-show');
    expect(within(rows[1]!).queryByTestId('tv-review-toggle')).toBeNull();
    const toggle = within(rows[0]!).getByTestId('tv-review-toggle');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByTestId('tv-review-list')).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    const items = Array.from(screen.getByTestId('tv-review-list').querySelectorAll('li')).map(li => li.textContent);
    expect(items).toEqual(['S03E01 is in Season 2', "S02E40 isn't an episode of this show", '1 file not recognized']);
    expect(screen.queryByText(/linked|mismatch|unrecognized/)).toBeNull();
    fireEvent.click(toggle);
    expect(screen.queryByTestId('tv-review-list')).toBeNull();
  });
});
