/** Settings > TV Import (Designer spec): the Import row, the progress card, Review (tiers, checks, picker), Import all. */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TvLibrarySettings as Bare } from '@/components/library/TvLibrarySettings';
import { COLLAPSE_MS, PICKER_MAX, initialReview, popoverLeft } from '@/components/library/TvImport';
import { importedText, lastImportText, sortReview, startingPick, startsChecked, type Unmatched } from '@/components/library/tvLibraryApi';

const Tv = (p: { wide: boolean }): ReactElement => <MemoryRouter><Bare {...p} /></MemoryRouter>;
const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const PATH = { id: 'p_0000aaaa', name: 'TV', path: '/media/TV', status: 'ok' };
const c = (tmdb_id: number, title: string, year: number | null) => ({ tmdb_id, title, year, poster_path: `/p${tmdb_id}.jpg` });
const row = (id: number, folder: string, tier: Unmatched['tier'], cands = [c(id * 10, folder, 2000)], pick: number | null = null): Unmatched => ({
  id, path_id: PATH.id, path_name: 'TV', folder, guess_title: folder, guess_year: null, candidates: cands,
  reason: tier === 'duplicate' ? 'duplicate' : 'not_exact', tier, pick: tier === 'likely' ? (pick ?? cands[0]!.tmdb_id) : null,
});
const LOVE = row(1, 'Love', 'likely', [c(9000001, 'Love', 2016), c(9000005, '#Love', 2026)]);
const NONE = row(2, 'Zzz Nothing', 'none', []);
const DUP = row(3, 'The Staircase (2022)', 'duplicate', [c(122196, 'The Staircase', 2022)]);
const LONG = row(4, 'Laguna Beach - The Real Orange County', 'likely', [c(3000, 'Laguna Beach: The Real Orange County', 2004)]);
const SIX = Array.from({ length: 6 }, (_, i) => c(500 + i, `Result ${i + 1}`, 2010 + i));

let library: Record<string, unknown>;
let progress: Record<string, unknown>[];
const calls: { url: string; method: string; body: unknown }[] = [];
let importAllGate: Promise<void> | null;

beforeEach(() => {
  calls.length = 0;
  importAllGate = null;
  library = { shows: [], unmatched: [LOVE, NONE, DUP, LONG], scan: { running: false, error: null, last_scan: null } };
  progress = [];
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    const url = String(u);
    const method = init?.method || 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (url === '/api/settings/tv') return res({ paths: [PATH], upgrades: { mode: 'keep_both' }, rescan_interval_h: 6 });
    if (url === '/api/settings/tv/libraries') return res({ selected: [PATH.id], libraries: [{ ...PATH, selected: true }] });
    if (url === '/api/settings/tv/library') return res(library);
    if (url === '/api/settings/tv/import' && method === 'POST') return res({ scan: { running: true, error: null, last_scan: null } }, 202);
    if (url === '/api/settings/tv/import') {
      const p = progress.shift() || { running: false, error: null, last_scan: 1_760_000_000, total: 4, done: 4, matched: 2, review: 2, none: 0 };
      return res(p);
    }
    if (url === '/api/settings/tv/search') return res({ results: [] });
    if (url.startsWith('/api/settings/tv/search?q=')) return res({ results: url.includes('tmdb%3A') ? [c(1396, 'Breaking Bad', 2008)] : SIX });
    if (url === '/api/settings/tv/import-all') {
      if (importAllGate) await importAllGate;
      const ids = (body.items as { id: number }[]).map(i => i.id);
      library = { ...library, unmatched: (library.unmatched as Unmatched[]).filter(x => !ids.includes(x.id)),
        shows: ids.map(id => ({ id: 100 + id, tmdb_id: id, title: `Show ${id}`, year: 2000, path_id: PATH.id, path_name: 'TV',
          folder: `F${id}`, poster_path: null, monitored: false, files: 1, episodes: 1, linked: 1, mismatch: 0, unrecognized: 0 })) };
      return res({ imported: ids, failed: [], ...library });
    }
    return res({ error: 'Not found' }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

const review = () => screen.findByTestId('tv-review');
const rowsOf = () => screen.getAllByTestId('tv-review-row');
const checks = () => screen.getAllByTestId('tv-review-check').map(b => b.getAttribute('aria-checked'));

describe('Import row', () => {
  it('Import is 40px on desktop and 44px on phone, with the last import 12px to its right in 13px white 50%', async () => {
    library = { ...library, scan: { running: false, error: null, last_scan: new Date(2026, 9, 17, 21, 2).getTime() / 1000 } };
    const { unmount } = render(<Tv wide />);
    const btn = await screen.findByTestId('tv-import-start');
    expect([btn.textContent, btn.style.height]).toEqual(['Import', '40px']);
    const idle = screen.getByTestId('tv-import-idle');
    expect(idle.style.gap).toBe('12px');
    const last = screen.getByTestId('tv-last-import');
    expect([last.style.fontSize, last.style.color]).toEqual(['13px', 'rgba(255, 255, 255, 0.5)']);
    unmount();
    render(<Tv wide={false} />);
    expect((await screen.findByTestId('tv-import-start')).style.height).toBe('44px');
  });

  it('lastImportText', () => {
    const now = new Date(2026, 9, 17, 22, 0).getTime();
    expect(lastImportText(new Date(2026, 9, 17, 21, 2).getTime() / 1000, now)).toBe('Last import 9:02 PM');
    expect(lastImportText(null)).toBe('');
    expect(importedText(1)).toBe('Imported 1 show');
    expect(importedText(12)).toBe('Imported 12 shows');
  });
});

describe('progress', () => {
  it('replaces the row: Importing, done of total, an accent bar and the three chips; polls only its endpoint', async () => {
    progress = [
      { running: true, error: null, last_scan: null, total: 687, done: 312, matched: 290, review: 18, none: 4 },
      { running: true, error: null, last_scan: null, total: 687, done: 500, matched: 470, review: 25, none: 5 },
    ];
    render(<Tv wide />);
    fireEvent.click(await screen.findByTestId('tv-import-start'));
    const card = await screen.findByTestId('tv-import-progress');
    expect(screen.queryByTestId('tv-import-start')).toBeNull();
    expect(screen.queryByTestId('tv-review')).toBeNull();                  // Review comes back when it ends
    await waitFor(() => expect(within(card).getByTestId('tv-import-count').textContent).toBe('312 of 687'));
    expect(within(card).getByText('Importing').style.fontSize).toBe('15px');
    expect(card.style.padding).toBe('16px');
    const bar = within(card).getByTestId('tv-import-bar');
    expect(bar.style.width).toBe('45.4%');
    expect(bar.parentElement!.style.height).toBe('4px');
    expect(bar.parentElement!.style.borderRadius).toBe('999px');
    expect(['tv-chip-matched', 'tv-chip-review', 'tv-chip-none'].map(id => within(card).getByTestId(id).textContent))
      .toEqual(['Matched 290', 'Review 18', 'No match 4']);
    const chip = within(card).getByTestId('tv-chip-matched');
    expect([chip.style.height, chip.style.fontSize, chip.style.fontWeight]).toEqual(['24px', '12px', '600']);
    const libLoads = calls.filter(x => x.url === '/api/settings/tv/library').length;
    await waitFor(() => expect(within(card).getByTestId('tv-import-count').textContent).toBe('500 of 687'), { timeout: 3000 });
    expect(calls.filter(x => x.url === '/api/settings/tv/library').length).toBe(libLoads);   // no page reloads while it runs
    await screen.findByTestId('tv-import-start', {}, { timeout: 3000 });                    // collapses back to idle
    await review();
  });

  it('collapses with a height transition of at most 200ms', async () => {
    render(<Tv wide />);
    const wrap = await screen.findByTestId('tv-import');
    expect(wrap.style.transition).toBe(`height ${COLLAPSE_MS}ms ease`);
    expect(COLLAPSE_MS).toBeLessThanOrEqual(200);
  });

  it('an error shows Import failed in red 13px with a Retry pill', async () => {
    progress = [{ running: false, error: "Couldn't reach TMDB", last_scan: null, total: 0, done: 0, matched: 0, review: 0, none: 0 }];
    render(<Tv wide />);
    fireEvent.click(await screen.findByTestId('tv-import-start'));
    const err = await screen.findByTestId('tv-import-error');
    const text = within(err).getByText('Import failed');
    expect([text.style.color, text.style.fontSize]).toEqual(['rgb(239, 68, 68)', '13px']);
    expect(within(err).getByTestId('tv-import-retry').style.borderRadius).toBe('999px');
    fireEvent.click(within(err).getByTestId('tv-import-retry'));
    await screen.findByTestId('tv-import-start');
    expect(calls.filter(x => x.url === '/api/settings/tv/import' && x.method === 'POST')).toHaveLength(2);
  });
});

describe('Review', () => {
  it('is sorted no match, duplicate, likely, with an amber count and nothing else', async () => {
    render(<Tv wide />);
    const sec = await review();
    expect(rowsOf().map(r => r.getAttribute('data-tier'))).toEqual(['none', 'duplicate', 'likely', 'likely']);
    expect(within(sec).getByTestId('tv-review-count').textContent).toBe('4');
    const pill = within(sec).getByTestId('tv-review-count');
    expect([pill.style.height, pill.style.fontSize, pill.style.color]).toEqual(['20px', '12px', 'rgb(245, 158, 11)']);
    expect(sec.querySelector('h3')!.textContent).toBe('Review4');
  });

  it('likely rows are checked with their guess; no match and duplicate rows are not', async () => {
    render(<Tv wide />);
    await review();
    expect(checks()).toEqual(['false', 'false', 'true', 'true']);
    const triggers = screen.getAllByTestId('tv-picker-trigger').map(t => t.textContent);
    expect(triggers).toEqual(['Choose show', 'The Staircase (2022)', 'Laguna Beach: The Real Orange County (2004)', 'Love (2016)']);
    expect(within(rowsOf()[1]!).getByTestId('tv-duplicate').textContent).toBe('Duplicate');
    expect(within(rowsOf()[0]!).queryByTestId('tv-duplicate')).toBeNull();
  });

  it('desktop rows: 64px, a 280px folder column with ellipsis and a tooltip, a 40px picker up to 520px', async () => {
    render(<Tv wide />);
    await review();
    const r = rowsOf()[2]!;
    expect(r.style.minHeight).toBe('64px');
    const f = within(r).getByTestId('tv-review-folder');
    expect([f.style.width, f.style.textOverflow, f.title, f.style.fontSize]).toEqual(['280px', 'ellipsis', 'Laguna Beach - The Real Orange County', '14px']);
    const t = within(r).getByTestId('tv-picker-trigger');
    expect(t.style.height).toBe('40px');
    expect(t.parentElement!.style.maxWidth).toBe('520px');
    const img = t.querySelector('img')!;
    expect([img.getAttribute('width'), img.getAttribute('height')]).toEqual(['24', '36']);
  });

  it('phone rows: folder 13px white 70% on line one, a full-width 44px picker under it', async () => {
    render(<Tv wide={false} />);
    await review();
    const r = rowsOf()[0]!;
    expect(r.style.padding).toBe('12px');
    const f = within(r).getByTestId('tv-review-folder');
    expect([f.style.fontSize, f.style.color]).toEqual(['13px', 'rgba(255, 255, 255, 0.7)']);
    const t = within(r).getByTestId('tv-picker-trigger');
    expect([t.style.height, t.style.width]).toEqual(['44px', '100%']);
  });

  it('a no-match row is checked once a show is picked', async () => {
    render(<Tv wide />);
    await review();
    expect(screen.getAllByTestId('tv-review-check')[0]!).toHaveProperty('disabled', true);
    fireEvent.click(screen.getAllByTestId('tv-picker-trigger')[0]!);
    await waitFor(() => expect(screen.getAllByTestId('tv-picker-option')).toHaveLength(PICKER_MAX));
    fireEvent.click(screen.getAllByTestId('tv-picker-option')[1]!);
    expect(screen.queryByTestId('tv-picker')).toBeNull();
    expect(checks()[0]).toBe('true');
    expect(screen.getAllByTestId('tv-picker-trigger')[0]!.textContent).toBe('Result 2 (2011)');
  });

  it('unchecking a likely row leaves it out of Import all', async () => {
    render(<Tv wide />);
    await review();
    fireEvent.click(screen.getAllByTestId('tv-review-check')[3]!);
    expect(checks()[3]).toBe('false');
    await act(async () => fireEvent.click(screen.getByTestId('tv-import-all')));
    await waitFor(() => expect(calls.find(x => x.url === '/api/settings/tv/import-all')?.body)
      .toEqual({ items: [{ id: 4, tmdb_id: 3000 }] }));
  });
});

describe('picker', () => {
  it('desktop: a 480px popover with a 40px Search field, up to 5 shows with posters and the current one checked', async () => {
    render(<Tv wide />);
    await review();
    fireEvent.click(screen.getAllByTestId('tv-picker-trigger')[3]!);           // Love
    const p = screen.getByTestId('tv-picker');
    expect(p.style.width).toBe('480px');
    const q = within(p).getByTestId('tv-picker-search') as HTMLInputElement;
    expect([q.placeholder, q.style.height]).toEqual(['Search', '40px']);
    const opts = within(p).getAllByTestId('tv-picker-option');
    expect(opts.map(o => o.textContent)).toEqual(['Love2016', '#Love2026']);
    expect(opts.map(o => o.getAttribute('aria-selected'))).toEqual(['true', 'false']);
    expect(within(opts[0]!).getByTestId('tv-picker-current')).toBeTruthy();
    const img = opts[0]!.querySelector('img')!;
    expect([img.getAttribute('width'), img.getAttribute('height')]).toEqual(['32', '48']);
    expect(calls.some(x => x.url.startsWith('/api/settings/tv/search'))).toBe(false);   // the candidates first
  });

  it('search results (name or tmdb:/tvdb:/imdb:) replace the list', async () => {
    render(<Tv wide />);
    await review();
    fireEvent.click(screen.getAllByTestId('tv-picker-trigger')[3]!);
    const q = screen.getByTestId('tv-picker-search');
    fireEvent.change(q, { target: { value: 'tmdb:1396' } });
    fireEvent.keyDown(q, { key: 'Enter' });
    await waitFor(() => expect(screen.getAllByTestId('tv-picker-option').map(o => o.textContent)).toEqual(['Breaking Bad2008']));
    expect(calls.some(x => x.url === '/api/settings/tv/search?q=tmdb%3A1396')).toBe(true);
  });

  it('Esc and a tap outside close it', async () => {
    render(<Tv wide />);
    await review();
    fireEvent.click(screen.getAllByTestId('tv-picker-trigger')[3]!);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByTestId('tv-picker')).toBeNull();
    fireEvent.click(screen.getAllByTestId('tv-picker-trigger')[3]!);
    fireEvent.pointerDown(document.body);
    expect(screen.queryByTestId('tv-picker')).toBeNull();
  });

  it('phone: a bottom sheet at most 70% tall', async () => {
    render(<Tv wide={false} />);
    await review();
    fireEvent.click(screen.getAllByTestId('tv-picker-trigger')[3]!);
    const p = screen.getByTestId('tv-picker');
    expect([p.style.maxHeight, p.style.bottom, p.style.position]).toEqual(['70vh', '0px', 'absolute']);
    expect(screen.getByTestId('tv-picker-sheet-backdrop').style.position).toBe('fixed');
  });

  it('a popover from a trigger on the right opens leftward inside the row', () => {
    const parent = { clientWidth: 900 } as HTMLElement;
    expect(popoverLeft({ offsetLeft: 800, offsetParent: parent } as unknown as HTMLElement)).toBe(420);
    expect(popoverLeft({ offsetLeft: 300, offsetParent: parent } as unknown as HTMLElement)).toBe(300);
    expect(popoverLeft(null)).toBe(0);
  });
});

describe('Import all', () => {
  it('desktop: right-aligned, at least 160px, 40px tall; phone: full width, 48px', async () => {
    const { unmount } = render(<Tv wide />);
    await review();
    const b = screen.getByTestId('tv-import-all');
    expect([b.style.minWidth, b.style.height, b.parentElement!.style.justifyContent, b.parentElement!.style.marginTop])
      .toEqual(['160px', '40px', 'flex-end', '16px']);
    unmount();
    render(<Tv wide={false} />);
    await review();
    const p = screen.getByTestId('tv-import-all');
    expect([p.style.width, p.style.height]).toEqual(['100%', '48px']);
  });

  it('is 40% and does nothing when nothing is checked', async () => {
    library = { ...library, unmatched: [NONE, DUP] };
    render(<Tv wide />);
    await review();
    const b = screen.getByTestId('tv-import-all') as HTMLButtonElement;
    expect([b.style.opacity, b.disabled]).toEqual(['0.4', true]);
  });

  it('runs with a spinner, imported rows fade out, Review goes when empty, Shows update, and a toast says how many', async () => {
    let open!: () => void;
    importAllGate = new Promise(r => { open = r; });
    library = { ...library, unmatched: [LOVE, LONG] };
    render(<Tv wide />);
    await review();
    fireEvent.click(screen.getByTestId('tv-import-all'));
    const b = screen.getByTestId('tv-import-all') as HTMLButtonElement;
    await waitFor(() => expect(b.disabled).toBe(true));
    expect(screen.getByTestId('tv-import-all-spinner')).toBeTruthy();
    expect(b.getAttribute('aria-busy')).toBe('true');
    await act(async () => { open(); });
    await waitFor(() => expect(rowsOf().every(r => r.style.opacity === '0')).toBe(true));
    await waitFor(() => expect(screen.queryByTestId('tv-review')).toBeNull());
    expect(screen.getAllByTestId('tv-show')).toHaveLength(2);
    expect(screen.getByTestId('notice-toast').textContent).toContain('Imported 2 shows');
    expect(calls.filter(x => x.url === '/api/settings/tv/library')).toHaveLength(1);   // updated in place
  });
});

describe('review state', () => {
  it('keeps the owner\'s picks across reloads and starts new rows from their tier', () => {
    const s1 = initialReview([LOVE, NONE]);
    expect(s1.checked).toEqual({ 1: true, 2: false });
    const s2 = initialReview([LOVE, NONE, DUP], { checked: { ...s1.checked, 1: false }, picks: { ...s1.picks, 1: c(9000005, '#Love', 2026) } });
    expect(s2.checked).toEqual({ 1: false, 2: false, 3: false });
    expect(s2.picks[1]!.tmdb_id).toBe(9000005);
    expect(startingPick(DUP)!.tmdb_id).toBe(122196);
    expect(startsChecked(DUP)).toBe(false);
    // same-name shows the files don't single out: pre-picked, unchecked (PM, Oct 18)
    expect(startsChecked({ ...LOVE, reason: 'unclear' })).toBe(false);
    expect(startingPick({ ...LOVE, reason: 'unclear' })!.tmdb_id).toBe(startingPick(LOVE)!.tmdb_id);
    expect(startsChecked(LOVE)).toBe(true);
    // Import all found Love's pick in another folder: the row is a duplicate now and starts over, unchecked
    const s3 = initialReview([LOVE]);
    expect(s3.checked[1]).toBe(true);
    const nowDup = { ...LOVE, reason: 'duplicate', tier: 'duplicate' as const, pick: null, candidates: [c(9000005, '#Love', 2026)] };
    const s4 = initialReview([nowDup as Unmatched], s3);
    expect(s4.checked[1]).toBe(false);
    expect(s4.picks[1]!.tmdb_id).toBe(9000005);
    expect(initialReview([nowDup as Unmatched], { ...s4, checked: { 1: true } }).checked[1]).toBe(true);   // the owner's own check stays
    expect(sortReview([LONG, LOVE, DUP, NONE]).map(u => u.id)).toEqual([2, 3, 4, 1]);
  });
});
