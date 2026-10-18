/** Settings > TV: the Libraries selector (Library paths picked as TV libraries), Import, Review, shows with
 * "Wrong show?", and Upgrades (Replace / Keep both). The Import card and Review in detail: tvImport.test.tsx. */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import type { ReactElement } from 'react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SECTION_GAP, TvLibrarySettings as TvSettingsBare, UPGRADE_OPTIONS } from '@/components/library/TvLibrarySettings';
import { episodeCount, lastScanText, needsReview, reviewCount, showLabel, showSubtitle } from '@/components/library/tvLibraryApi';

const TvLibrarySettings = (p: { wide: boolean }): ReactElement => <MemoryRouter><TvSettingsBare {...p} /></MemoryRouter>;
const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const PATH = { id: 'p_0000aaaa', name: 'TV', path: '/media/TV', status: 'ok' };
const ANIME = { id: 'p_0000bbbb', name: 'Anime', path: '/media/Anime', status: 'ok' };
let selected: string[];
const SEV = { id: 1, tmdb_id: 95396, title: 'Severance', year: 2022, path_id: PATH.id, path_name: 'TV', folder: 'Severance (2022)',
  poster_path: null, monitored: false, files: 6, episodes: 4, linked: 3, mismatch: 2, unrecognized: 1,
  review: ["S02E40 isn't an episode of this show", "S03E01 isn't an episode of this show", '1 file not recognized'] };
const DARK = { ...SEV, id: 3, tmdb_id: 70523, title: 'Dark', year: 2017, folder: 'Dark', files: 1, episodes: 1, linked: 1, mismatch: 0, unrecognized: 0 };
const MYS = { id: 7, path_id: PATH.id, path_name: 'TV', folder: 'Mystery Show', guess_title: 'Mystery Show', guess_year: null,
  candidates: [{ tmdb_id: 1001, title: 'Mystery Show', year: 2015 }, { tmdb_id: 1002, title: 'Mystery Show', year: 2019 }],
  reason: 'not_exact', tier: 'likely', pick: 1002 };
let settings: Record<string, unknown>;
let library: Record<string, unknown>;
const calls: { url: string; method: string; body: unknown }[] = [];

beforeEach(() => {
  calls.length = 0;
  selected = [PATH.id];
  settings = { paths: [PATH], upgrades: { mode: 'keep_both' }, rescan_interval_h: 6, naming: {}, automation: {} };
  library = { shows: [SEV, DARK], unmatched: [MYS], scan: { running: false, error: null, last_scan: null } };
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    const url = String(u);
    const method = init?.method || 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (url === '/api/settings/tv' && method === 'GET') return res(settings);
    if (url === '/api/settings/tv' && method === 'PUT') { settings = { ...settings, ...body }; return res(settings); }
    if (url === '/api/settings/tv/libraries') {
      if (method === 'PUT') selected = body.selected;
      return res({ selected, libraries: [PATH, ANIME].map(l => ({ ...l, selected: selected.includes(l.id) })) });
    }
    if (url === '/api/settings/tv/library') return res(library);
    if (url === '/api/settings/tv/import' && method === 'POST') {
      const { error } = library.scan as { error: string | null };
      library = { ...library, scan: { running: false, error, last_scan: error ? null : 1_760_000_000 } };
      return res({ scan: { running: true, error: null, last_scan: null } }, 202);
    }
    if (url === '/api/settings/tv/import') {
      return res({ running: false, error: (library.scan as { error: string | null }).error, last_scan: 1_760_000_000,
        total: 3, done: 3, matched: 2, review: 1, none: 0 });
    }
    if (url === '/api/settings/tv/import-all') {
      library = { ...library, unmatched: [], shows: [SEV, DARK, { ...SEV, id: 2, tmdb_id: body.items[0].tmdb_id, title: 'Mystery Show', year: 2019, folder: 'Mystery Show' }] };
      return res({ imported: [7], failed: [], ...library });
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
  it('lists the paths, the shows with their files, the review rows and Upgrades', async () => {
    render(<TvLibrarySettings wide />);
    await screen.findByText('Severance (2022)');
    await screen.findAllByTestId('lib-select-row');
    const libs = within(screen.getByTestId('tv-paths')).getAllByTestId('lib-select-row');
    expect(libs.map(r => [r.textContent, r.getAttribute('aria-checked')])).toEqual([['TV/media/TV', 'true'], ['Anime/media/Anime', 'false']]);
    const rows = screen.getAllByTestId('tv-show');
    expect(within(rows[0]!).getByTestId('tv-show-subtitle').textContent).toBe('4 episodes \u00b7 3 need review');
    expect(within(rows[0]!).getByTestId('tv-review-dot').getAttribute('aria-label')).toBe('Needs review');
    expect(within(rows[1]!).getByTestId('tv-show-subtitle').textContent).toBe('1 episode');
    expect(within(rows[1]!).queryByTestId('tv-review-dot')).toBeNull();
    expect(screen.queryByText(/linked|mismatch|unrecognized/)).toBeNull();
    expect(within(screen.getByTestId('tv-review')).getByText('Mystery Show')).toBeTruthy();
    expect(screen.queryByTestId('tv-last-import')).toBeNull();          // nothing before the first import
    expect(screen.getByTestId('tv-upgrades-keep_both').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByTestId('tv-upgrades-replace_verified').getAttribute('aria-checked')).toBe('false');
  });

  it('selects Library paths as TV libraries; no add-path or folder picker', async () => {
    render(<TvLibrarySettings wide={false} />);
    const rows = await screen.findAllByTestId('lib-select-row');
    expect(screen.queryByTestId('tv-add-path')).toBeNull();
    expect(screen.queryByText('Add path')).toBeNull();
    expect(screen.queryByTestId('lp-browse')).toBeNull();
    const n = calls.filter(c => c.url === '/api/settings/tv/library').length;
    fireEvent.click(rows[1]!);
    await waitFor(() => expect(screen.getAllByTestId('lib-select-row')[1]!.getAttribute('aria-checked')).toBe('true'));
    expect(calls.filter(c => c.method === 'PUT' && c.url === '/api/settings/tv/libraries').map(c => c.body))
      .toEqual([{ selected: ['p_0000aaaa', 'p_0000bbbb'] }]);
    await waitFor(() => expect(calls.filter(c => c.url === '/api/settings/tv/library').length).toBe(n + 1));
  });

  it('Import hides when no library is selected and shows once one is', async () => {
    selected = [];
    settings = { ...settings, paths: [] };
    render(<TvLibrarySettings wide />);
    const rows = await screen.findAllByTestId('lib-select-row');
    expect(screen.queryByTestId('tv-import-start')).toBeNull();
    fireEvent.click(rows[0]!);
    await screen.findByTestId('tv-import-start');
  });

  it('Import starts an import, shows its progress, then the last import', async () => {
    render(<TvLibrarySettings wide />);
    fireEvent.click(await screen.findByTestId('tv-import-start'));
    await waitFor(() => expect(screen.getByTestId('tv-last-import').textContent).toMatch(/^Last import /));
    expect(calls.some(c => c.url === '/api/settings/tv/import' && c.method === 'POST')).toBe(true);
    expect(calls.some(c => c.url === '/api/settings/tv/import' && c.method === 'GET')).toBe(true);
  });

  it('an import error shows Import failed with Retry', async () => {
    library = { ...library, scan: { running: false, error: 'Add a TMDB key in Connectors', last_scan: null } };
    render(<TvLibrarySettings wide />);
    fireEvent.click(await screen.findByTestId('tv-import-start'));
    const err = await screen.findByTestId('tv-import-error');
    expect(err.textContent).toBe('Import failed' + 'Retry');
    library = { ...library, scan: { running: false, error: null, last_scan: null } };
    fireEvent.click(within(err).getByTestId('tv-import-retry'));
    await screen.findByTestId('tv-last-import');
  });

  it('Import all imports the checked rows with their picks', async () => {
    render(<TvLibrarySettings wide />);
    fireEvent.click(await screen.findByTestId('tv-import-all'));
    await waitFor(() => expect(screen.queryByTestId('tv-review')).toBeNull());
    expect(calls.find(c => c.url === '/api/settings/tv/import-all')?.body).toEqual({ items: [{ id: 7, tmdb_id: 1002 }] });
    expect(screen.getAllByTestId('tv-show')).toHaveLength(3);
    expect(screen.getByTestId('notice-toast').textContent).toContain('Imported 1 show');
  });

  it('Wrong show? opens the picker, searches and rematches; the current show has the check', async () => {
    render(<TvLibrarySettings wide />);
    fireEvent.click((await screen.findAllByTestId('tv-wrong-show'))[0]!);
    await waitFor(() => expect(screen.getAllByTestId('tv-picker-option').map(b => b.textContent)).toEqual(['The Office2005', 'Severance2022']));
    expect(calls.find(c => c.url.startsWith('/api/settings/tv/search'))?.url).toBe('/api/settings/tv/search?q=Severance');
    const opts = screen.getAllByTestId('tv-picker-option');
    expect(opts.map(o => o.getAttribute('aria-selected'))).toEqual(['false', 'true']);
    fireEvent.change(screen.getByTestId('tv-picker-search'), { target: { value: 'office' } });
    fireEvent.keyDown(screen.getByTestId('tv-picker-search'), { key: 'Enter' });
    await waitFor(() => expect(calls.some(c => c.url === '/api/settings/tv/search?q=office')).toBe(true));
    await act(async () => fireEvent.click(screen.getAllByTestId('tv-picker-option')[0]!));
    await screen.findByText('The Office (2005)', { selector: 'span' });
    expect(calls.find(c => c.url === '/api/settings/tv/shows/1/match')?.body).toEqual({ tmdb_id: 2316 });
    expect(screen.queryByTestId('tv-picker')).toBeNull();
  });

  it('Upgrades saves Replace and Keep both', async () => {
    render(<TvLibrarySettings wide />);
    fireEvent.click(await screen.findByTestId('tv-upgrades-replace_verified'));
    await waitFor(() => expect(screen.getByTestId('tv-upgrades-replace_verified').getAttribute('aria-checked')).toBe('true'));
    expect(calls.find(c => c.url === '/api/settings/tv' && c.method === 'PUT')?.body).toEqual({ upgrades: { mode: 'replace_verified' } });
    expect(UPGRADE_OPTIONS.map(o => o.label)).toEqual(['Replace', 'Keep both']);
  });

  it('the review dot is 8px amber; Libraries, Import, Review, Shows in that order with the section gap', async () => {
    render(<TvLibrarySettings wide />);
    const dot = await screen.findByTestId('tv-review-dot');
    await screen.findAllByTestId('lib-select-row');
    expect([dot.style.width, dot.style.height, dot.style.background]).toEqual(['8px', '8px', 'var(--color-warning)']);
    const section = screen.getByTestId('tv-section');
    expect(section.style.gap).toBe(`${SECTION_GAP}px`);
    const kids = Array.from(section.children).map(c => c.getAttribute('data-testid')).filter(Boolean);
    expect(kids.slice(0, 5)).toEqual(['tv-paths', 'tv-import', 'tv-review', 'tv-shows', 'tv-upgrades']);
    expect(screen.getByTestId('tv-import').style.marginTop).toBe('');
  });

  it('no helper text, sentence case', () => {
    const src = ['TvLibrarySettings.tsx', 'TvImport.tsx']
      .map(f => readFileSync(resolve(__dirname, '../src/components/library/' + f), 'utf8')).join('\n');
    expect(src).not.toMatch(/hint=|note=|helper/i);
    for (const label of ['Libraries', 'Review', 'Shows', 'Upgrades', 'Wrong show?', 'Import', 'Import all', 'Importing',
      'Import failed', 'Retry', 'Choose show', 'Duplicate', 'Matched', 'No match', 'Keep both', '"Search"'])
      expect(src).toContain(label);
    expect(src).not.toMatch(/Add path|PathForm|FolderBrowser|addTvPath|Unmatched<|>Scan<|Scanning/);
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
    // M23: wraps on phones, never clipped (neither the text nor the row it sits in)
    const sub = within(rows[0]!).getByTestId('tv-show-subtitle');
    expect(sub.style.whiteSpace).toBe('normal');
    expect([toggle.style.whiteSpace, toggle.style.overflow, toggle.style.textOverflow]).toEqual(['normal', 'visible', 'clip']);
    // same 13px type as a plain subtitle (a 'font' shorthand used to reset it to 16px)
    expect(toggle.getAttribute('style')).not.toMatch(/(^|; )font: /);
    expect(toggle.style.fontSize).toBe('13px');
    const plain = within(rows[1]!).getByTestId('tv-show-subtitle').parentElement!;
    expect([plain.style.whiteSpace, plain.style.overflow]).toEqual(['normal', 'visible']);
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    const items = Array.from(screen.getByTestId('tv-review-list').querySelectorAll('li')).map(li => li.textContent);
    expect(items).toEqual(["S02E40 isn't an episode of this show", "S03E01 isn't an episode of this show", '1 file not recognized']);
    expect(screen.queryByText(/linked|mismatch|unrecognized/)).toBeNull();
    fireEvent.click(toggle);
    expect(screen.queryByTestId('tv-review-list')).toBeNull();
  });
});
