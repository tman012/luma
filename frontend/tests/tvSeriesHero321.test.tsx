/** v321 (PM, Taylor): the series detail header: no poster, the card takes the banner's natural aspect ratio with no
 * strip below, the backdrop fallback, the fade-in (instant on a cache hit or reduced motion), the centered title and the bottom meta/fields, the larger back link, and no "Search your shows" box on the detail route. */
process.env.TZ = 'America/New_York';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TvSeriesHero, BANNER_RATIO, PATH_MAX, TITLE_MAX, BOTTOM_MAX, GAP_TITLE, GAP_META, GAP_FIELDS, GAP_SEASONS, PAGE_GAP } from '@/components/library/TvSeriesHero';
import { TvSeriesDetail } from '@/components/library/TvSeriesDetail';
import { TvLibrarySettings } from '@/components/library/TvLibrarySettings';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const TV = { id: 'p_0000aaaa', name: 'TV', path: '/media/TV', status: 'ok' };
const BANNER = 'https://artworks.thetvdb.com/banners/graphical/1-g.jpg';
let detail: Record<string, unknown>;
const base = () => ({
  id: 1, title: 'VisionQuest', year: 2026, tmdb_id: 1, monitored: true, monitor: 'all', monitor_new_seasons: 'all', monitor_specials: false,
  season_folder: true, series_type: 'standard', profile_id: 1, path_id: TV.id, folder: 'VisionQuest', path: '/media/Video/TV/VisionQuest',
  network: 'Disney+', status: 'Returning Series', overview: null, poster_path: '/p.jpg', backdrop_path: '/b.jpg', banner_url: BANNER,
  profile_name: 'TFlix', size: 11.5 * 1024 ** 3, have: 6, total: 6, seasons: [], episodes: [],
});
let reduce = false;
beforeEach(() => {
  detail = base(); reduce = false;
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('reduced-motion') ? reduce : false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    const url = String(u);
    if (url === '/api/settings/tv') return res({ paths: [TV], upgrades: { mode: 'keep_both' }, rescan_interval_h: 6 });
    if (url === '/api/settings/tv/libraries') return res({ selected: [TV.id], libraries: [{ ...TV, selected: true }] });
    if (url === '/api/settings/tv/library') return res({ shows: [], unmatched: [], scan: { running: false, error: null, last_scan: null } });
    if (url === '/api/settings/tv/shows/1/detail') return res(detail);
    return res({ error: 'Not found' }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const FACTS: [string, string, string][] = [['Quality profile', 'TFlix', 'profile'], ['Path', '/media/Video/TV/VisionQuest', 'path'], ['Size', '11.5 GB', 'size']];
const hero = (p: Partial<Parameters<typeof TvSeriesHero>[0]> = {}) => render(
  <TvSeriesHero title="VisionQuest" meta={['2026', 'Disney+', 'Returning series']} facts={FACTS} bannerUrl={BANNER} backdropPath="/b.jpg"
    phone={false} edit={<button type="button">Edit</button>} flag={null} {...p} />);
const load = (img: HTMLElement, w: number, h: number) => {
  Object.defineProperty(img, 'naturalWidth', { value: w, configurable: true });
  Object.defineProperty(img, 'naturalHeight', { value: h, configurable: true });
  fireEvent.load(img);
};
// jsdom drops aspect-ratio from style; the card mirrors it in data-ratio (the same value)
const ratioOf = (el: HTMLElement) => Number(el.dataset.ratio);

describe('v321 series header', () => {
  it('has no poster; the card takes the banner ratio (758/140 until it loads) nothing overlaid but Edit', async () => {
    render(<TvSeriesDetail seriesId={1} wide onBack={() => {}} say={() => {}} />);
    const card = await screen.findByTestId('tv-detail-header');
    expect(screen.queryByTestId('tv-detail-poster')).toBeNull();
    expect(card.querySelector('img[src*="w185"]')).toBeNull();
    expect(ratioOf(card)).toBeCloseTo(BANNER_RATIO, 5);
    const img = screen.getByTestId('tv-detail-art');
    expect(img.getAttribute('src')).toContain('host=tvdb');
    load(img, 1000, 250);
    expect(ratioOf(card)).toBeCloseTo(4, 5);
    // the art fills the box edge to edge (no crop, no stretch: the box is the art's own ratio), no strip inside
    expect(img.style.objectFit).toBe('fill');
    expect(img.style.width).toBe('100%'); expect(img.style.height).toBe('100%'); expect(img.style.inset).toBe('0px');
    expect(card.style.height).toBe('');
    // v9: clean art; only the art and the Edit/bookmark corner are inside the card
    expect(card.querySelector('[data-testid="tv-detail-facts"]')).toBeNull();
    expect(card.children.length).toBe(2);
    for (const c of Array.from(card.children) as HTMLElement[]) expect(c.style.position).toBe('absolute');
  });
  it('a different banner ratio gives the card that ratio', () => {
    hero();
    load(screen.getByTestId('tv-detail-art'), 758, 300);
    expect(ratioOf(screen.getByTestId('tv-detail-header'))).toBeCloseTo(758 / 300, 5);
  });
  it('no banner: the backdrop, cover at center 22%, same box; neither: plain #111318', () => {
    hero({ bannerUrl: null });
    const card = screen.getByTestId('tv-detail-header');
    expect(card.dataset.art).toBe('backdrop');
    const img = screen.getByTestId('tv-detail-art');
    expect(img.style.objectFit).toBe('cover'); expect(img.style.objectPosition).toBe('center 22%');
    load(img, 1280, 720);
    expect(ratioOf(card)).toBeCloseTo(BANNER_RATIO, 5);
    cleanup();
    hero({ bannerUrl: null, backdropPath: null });
    expect(screen.getByTestId('tv-detail-header').dataset.art).toBe('none');
    expect(screen.queryByTestId('tv-detail-art')).toBeNull();
    expect(screen.getByTestId('tv-detail-header').style.background).toMatch(/rgb\(17, 19, 24\)|#111318/);
  });
  it('a banner that fails to load falls back to the backdrop', () => {
    hero();
    fireEvent.error(screen.getByTestId('tv-detail-art'));
    expect(screen.getByTestId('tv-detail-header').dataset.art).toBe('backdrop');
  });
  it('fades in 240ms on load; instant on a cache hit and with reduced motion', () => {
    hero();
    const img = screen.getByTestId('tv-detail-art');
    expect(img.style.opacity).toBe('0');
    load(img, 758, 140);
    expect(img.style.opacity).toBe('1');
    expect(img.style.transition).toContain('opacity');
    expect(img.style.transition).toContain('240ms');
    expect(screen.getByTestId('tv-detail-art').style.transform).toBe('');
    cleanup();
    reduce = true;
    hero();
    const r = screen.getByTestId('tv-detail-art');
    expect(screen.getByTestId('tv-detail-art').style.transform).toBe('');
    load(r, 758, 140);
    expect(r.style.opacity).toBe('1'); expect(r.style.transition).toBe('none');
    cleanup();
    reduce = false;
    const orig = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'complete');
    Object.defineProperty(HTMLImageElement.prototype, 'complete', { get: () => true, configurable: true });
    Object.defineProperty(HTMLImageElement.prototype, 'naturalWidth', { get: () => 758, configurable: true });
    try {
      hero();
      const c = screen.getByTestId('tv-detail-art');
      expect(c.style.opacity).toBe('1'); expect(c.style.transition).toBe('none');
    } finally {
      if (orig) Object.defineProperty(HTMLImageElement.prototype, 'complete', orig);
      delete (HTMLImageElement.prototype as unknown as Record<string, unknown>).naturalWidth;
    }
  });
  it('the banner is clean art with only Edit; below it, centered: title, meta, then the fields row (v9 spacing)', () => {
    hero({ edit: <button type="button" data-testid="e">Edit</button> });
    const card = screen.getByTestId('tv-detail-header');
    const t = screen.getByTestId('tv-detail-title');
    const meta = screen.getByTestId('tv-detail-meta');
    const row = screen.getByTestId('tv-detail-text');
    for (const el of [t, meta, row]) expect(card.contains(el)).toBe(false);
    expect(card.contains(screen.getByTestId('e'))).toBe(true);
    expect(screen.queryByTestId('tv-detail-fade')).toBeNull();                                        // no fade on the art
    expect(t.tagName).toBe('H1'); expect(t.textContent).toBe('VisionQuest');
    expect(t.style.fontSize).toBe('36px'); expect(t.style.fontWeight).toBe('700'); expect(t.style.fontFamily).toContain('Google Sans Flex');
    expect([t.style.textAlign, t.style.textOverflow, t.style.whiteSpace, t.style.maxWidth]).toEqual(['center', 'ellipsis', 'nowrap', `min(${TITLE_MAX}px, 100%)`]);
    expect(t.style.margin).toBe(`${GAP_TITLE}px auto 0px`);
    expect([...screen.getByTestId('tv-detail-hero').children].map(c => c.getAttribute('data-testid')))
      .toEqual(['tv-detail-header', 'tv-detail-title', 'tv-detail-meta', 'tv-detail-text']);
    expect([meta.style.margin, meta.style.fontSize, meta.style.textAlign]).toEqual([`${GAP_META}px auto 0px`, '14px', 'center']);
    expect(meta.style.color).toMatch(/rgb\(201, 202, 210\)|#c9cad2/);
    expect(meta.textContent).toBe('2026 \u00b7 Disney+ \u00b7 Returning series');
    expect([row.style.margin, row.style.alignItems]).toEqual([`${GAP_FIELDS}px auto 0px`, 'center']);
    expect(row.style.maxWidth).toContain(`${BOTTOM_MAX}px`);
    expect([GAP_TITLE, GAP_META, GAP_FIELDS, GAP_SEASONS]).toEqual([20, 6, 16, 28]);
    expect(screen.getByTestId('tv-detail-hero').style.marginBottom).toBe(`${GAP_SEASONS - PAGE_GAP}px`);
    const lab = row.querySelector('dt')!, val = row.querySelector('dd')!;
    expect([lab.style.fontSize, lab.style.textShadow, val.style.fontSize, val.style.textShadow]).toEqual(['11.5px', '', '13px', '']);
  });
  it('fields row below the card (with Episodes), path ellipsis; Edit keeps its dark fill', async () => {
    render(<TvSeriesDetail seriesId={1} wide onBack={() => {}} say={() => {}} />);
    const facts = await screen.findByTestId('tv-detail-facts');
    expect(facts.style.justifyContent).toBe('center'); expect(facts.style.gap).toBe('40px');
    expect(screen.getByTestId('tv-detail-fact-episodes').textContent).toBe('Episodes6 of 6');        // in the row
    expect(facts.closest('[data-testid="tv-detail-header"]')).toBeNull();                          // v9: below the card
    expect(screen.queryByTestId('tv-detail-episodes')).toBeNull();                                   // no separate count line
    // below the card: the title, the meta, then the seasons
    const hero = screen.getByTestId('tv-detail-hero');
    expect([...hero.children].map(c => c.getAttribute('data-testid'))).toEqual(['tv-detail-header', 'tv-detail-title', 'tv-detail-meta', 'tv-detail-text']);
    expect(screen.getByTestId('tv-detail-fact-path').style.maxWidth).toBe(`${PATH_MAX}px`);
    const pv = screen.getByTestId('tv-detail-fact-path').querySelector('dd')!;
    expect(pv.getAttribute('title')).toBe(pv.textContent);                                   // the full path on hover
    expect(pv.style.textOverflow).toBe('ellipsis');
    for (const f of ['profile', 'path', 'size', 'episodes']) expect(screen.getByTestId(`tv-detail-fact-${f}`).style.textAlign).toBe('center');
    expect(screen.getByTestId('tv-detail-edit').style.background).toMatch(/rgba\(17, 19, 24, 0\.94\)/);
  });
  it('the back link is 17px semibold, an 18px chevron, at least 44px tall', async () => {
    render(<TvSeriesDetail seriesId={1} wide onBack={() => {}} say={() => {}} />);
    const b = await screen.findByTestId('tv-detail-back');
    expect(b.style.fontSize).toBe('17px'); expect(b.style.fontWeight).toBe('600'); expect(b.style.minHeight).toBe('44px');
    expect(screen.getByTestId('tv-detail-back-chevron').getAttribute('width')).toBe('18');
  });
  it('no "Search your shows" box on the detail route; still on the Series list', async () => {
    render(<MemoryRouter initialEntries={['/settings/tv?view=series&show=1']}><TvLibrarySettings wide /></MemoryRouter>);
    await screen.findByTestId('tv-detail-header');
    expect(screen.queryByPlaceholderText('Search your shows')).toBeNull();
    cleanup();
    render(<MemoryRouter initialEntries={['/settings/tv?view=series']}><TvLibrarySettings wide /></MemoryRouter>);
    await waitFor(() => expect(screen.getByPlaceholderText('Search your shows')).toBeTruthy());
  });
});
