/** v226 (Taylor's S1): the phone TV Series list and its navigation. Desktop unchanged. */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TV_VIEWS, TvLibrarySettings } from '@/components/library/TvLibrarySettings';
import { PhoneSeriesList, TvSeries, phoneBarColor } from '@/components/library/TvSeries';
import type { Show } from '@/components/library/tvLibraryApi';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const TV = { id: 'p_0000aaaa', name: 'TV', path: '/media/TV', status: 'ok' };
const show = (id: number, title: string, o: Partial<Show> = {}): Show => ({
  id, tmdb_id: id, title, year: 2021, path_id: TV.id, path_name: 'TV', folder: title, poster_path: `/p${id}.jpg`, monitored: true,
  files: 0, episodes: 0, linked: 0, mismatch: 0, unrecognized: 0, aired: 70, have: 62, continuing: true, ...o,
});
const SHOWS = [show(1, 'Abbott Elementary'), show(2, 'Andor', { year: 2022, aired: 24, have: 24, continuing: false, poster_path: null })];
let loc = '';
function Where() { const l = useLocation(); loc = l.pathname + l.search; return null; }
const setPhone = (on: boolean) => vi.stubGlobal('matchMedia', (q: string) => ({
  matches: on && q.includes('max-width'), media: q, addEventListener() {}, removeEventListener() {},
}) as unknown as MediaQueryList);
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    const url = String(u);
    if (url === '/api/settings/tv') return res({ paths: [TV], upgrades: { mode: 'keep_both' }, rescan_interval_h: 6 });
    if (url === '/api/settings/tv/libraries') return res({ selected: [TV.id], libraries: [{ ...TV, selected: true }] });
    if (url === '/api/settings/tv/library') return res({ shows: SHOWS, unmatched: [], scan: { running: false, error: null, last_scan: null } });
    return res({ error: 'Not found' }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const mount = (wide: boolean) => render(
  <MemoryRouter initialEntries={['/settings/tv?view=series']}><TvLibrarySettings wide={wide} /><Where /></MemoryRouter>);

describe('v226 S1 phone Series list', () => {
  it('no filter chips, no Add series, no monitored button, no colored left edge', async () => {
    setPhone(true);
    mount(false);
    const rows = await screen.findAllByTestId('tv-phone-row');
    expect(rows).toHaveLength(2);
    for (const id of ['tv-series-filters', 'tv-filter-all', 'tv-filter-missing', 'tv-filter-continuing', 'tv-add-series', 'tv-phone-flag']) {
      expect(screen.queryByTestId(id)).toBeNull();
    }
    expect(screen.queryByText(/^(All|Missing|Continuing)\b/)).toBeNull();
    expect(screen.queryByText('Add series')).toBeNull();
    for (const r of rows) {
      expect(r.style.boxShadow).toBe('');
      expect(r.querySelector('[aria-pressed]')).toBeNull();
    }
  });

  it('a row: 40x60 poster (placeholder when missing), title, "Year · N of M", thin progress bar', () => {
    const open = vi.fn();
    render(<PhoneSeriesList shows={SHOWS} onOpen={open} />);
    const [a, b] = screen.getAllByTestId('tv-phone-row');
    const img = within(a!).getByTestId('tv-phone-poster') as HTMLImageElement;
    expect([img.style.width, img.style.height]).toEqual(['40px', '60px']);
    expect(img.getAttribute('src')).toContain('/p1.jpg');
    const empty = within(b!).getByTestId('tv-phone-poster-empty');
    expect([empty.style.width, empty.style.height]).toEqual(['40px', '60px']);
    expect(within(a!).getByTestId('tv-phone-title').textContent).toBe('Abbott Elementary');
    expect(within(a!).getByTestId('tv-phone-meta').textContent).toBe('2021 · 62 of 70');
    expect(within(b!).getByTestId('tv-phone-meta').textContent).toBe('2022 · 24 of 24');
    const bar = within(a!).getByTestId('tv-phone-bar');
    expect(bar.style.height).toBe('3px');
    expect((bar.firstElementChild as HTMLElement).style.width).toBe('88.6%');
    expect(phoneBarColor(SHOWS[0]!)).toBe('#3b82f6');
    expect(phoneBarColor(SHOWS[1]!)).toBe('#22c55e');
    expect(screen.getByTestId('tv-phone-rail')).toBeTruthy();
    fireEvent.click(a!);
    expect(open).toHaveBeenCalledWith(SHOWS[0]);
  });

  it('a full-width 5-part segmented control, icon over label, no slider; search kept', async () => {
    setPhone(true);
    mount(false);
    await screen.findAllByTestId('tv-phone-row');
    expect(screen.getByTestId('tv-search')).toBeTruthy();
    const tabs = screen.getByTestId('tv-tabs');
    expect(tabs.dataset.variant).toBe('phone');
    expect(tabs.style.width).toBe('100%');
    expect(tabs.parentElement!.className).not.toContain('tvt-scroll');
    const parts = within(tabs).getAllByRole('tab');
    expect(parts.map(p => p.textContent)).toEqual(['Series', 'Calendar', 'Import', 'Libraries', 'Options']);
    for (const p of parts) {
      expect(p.style.flexDirection).toBe('column');
      expect(p.style.minWidth).toBe('0px');
      expect(p.firstElementChild!.tagName.toLowerCase()).toBe('svg');
    }
    expect(parts[0]!.getAttribute('aria-selected')).toBe('true');
    fireEvent.click(parts[1]!);
    expect(loc).toBe('/settings/tv');
  });

  it('desktop is unchanged: text tabs, grid of posters, Add series', async () => {
    setPhone(false);
    mount(true);
    await screen.findAllByTestId('tv-series-tile');
    const tabs = screen.getByTestId('tv-tabs');
    expect(tabs.dataset.variant).toBeUndefined();
    expect(within(tabs).getAllByRole('tab').map(t => t.textContent)).toEqual(TV_VIEWS.map(v => v.label));
    expect(tabs.querySelector('svg')).toBeNull();
    expect(screen.getByTestId('tv-add-series')).toBeTruthy();
    expect(screen.queryByTestId('tv-phone-row')).toBeNull();
    cleanup();
    render(<TvSeries shows={SHOWS} />);
    expect(screen.getAllByTestId('tv-series-tile')).toHaveLength(2);
  });
});

import { phoneFill, railFit } from '@/components/library/TvSeries';
describe('v226.1 S1 follow-ups', () => {
  it('the phone bar fill is clamped to 0..1 and 0 of 0 is empty (Reviewer)', () => {
    expect(phoneFill(show(1, 'A', { have: 80, aired: 70 }))).toBe(1);
    expect(phoneFill(show(1, 'A', { have: 0, aired: 0 }))).toBe(0);
    expect(phoneFill(show(1, 'A', { have: 5, aired: 0 }))).toBe(0);
    expect(phoneFill(show(1, 'A', { have: -1, aired: 4 }))).toBe(0);
    expect(phoneFill(show(1, 'A', { have: 62, aired: 70 }))).toBeCloseTo(62 / 70);
    render(<PhoneSeriesList shows={[show(9, 'Over', { have: 9, aired: 3 })]} onOpen={() => {}} />);
    expect((screen.getByTestId('tv-phone-bar').firstElementChild as HTMLElement).style.width).toBe('100%');
  });
  it('the rail reaches 16px above the bottom both before and after it sticks (Designer)', () => {
    expect(railFit(250, 844)).toBe(844 - 250 - 16);
    expect(railFit(-400, 844)).toBe(844 - 8 - 16);   // scrolled: sticky at 8px, no gap under Z
  });
});
