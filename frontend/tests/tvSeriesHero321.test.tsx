/** v321 (PM, Taylor): the series detail header: no poster, the card takes the banner's natural aspect ratio with no
 * strip below, no art box without a banner (no backdrop fallback), the fade-in (instant on a cache hit or reduced motion), the centered title and the bottom meta/fields, the larger back link, and no "Search your shows" box on the detail route. */
process.env.TZ = 'America/New_York';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TvSeriesHero, BANNER_RATIO, PATH_MAX, TITLE_MAX, BOTTOM_MAX, GAP_TITLE, GAP_META, GAP_FIELDS, GAP_SEASONS, PAGE_GAP, HERO_MIN_H, HERO_MIN_H_PHONE, EDIT_INSET, EDIT_INSET_PHONE, CONTROLS_H, TYPE_DESK, PATH_MAX_DESK, BOTTOM_MAX_DESK } from '@/components/library/TvSeriesHero';
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
  <TvSeriesHero title="VisionQuest" meta={['2026', 'Disney+', 'Returning series']} facts={FACTS} bannerUrl={BANNER}
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
    expect(img.style.objectFit).toBe('cover');
    expect(img.style.width).toBe('100%'); expect(img.style.height).toBe('100%'); expect(img.style.inset).toBe('0px');
    expect(card.style.height).toBe('');
    // v9: clean art; only the art and the Edit/bookmark corner are inside the card
    expect(card.querySelector('[data-testid="tv-detail-facts"]')).toBeNull();
    expect(card.children.length).toBe(3);
    for (const c of Array.from(card.children) as HTMLElement[]) expect(c.style.position).toBe('absolute');
  });
  it('a different banner ratio gives the card that ratio', () => {
    hero();
    load(screen.getByTestId('tv-detail-art'), 758, 300);
    expect(ratioOf(screen.getByTestId('tv-detail-header'))).toBeCloseTo(758 / 300, 5);
  });
  it('no banner: no art box at all, even with a backdrop; the title leads and Edit sits at the header top right', () => {
    hero({ bannerUrl: null, edit: <button type="button" data-testid="e">Edit</button> });
    expect(screen.queryByTestId('tv-detail-header')).toBeNull();
    expect(screen.queryByTestId('tv-detail-art')).toBeNull();
    expect(document.querySelector('img')).toBeNull();
    const h = screen.getByTestId('tv-detail-hero');
    expect(h.dataset.mode).toBe('none');
    const corner = screen.getByTestId('tv-detail-corner');
    expect([corner.style.position, corner.style.top, corner.style.right]).toEqual(['absolute', '0px', '0px']);
    expect(corner.contains(screen.getByTestId('e'))).toBe(true);
    const t = screen.getByTestId('tv-detail-title');
    expect([t.className, t.style.margin, t.style.fontSize]).toEqual(['', '0px auto', '40px']);
    expect([...h.children].map(c => c.getAttribute('data-testid'))).toEqual(['tv-detail-corner', 'tv-detail-title', 'tv-detail-meta', 'tv-detail-text']);
  });
  it('the detail page: a show with a backdrop but no banner renders no art box', async () => {
    detail = { ...base(), banner_url: null, backdrop_path: '/b.jpg' };
    render(<TvSeriesDetail seriesId={1} wide onBack={() => {}} say={() => {}} />);
    expect((await screen.findByTestId('tv-detail-title')).className).toBe('');
    expect(screen.queryByTestId('tv-detail-header')).toBeNull();
    expect(screen.queryByTestId('tv-detail-art')).toBeNull();
    expect(screen.getByTestId('tv-detail-corner').contains(screen.getByTestId('tv-detail-edit'))).toBe(true);
  });
  it('a banner that fails to load counts as no banner (no art box, visible title)', () => {
    hero();
    fireEvent.error(screen.getByTestId('tv-detail-art'));
    expect(screen.queryByTestId('tv-detail-header')).toBeNull();
    expect(screen.getByTestId('tv-detail-title').className).toBe('');
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
  it('no banner: the visible centered title, then meta at 12px, then the fields row (v11 spacing)', () => {
    hero({ edit: <button type="button" data-testid="e">Edit</button>, bannerUrl: null });   // no banner: the visible title
    const t = screen.getByTestId('tv-detail-title');
    const meta = screen.getByTestId('tv-detail-meta');
    const row = screen.getByTestId('tv-detail-text');
    expect(screen.getByTestId('tv-detail-corner').contains(screen.getByTestId('e'))).toBe(true);
    expect(t.tagName).toBe('H1'); expect(t.textContent).toBe('VisionQuest');
    expect(t.style.fontSize).toBe('40px'); expect(t.style.fontWeight).toBe('700'); expect(t.style.fontFamily).toContain('Google Sans Flex');
    expect([t.style.textAlign, t.style.textOverflow, t.style.whiteSpace, t.style.maxWidth]).toEqual(['center', 'ellipsis', 'nowrap', `min(${TITLE_MAX}px, calc(100% - 240px))`]);   // clear of Edit
    expect(t.style.margin).toBe('0px auto');
    expect([...screen.getByTestId('tv-detail-hero').children].map(c => c.getAttribute('data-testid')))
      .toEqual(['tv-detail-corner', 'tv-detail-title', 'tv-detail-meta', 'tv-detail-text']);
    expect([meta.style.margin, meta.style.fontSize, meta.style.textAlign]).toEqual([`${TYPE_DESK.gapMeta}px auto 0px`, '17px', 'center']);
    expect(meta.style.color).toMatch(/rgb\(201, 202, 210\)|#c9cad2/);
    expect(meta.textContent).toBe('2026 \u00b7 Disney+ \u00b7 Returning series');
    expect([row.style.margin, row.style.alignItems]).toEqual([`${TYPE_DESK.gapFields}px auto 0px`, 'center']);
    expect(row.style.maxWidth).toContain(`${BOTTOM_MAX_DESK}px`);
    expect([GAP_TITLE, GAP_META, GAP_FIELDS, GAP_SEASONS]).toEqual([20, 12, 16, 28]);
    expect(screen.getByTestId('tv-detail-hero').style.marginBottom).toBe(`${GAP_SEASONS - PAGE_GAP}px`);
    const lab = row.querySelector('dt')!, val = row.querySelector('dd')!;
    expect([lab.style.fontSize, lab.style.textShadow, val.style.fontSize, val.style.textShadow]).toEqual(['14px', '', '16px', '']);
  });
  it('with a banner: the h1 stays in the DOM, visually hidden (clip, not display:none); the meta takes its slot at 20px', () => {
    hero();
    const t = screen.getByTestId('tv-detail-title');
    expect(t.tagName).toBe('H1'); expect(t.textContent).toBe('VisionQuest');
    expect(screen.getByRole('heading', { level: 1, name: 'VisionQuest' })).toBe(t);
    expect(t.className).toBe('sr-only');
    expect([t.style.position, t.style.width, t.style.height, t.style.overflow, t.style.clip]).toEqual(['absolute', '1px', '1px', 'hidden', 'rect(0px, 0px, 0px, 0px)']);
    expect(t.style.display).not.toBe('none'); expect(t.getAttribute('aria-hidden')).toBeNull();
    const meta = screen.getByTestId('tv-detail-meta');
    expect(meta.style.margin).toBe(`${TYPE_DESK.gapTitle}px auto 0px`);
    expect(screen.getByTestId('tv-detail-text').style.margin).toBe(`${TYPE_DESK.gapFields}px auto 0px`);
    // a banner that fails: no art, the title shows again with meta 12px under it
    fireEvent.error(screen.getByTestId('tv-detail-art'));
    expect(screen.getByTestId('tv-detail-title').className).toBe('');
    expect(screen.getByTestId('tv-detail-title').style.fontSize).toBe('40px');
    expect(screen.getByTestId('tv-detail-meta').style.margin).toBe(`${TYPE_DESK.gapMeta}px auto 0px`);
  });
  it('fields row below the card (with Episodes), path ellipsis; Edit keeps its dark fill', async () => {
    render(<TvSeriesDetail seriesId={1} wide onBack={() => {}} say={() => {}} />);
    const facts = await screen.findByTestId('tv-detail-facts');
    expect(facts.style.justifyContent).toBe('center'); expect(facts.style.gap).toBe('48px');
    expect(screen.getByTestId('tv-detail-fact-episodes').textContent).toBe('Episodes6 of 6');        // in the row
    expect(facts.closest('[data-testid="tv-detail-header"]')).toBeNull();                          // v9: below the card
    expect(screen.queryByTestId('tv-detail-episodes')).toBeNull();                                   // no separate count line
    // below the card: the title, the meta, then the seasons
    const hero = screen.getByTestId('tv-detail-hero');
    expect([...hero.children].map(c => c.getAttribute('data-testid'))).toEqual(['tv-detail-header', 'tv-detail-title', 'tv-detail-meta', 'tv-detail-text']);
    expect(screen.getByTestId('tv-detail-fact-path').style.maxWidth).toBe(`${PATH_MAX_DESK}px`);
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

describe('r2 (Designer NO on e799265e): Edit on the banner at phone width', () => {
  const phoneAt390 = () => {
    vi.stubGlobal('innerWidth', 390);
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('reduced-motion') ? false : /max-width/.test(q) && !/min-width/.test(q), media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
  };
  it('at 390 with a banner, Edit sits inside the banner card (top right, beside the bookmark), not in the back row', async () => {
    phoneAt390();
    render(<TvSeriesDetail seriesId={1} wide={false} onBack={() => {}} say={() => {}} />);
    const card = await screen.findByTestId('tv-detail-header');
    await waitFor(() => expect(screen.getAllByTestId('tv-detail-edit')).toHaveLength(1));
    const edit = screen.getByTestId('tv-detail-edit');
    expect(card.contains(edit)).toBe(true);
    const corner = screen.getByTestId('tv-detail-corner');
    expect(corner.contains(edit)).toBe(true);
    expect(corner.contains(screen.getByTestId('tv-detail-monitored'))).toBe(true);
    expect(corner.style.top).toBe('9px');
    expect(corner.style.right).toBe('9px');
    const row = screen.queryByTestId('tv-detail-backrow');
    if (row) expect(row.contains(edit)).toBe(false);
  });
  it('at 390 with no banner, there is no art box and Edit stays at the header top right (outside the hero)', async () => {
    phoneAt390();
    detail = { ...base(), banner_url: null };
    render(<TvSeriesDetail seriesId={1} wide={false} onBack={() => {}} say={() => {}} />);
    await screen.findByTestId('tv-detail-hero');
    expect(screen.queryByTestId('tv-detail-header')).toBeNull();
    const edits = screen.getAllByTestId('tv-detail-edit');
    expect(edits).toHaveLength(1);
    expect(screen.getByTestId('tv-detail-hero').contains(edits[0])).toBe(false);
  });
});

describe('r3 (Tester PREP FAIL on e89d15ee): exact ratio and a floor for the controls', () => {
  it('D1: the card box has no border (the hairline is an overlay), so the art box is exactly the natural ratio', () => {
    hero();
    const card = screen.getByTestId('tv-detail-header');
    load(screen.getByTestId('tv-detail-art'), 758, 140);
    expect(card.style.borderWidth === '' || card.style.borderWidth === '0px' || card.style.border === '0px' || card.style.borderStyle === 'none' || card.style.border === '').toBe(true);
    expect(card.style.padding).toBe('0px');
    expect(ratioOf(card)).toBeCloseTo(758 / 140, 5);
    const line = screen.getByTestId('tv-detail-hairline');
    expect(line.style.position).toBe('absolute'); expect(line.style.inset).toBe('0px');
    expect(line.style.pointerEvents).toBe('none');
    expect(screen.getByTestId('tv-detail-art').style.objectFit).toBe('cover');
  });
  it('D2: the card has a min height that fits Edit and the bookmark plus their inset (64px phone, 80px desktop)', () => {
    expect(HERO_MIN_H_PHONE).toBeGreaterThanOrEqual(2 * EDIT_INSET_PHONE + CONTROLS_H);
    expect(HERO_MIN_H).toBe(2 * EDIT_INSET + CONTROLS_H);
    hero({ phone: true });
    let card = screen.getByTestId('tv-detail-header');
    expect(card.style.minHeight).toBe('64px');
    expect(screen.getByTestId('tv-detail-art').style.objectPosition).toBe('center');
    cleanup();
    hero();
    card = screen.getByTestId('tv-detail-header');
    expect(card.style.minHeight).toBe(`${HERO_MIN_H}px`);
  });
});

describe('v325 TYPE-series (Designer): desktop type scale', () => {
  it('desktop: meta 17/500/24, labels 14/500, values 16/600/22, path capped at 360 with the full path as its tooltip', async () => {
    render(<TvSeriesDetail seriesId={1} wide onBack={() => {}} say={() => {}} />);
    const facts = await screen.findByTestId('tv-detail-facts');
    const meta = screen.getByTestId('tv-detail-meta');
    expect([meta.style.fontSize, meta.style.fontWeight, meta.style.lineHeight]).toEqual(['17px', '500', '24px']);
    for (const dt of facts.querySelectorAll('dt')) expect([(dt as HTMLElement).style.fontSize, (dt as HTMLElement).style.fontWeight]).toEqual(['14px', '500']);
    for (const dd of facts.querySelectorAll('dd')) {
      const s = (dd as HTMLElement).style;
      expect([s.fontSize, s.fontWeight, s.lineHeight]).toEqual(['16px', '600', '22px']);
      expect(dd.getAttribute('title')).toBe(dd.textContent);
    }
    const path = screen.getByTestId('tv-detail-fact-path');
    expect(path.style.maxWidth).toBe(`${PATH_MAX_DESK}px`);
    expect((path.querySelector('dd') as HTMLElement).style.textOverflow).toBe('ellipsis');
    expect(BOTTOM_MAX_DESK).toBe(900);
  });
  it('phone: only the facts change (labels 14, values 16)', async () => {
    render(<TvSeriesDetail seriesId={1} wide={false} onBack={() => {}} say={() => {}} />);
    const facts = await screen.findByTestId('tv-detail-facts');
    expect((facts.querySelector('dt') as HTMLElement).style.fontSize).toBe('14px');
    expect((facts.querySelector('dd') as HTMLElement).style.fontSize).toBe('16px');
    expect(screen.getByTestId('tv-detail-meta').style.fontSize).toBe('14px');
  });
});
