/** v358 desktop top nav, Direction B "Violet Glass Bar": one glass bar, five labeled segments that go straight
 * to their page (no menu), a search modal, hidden on every Settings route, desktop only, no logo/avatar overlap. */
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  BAR, DENSITY, DESKTOP_DESTINATIONS, DesktopDock, HEADER, barAvailable, barBox, barDensity, barWidth,
  desktopDestActive, desktopDestinations, dockCss, dockVisible, headerEdges,
} from '@/components/home/DesktopDock';
import { CLOSE_SYMBOL, DOCK_SYMBOLS, symbolPath } from '@/assets/desktop-nav/symbols';
import { MINIMAL_ROUTES, minimalRouteExists } from '@/minimalRoutes';

const src = (f: string) => readFileSync(resolve(__dirname, '../src/' + f), 'utf8');
const symbolsDir = resolve(__dirname, '../src/assets/desktop-nav/symbols');
const names = () => [...screen.getByTestId('desktop-dock').querySelectorAll('button, a')].map(el => el.textContent);

afterEach(() => {
  cleanup();
  delete window.__LUMA_MINIMAL__;
  window.history.pushState({}, '', '/');
});

describe('icons: Material Symbols Rounded 500, bundled locally with the license', () => {
  it('every bundled file matches SHA256SUMS and the Apache-2.0 license file is kept', () => {
    const sums = readFileSync(resolve(symbolsDir, 'SHA256SUMS'), 'utf8').trim().split('\n').map(l => l.split(/\s+/) as [string, string]);
    expect(sums.map(([, f]) => f).sort()).toEqual(readdirSync(symbolsDir).filter(f => f !== 'SHA256SUMS').sort());
    for (const [hex, f] of sums) expect(createHash('sha256').update(readFileSync(resolve(symbolsDir, f))).digest('hex')).toBe(hex);
    const license = readFileSync(resolve(symbolsDir, 'LICENSE'), 'utf8');
    expect(license).toContain('Apache License');
    expect(license).toContain('Version 2.0, January 2004');
  });

  it('search, video_library, explore, calendar_month and bookmark: the outlined path, and the FILL 1 path of the same symbol', () => {
    const file = (n: string) => symbolPath(readFileSync(resolve(symbolsDir, `${n}.svg`), 'utf8'));
    expect(Object.entries(DOCK_SYMBOLS).map(([id, s]) => [id, s.name])).toEqual([
      ['search', 'search'], ['libraries', 'video_library'], ['explore', 'explore'], ['calendar', 'calendar_month'], ['watchlist', 'bookmark'],
    ]);
    for (const s of Object.values(DOCK_SYMBOLS)) {
      expect(s.outline).toBe(file(s.name));
      expect(s.fill).toBe(s.name === 'search' ? file('search') : file(`${s.name}-fill`));
      if (s.name !== 'search') expect(s.fill).not.toBe(s.outline);
    }
    expect(CLOSE_SYMBOL).toBe(file('close'));
  });

  it('nothing is fetched from a CDN or a font host at runtime, and the raster tiles are gone', () => {
    const all = [dockCss, src('components/home/DesktopDock.tsx'), src('assets/desktop-nav/symbols.ts'), readFileSync(resolve(__dirname, '../index.html'), 'utf8')].join('\n');
    expect(all).not.toMatch(/fonts\.googleapis|fonts\.gstatic|cdn\.jsdelivr|unpkg\.com|@import|url\(/);
    expect(readdirSync(resolve(__dirname, '../src/assets/desktop-nav')).sort()).toEqual(['SOURCES.md', 'symbols', 'symbols.ts']);
    expect(src('components/home/DesktopDock.tsx')).not.toMatch(/\.png|<img|DOCK_ICONS/);
  });
});

describe('Direction B tokens', () => {
  it('56px bar, radius 28, centered on y=52; 46px segments; 22px icons; 600-weight 15px labels in the app font', () => {
    expect(BAR).toMatchObject({ h: 56, r: 28, cy: 52, top: 24, pad: 5, seg: 46, segR: 23, icon: 22, labelSize: 15, labelWeight: 600 });
    expect(BAR.top + BAR.h / 2).toBe(BAR.cy);
    expect(BAR.pad * 2 + BAR.seg).toBe(BAR.h);
    expect(dockCss).toContain('.vgb{position:fixed;z-index:56;top:24px;left:50%;transform:translateX(-50%);height:56px;');
    expect(dockCss).toContain('border-radius:28px');
    expect(dockCss).toContain('font-size:15px;font-weight:600');
    expect(dockCss).toContain('letter-spacing:.1px');
    expect(dockCss).toContain('color:rgba(255,255,255,.92)');
    expect(dockCss).toContain('padding:0 var(--vgb-pad-r) 0 var(--vgb-pad-l)');
    expect(DENSITY.full).toEqual({ padL: 14, padR: 18, gap: 2, iconGap: 8 });
    expect(dockCss).toContain('font-family:-apple-system, BlinkMacSystemFont, "SF Pro Text"');
    expect(dockCss).not.toMatch(/Inter|Material Symbols/);
    expect(dockCss).toContain('.vgb-ico{position:relative;flex:none;width:22px;height:22px;');
  });

  it('glass: blur 24 saturate 1.6 over navy .56 with a violet sheen and a 1px lavender edge; nothing baked in', () => {
    expect(dockCss).toContain('backdrop-filter:blur(24px) saturate(1.6)');
    expect(dockCss).toContain('-webkit-backdrop-filter:blur(24px) saturate(1.6)');
    expect(BAR.fill).toBe('rgba(21,16,42,.56)');
    expect(dockCss).toContain(`background:${BAR.sheen},rgba(21,16,42,.56)`);
    expect(BAR.sheen).toBe('linear-gradient(180deg,rgba(150,132,255,.16),rgba(150,132,255,0) 70%)');
    expect(BAR.edge).toBe('rgba(196,186,255,.30)');
    expect(dockCss).toContain('box-shadow:0 8px 24px rgba(8,4,24,.36),inset 0 1px 0 rgba(255,255,255,.26),inset 0 0 0 1px rgba(196,186,255,.30);');
    expect(dockCss).not.toContain('url(');
  });

  it('current pill violet .80 to blue .76; hover white .10 over navy .26; pressed a darker inset', () => {
    expect(dockCss).toContain('background:linear-gradient(180deg,rgba(255,255,255,.14),rgba(255,255,255,0) 70%),linear-gradient(135deg,rgba(104,64,232,.80),rgba(37,99,235,.76));');
    expect(dockCss).toContain('box-shadow:0 4px 12px rgba(60,40,190,.40),inset 0 1px 0 rgba(255,255,255,.45),inset 0 0 0 1px rgba(214,206,255,.55);');
    expect(dockCss).toContain('background:linear-gradient(rgba(255,255,255,.10),rgba(255,255,255,.10)),rgba(21,16,42,.26)');
    expect(dockCss).toContain('.vgb-item:hover::before{opacity:1}');
    expect(dockCss).toContain('box-shadow:inset 0 0 0 1px rgba(255,255,255,.24);');
    expect(dockCss).toMatch(/\.vgb-item:active::before[^{]*\{opacity:1;background:rgba\(10,6,28,\.34\);\s*box-shadow:inset 0 2px 5px rgba\(0,0,0,\.30\)\}/);
  });

  it('the pill slides (transform and width); reduced motion turns every transition off', () => {
    expect(dockCss).toMatch(/\.vgb-pill\{[^}]*transition:transform var\(--m-dur-standard, 240ms\)[^}]*width var\(--m-dur-standard, 240ms\)/);
    expect(dockCss).toContain('@media (prefers-reduced-motion: reduce){.vgb-pill,.vgb-pill[data-instant="true"],.vgb-item,.vgb-item::before{transition:none}}');
    expect(dockCss).not.toMatch(/animation:|bounce|scale\(/);
  });

  it('a visible keyboard focus ring in the bar\'s violet/blue, outside the segment and not clipped', () => {
    expect(dockCss).toContain('.vgb-item:focus-visible{outline:2px solid #ede9fe;outline-offset:2px;box-shadow:0 0 0 4px rgba(21,16,42,.72),0 0 0 7px rgba(99,102,241,.78)}');
    expect(dockCss).toContain('overflow:visible');
    expect(dockCss).not.toMatch(/\.vgb-item:focus\{/);
  });
});

describe('narrow desktop: measured fit, never over the logo or the avatar', () => {
  // Label widths (px) at 15px/600: Chrome on Linux as measured, and two wider sets for other system fonts.
  const MEASURED = [51, 66, 56, 68, 71];
  const WIDE = MEASURED.map(w => Math.ceil(w * 1.12));
  const HUGE = MEASURED.map(w => Math.ceil(w * 1.6));

  it('bar width is the sum of what the CSS draws at each density', () => {
    expect(barWidth('full', MEASURED)).toBe(2 * 5 + 4 * 2 + 5 * (14 + 18 + 22 + 8) + 312);
    expect(barWidth('tight', MEASURED)).toBe(2 * 5 + 4 * 2 + 5 * (2 * 8 + 22 + 6) + 312);
    expect(barWidth('icons', MEASURED)).toBe(2 * 5 + 4 * 4 + 5 * 46);
    expect(barWidth('full', MEASURED)).toBeGreaterThan(630);
    expect(barWidth('full', MEASURED)).toBeLessThan(650);          // about 640px
    expect(barWidth('icons', [51, 66, 68])).toBe(2 * 5 + 2 * 4 + 3 * 46);
    for (const d of ['full', 'tight', 'icons'] as const) {
      expect(dockCss).toContain(`--vgb-pad-l:${DENSITY[d].padL}px;--vgb-pad-r:${DENSITY[d].padR}px;--vgb-gap:${DENSITY[d].gap}px;--vgb-icon-gap:${DENSITY[d].iconGap}px`);
    }
    expect(DENSITY.icons.padL + DENSITY.icons.padR + BAR.icon).toBe(BAR.seg);
  });

  it('the space between the logo and the avatar, less the clearance on both sides of the center', () => {
    const logoRight = HEADER.padLeft + HEADER.logoW;               // 44 + 916 * 64 / 416
    expect(logoRight).toBeCloseTo(184.92, 1);
    expect(barAvailable(1440, logoRight, 1440 - 108)).toBeCloseTo(1440 - 2 * (logoRight + BAR.clearance), 5);
    expect(barAvailable(1440, 100, 1140)).toBe(2 * (1140 - 720 - BAR.clearance));   // the nearer side decides
    expect(barAvailable(300, 185, 192)).toBe(0);
  });

  it('labels when they fit, then tighter spacing, then icons only', () => {
    expect(barDensity(barWidth('full', MEASURED), MEASURED)).toBe('full');
    expect(barDensity(barWidth('full', MEASURED) - 1, MEASURED)).toBe('tight');
    expect(barDensity(barWidth('tight', MEASURED) - 1, MEASURED)).toBe('icons');
    expect(barDensity(0, MEASURED)).toBe('icons');
  });

  it.each([1024, 1180, 1280, 1440, 1920])('at %ipx the centered bar clears the logo and the avatar for every font width', (vw) => {
    for (const labels of [MEASURED, WIDE, HUGE]) {
      const { logoRight, avatarLeft } = headerEdges(vw);
      const density = barDensity(barAvailable(vw, logoRight, avatarLeft), labels);
      const box = barBox(vw, barWidth(density, labels));
      expect(box.left - logoRight).toBeGreaterThanOrEqual(BAR.clearance);
      expect(avatarLeft - box.right).toBeGreaterThanOrEqual(BAR.clearance);
      expect((box.left + box.right) / 2).toBe(vw / 2);
      expect([box.top, box.bottom, box.cy]).toEqual([24, 80, 52]);
      expect(box.bottom).toBeLessThanOrEqual(20 + 64);             // inside the logo row
    }
  });

  it('1440 and wider keep the labels at full spacing; a wider logo or avatar on screen narrows the bar', () => {
    for (const vw of [1280, 1440, 1920]) {
      const e = headerEdges(vw);
      expect(barDensity(barAvailable(vw, e.logoRight, e.avatarLeft), MEASURED)).toBe('full');
    }
    const rect = (left: number, right: number) => ({ left, right, width: right - left });
    expect(headerEdges(1440, [])).toEqual({ logoRight: HEADER.padLeft + HEADER.logoW, avatarLeft: 1332 });
    expect(headerEdges(1440, [rect(44, 400), rect(1200, 1380)])).toEqual({ logoRight: 400, avatarLeft: 1200 });
    expect(headerEdges(1440, [rect(44, 44)])).toEqual(headerEdges(1440));       // an image that has not loaded yet
    const wideLogo = headerEdges(1024, [rect(44, 330)]);
    expect(barDensity(barAvailable(1024, wideLogo.logoRight, wideLogo.avatarLeft), MEASURED)).toBe('icons');
  });

  it('icons only keeps every accessible name (the label is clipped, not removed)', () => {
    expect(dockCss).toMatch(/\.vgb\[data-density="icons"\] \.vgb-label\{position:absolute;width:1px;height:1px;[^}]*clip:rect\(0 0 0 0\)/);
    expect(dockCss).not.toMatch(/\.vgb-label\{[^}]*display:none/);
    render(<DesktopDock pathname="/" />);
    const dock = screen.getByTestId('desktop-dock');
    act(() => { dock.setAttribute('data-density', 'icons'); });
    expect(screen.getByRole('button', { name: 'Search' })).toBeTruthy();
    for (const name of ['Libraries', 'Explore', 'Calendar', 'Watchlist']) expect(screen.getByRole('link', { name })).toBeTruthy();
  });
});

describe('controls', () => {
  it('five controls in order with a visible label and a 22px symbol each; Search is a button, the rest are links', () => {
    render(<DesktopDock pathname="/" />);
    const dock = screen.getByTestId('desktop-dock');
    expect(dock.tagName).toBe('NAV');
    expect(dock.getAttribute('aria-label')).toBe('Main');
    const controls = [...dock.querySelectorAll('button, a')];
    expect(controls.map(el => el.textContent)).toEqual(['Search', 'Libraries', 'Explore', 'Calendar', 'Watchlist']);
    expect(controls.map(el => el.tagName)).toEqual(['BUTTON', 'A', 'A', 'A', 'A']);
    expect(controls.map(el => (el as HTMLElement).tabIndex)).toEqual([0, 0, 0, 0, 0]);
    expect((controls[0] as HTMLButtonElement).type).toBe('button');
    expect(controls.slice(1).map(el => el.getAttribute('href'))).toEqual(['/browse', '/explore', '/settings/tv', '/watchlist']);
    expect(controls.map(el => el.querySelector('svg')!.getAttribute('data-symbol'))).toEqual(['search', 'video_library', 'explore', 'calendar_month', 'bookmark']);
    for (const el of controls) {
      const svg = el.querySelector('svg')!;
      expect([svg.getAttribute('width'), svg.getAttribute('height'), svg.getAttribute('aria-hidden'), svg.getAttribute('data-fill')]).toEqual(['22', '22', 'true', '0']);
      expect(el.querySelector('.vgb-label')!.textContent).toBe(el.textContent);
      expect(el.hasAttribute('title')).toBe(false);
    }
    expect(dock.querySelector('img')).toBeNull();
    expect(dock.textContent).toBe('SearchLibrariesExploreCalendarWatchlist');   // labels only, no helper text
  });

  it('no dropdown, menu or popover: no control expands anything, and a click opens none', () => {
    const seen: Event[] = [];
    const on = (e: Event) => { seen.push(e); };
    window.addEventListener('open-search', on);
    render(<DesktopDock pathname="/" />);
    const dock = screen.getByTestId('desktop-dock');
    for (const el of dock.querySelectorAll('button, a')) {
      expect(el.hasAttribute('aria-expanded')).toBe(false);
      expect(el.hasAttribute('aria-controls')).toBe(false);
      expect(el.getAttribute('aria-haspopup')).toBe(el.tagName === 'BUTTON' ? 'dialog' : null);
      expect(el.hasAttribute('popovertarget')).toBe(false);
    }
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(seen).toHaveLength(1);                                   // Search only asks for the search modal
    for (const name of ['Libraries', 'Explore', 'Watchlist']) {
      fireEvent.click(screen.getByRole('link', { name }));
      expect(document.querySelector('[role="menu"], [role="listbox"], [role="menuitem"], [popover], details, .a1-bar, .a1-menu')).toBeNull();
    }
    expect(window.location.pathname).toBe('/watchlist');
    window.removeEventListener('open-search', on);
    const code = src('components/home/DesktopDock.tsx');
    expect(code).not.toMatch(/aria-expanded|role="menu"|search-palette|setOpen\(/);
  });

  it('each link goes straight to its page; a modified click is left to the browser', () => {
    const before = vi.fn();
    render(<DesktopDock pathname="/" onBeforeNavigate={before} />);
    const pops: string[] = [];
    const onPop = () => { pops.push(window.location.pathname + window.location.search); };
    window.addEventListener('popstate', onPop);
    for (const name of ['Libraries', 'Explore', 'Watchlist']) fireEvent.click(screen.getByRole('link', { name }));
    expect(pops).toEqual(['/browse', '/explore', '/watchlist']);
    expect(before).toHaveBeenCalledTimes(3);
    fireEvent.click(screen.getByRole('link', { name: 'Libraries' }), { metaKey: true });
    fireEvent.click(screen.getByRole('link', { name: 'Libraries' }), { button: 1 });
    expect(pops).toHaveLength(3);
    window.removeEventListener('popstate', onPop);
  });

  it('Search dispatches open-search unless a caller handles it', () => {
    const seen: Event[] = [];
    const on = (e: Event) => { seen.push(e); };
    window.addEventListener('open-search', on);
    const { unmount } = render(<DesktopDock pathname="/" />);
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(seen).toHaveLength(1);
    unmount();
    const custom = vi.fn();
    render(<DesktopDock pathname="/" onSearch={custom} />);
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(custom).toHaveBeenCalledTimes(1);
    expect(seen).toHaveLength(1);
    window.removeEventListener('open-search', on);
  });

  it('keyboard: Tab order is the visual order; Enter activates a link and Space does not; both activate Search', () => {
    // jsdom does not synthesize the click a browser sends for Enter/Space; this is the HTML behavior for native controls.
    const activate = (el: Element, key: 'Enter' | ' ') => {
      fireEvent.keyDown(el, { key });
      if (el.tagName === 'BUTTON' || (el.tagName === 'A' && key === 'Enter')) fireEvent.click(el);
      fireEvent.keyUp(el, { key });
    };
    const opens: Event[] = [];
    const on = (e: Event) => { opens.push(e); };
    window.addEventListener('open-search', on);
    render(<DesktopDock />);
    const controls = [...screen.getByTestId('desktop-dock').querySelectorAll<HTMLElement>('button, a')];
    for (const el of controls) {
      expect(el.tabIndex).toBe(0);
      expect(el.hasAttribute('disabled')).toBe(false);
      expect(el.getAttribute('aria-disabled')).toBeNull();
      el.focus();
      expect(document.activeElement).toBe(el);
    }
    const explore = screen.getByRole('link', { name: 'Explore' });
    activate(explore, ' ');
    expect(window.location.pathname).toBe('/');
    activate(explore, 'Enter');
    expect(window.location.pathname).toBe('/explore');
    const search = screen.getByRole('button', { name: 'Search' });
    activate(search, ' ');
    activate(search, 'Enter');
    expect(opens).toHaveLength(2);
    window.removeEventListener('open-search', on);
  });
});

describe('current page', () => {
  const current = () => [...screen.getByTestId('desktop-dock').querySelectorAll('[aria-current]')].map(el => el.textContent);
  it('Home selects nothing and the pill is hidden', () => {
    render(<DesktopDock pathname="/" />);
    expect(current()).toEqual([]);
    expect(screen.getByTestId('desktop-dock').querySelector('[data-current]')).toBeNull();
    expect(screen.getByTestId('dock-pill').dataset.visible).toBe('false');
    expect(screen.getByTestId('dock-pill').getAttribute('aria-hidden')).toBe('true');
    expect(screen.getByRole('button', { name: 'Search' }).hasAttribute('aria-current')).toBe(false);
  });

  it('only the exact destination path is current: aria-current="page", the filled symbol and the pill', () => {
    expect(desktopDestActive('/', '/browse')).toBe(false);
    expect(desktopDestActive('', '/browse')).toBe(false);
    expect(desktopDestActive('/browse', '/browse')).toBe(true);
    expect(desktopDestActive('/browse/x', '/browse')).toBe(false);
    for (const d of DESKTOP_DESTINATIONS.filter(x => x.id !== 'calendar')) {
      render(<DesktopDock pathname={d.href} />);
      expect(current()).toEqual([d.name]);
      const link = screen.getByRole('link', { name: d.name });
      expect(link.getAttribute('aria-current')).toBe('page');
      expect(link.querySelector('svg')!.getAttribute('data-fill')).toBe('1');
      expect(link.querySelector('path')!.getAttribute('d')).toBe(DOCK_SYMBOLS[d.id].fill);
      expect(screen.getByTestId('dock-pill').dataset.visible).toBe('true');
      for (const other of DESKTOP_DESTINATIONS.filter(x => x.id !== d.id)) {
        const o = screen.getByRole('link', { name: other.name });
        expect(o.hasAttribute('aria-current')).toBe(false);
        expect(o.querySelector('svg')!.getAttribute('data-fill')).toBe('0');
        expect(o.querySelector('path')!.getAttribute('d')).toBe(DOCK_SYMBOLS[other.id].outline);
      }
      cleanup();
    }
  });

  it('follows the window path after popstate, and the pill slides from one item to the next', () => {
    render(<DesktopDock />);
    expect(current()).toEqual([]);
    act(() => { window.history.pushState({}, '', '/explore'); window.dispatchEvent(new PopStateEvent('popstate')); });
    expect(current()).toEqual(['Explore']);
    expect(screen.getByTestId('dock-pill').dataset).toMatchObject({ visible: 'true', instant: 'true' });   // first appearance: placed, not slid
    act(() => { window.history.pushState({}, '', '/watchlist'); window.dispatchEvent(new PopStateEvent('popstate')); });
    expect(current()).toEqual(['Watchlist']);
    expect(screen.getByTestId('dock-pill').dataset).toMatchObject({ visible: 'true', instant: 'false' });  // slides
    act(() => { window.history.pushState({}, '', '/'); window.dispatchEvent(new PopStateEvent('popstate')); });
    expect(current()).toEqual([]);
    expect(screen.getByTestId('dock-pill').dataset.visible).toBe('false');
  });

  it('while the search modal is open Search carries the pill; aria-current stays with the page', () => {
    const { rerender } = render(<DesktopDock pathname="/browse" searchOpen />);
    expect(screen.getByRole('button', { name: 'Search' }).dataset.current).toBe('true');
    expect(screen.getByRole('button', { name: 'Search' }).hasAttribute('aria-current')).toBe(false);
    expect(current()).toEqual(['Libraries']);
    expect(screen.getByRole('link', { name: 'Libraries' }).dataset.current).toBeUndefined();
    rerender(<DesktopDock pathname="/browse" />);
    expect(screen.getByRole('button', { name: 'Search' }).dataset.current).toBeUndefined();
    expect(screen.getByRole('link', { name: 'Libraries' }).dataset.current).toBe('true');
    expect(screen.getByTestId('dock-pill').dataset.instant).toBe('false');
  });
});

describe('not on Settings', () => {
  const SETTINGS = ['/settings', '/settings/', '/settings/home-screen', '/settings/rooms', '/settings/connectors', '/settings/notifications',
    '/settings/trackers', '/settings/library-paths', '/settings/movies', '/settings/tv', '/settings/tv/', '/settings/tv/settings',
    '/settings/automation', '/settings/libraries', '/settings/plex'];
  const SHOWN = ['/', '', '/browse', '/explore', '/watchlist', '/settingsx', '/my-settings'];

  it('dockVisible: false for every Settings route, /settings/tv (Calendar) included; true everywhere else', () => {
    for (const p of SETTINGS) expect([p, dockVisible(p)]).toEqual([p, false]);
    for (const p of SHOWN) expect([p, dockVisible(p)]).toEqual([p, true]);
  });

  it.each(SETTINGS)('renders nothing on %s', (path) => {
    const { container } = render(<DesktopDock pathname={path} />);
    expect(screen.queryByTestId('desktop-dock')).toBeNull();
    expect(container.querySelector('nav, .vgb, .vgb-ruler, style')).toBeNull();
  });

  it.each(SHOWN)('renders on %s', (path) => {
    render(<DesktopDock pathname={path} />);
    expect(screen.getByTestId('desktop-dock')).toBeTruthy();
  });

  it('Calendar still goes to /settings/tv; the bar is gone once there and back again on Home', () => {
    render(<DesktopDock />);
    const calendar = screen.getByRole('link', { name: 'Calendar' });
    expect(calendar.getAttribute('href')).toBe('/settings/tv');
    fireEvent.click(calendar);
    expect(window.location.pathname).toBe('/settings/tv');
    expect(screen.queryByTestId('desktop-dock')).toBeNull();
    act(() => { window.history.pushState({}, '', '/'); window.dispatchEvent(new PopStateEvent('popstate')); });
    expect(screen.getByTestId('desktop-dock')).toBeTruthy();
  });

  it('the Settings page does not mount the bar at all (desktop or phone)', () => {
    const settings = src('pages/LibrariesPage.tsx');
    expect(settings).not.toMatch(/DesktopDock|HomeSearchR3|data-desktop-dock/);
  });
});

describe('desktop only: phone navigation is untouched', () => {
  it('the bar is mounted only by the >= 1024px Home, and its CSS turns it off below 1024px', () => {
    const home = src('pages/MinimalHomePage.tsx');
    const kiosk = home.slice(home.indexOf('if (kiosk) {'), home.indexOf('// Startup and a full document refresh'));
    expect(kiosk).toContain('<HomeSearchR3 />');
    const phone = home.slice(home.indexOf('// Startup and a full document refresh'));
    expect(phone).toContain('data-testid="phone-header"');
    expect(phone).toContain('data-testid="search-button"');
    expect(phone).not.toMatch(/DesktopDock|HomeSearchR3/);
    expect(src('components/search/HomeSearchR3.tsx')).toMatch(/<DesktopDock /);
    expect(dockCss).toContain('@media (max-width: 1023.98px){.vgb,.vgb-ruler{display:none}}');
    for (const f of ['components/home/HomePills.tsx', 'components/search/SearchShelf.tsx', 'components/search/MinimalSearchHost.tsx', 'components/mobile/MobilePushBanner.tsx']) {
      expect(src(f)).not.toMatch(/DesktopDock|vgb-/);
    }
  });
});

describe('LUMA_MINIMAL: only destinations with a real page, never a dead button', () => {
  it('the minimal route table has Home and Settings pages and none for /browse, /explore or /watchlist', () => {
    expect(Object.values(MINIMAL_ROUTES)).toEqual(['/', '/login', '/start', '/unlock', '/settings', '/settings/libraries', '/settings/:section', '/settings/:section/*']);
    for (const href of ['/', '/settings', '/settings/tv', '/settings/tv?view=series', '/settings/tv/settings', '/settings/movies#x']) expect([href, minimalRouteExists(href)]).toEqual([href, true]);
    for (const href of ['/browse', '/explore', '/watchlist', '/library', '/settingsx', '/browse?x=/settings']) expect([href, minimalRouteExists(href)]).toEqual([href, false]);
    // MinimalApp's routes are that table and the catch-all home redirect, nothing else.
    const app = src('App.tsx');
    const minimal = app.slice(app.indexOf('function MinimalApp'), app.indexOf('export default function App'));
    const paths = [...minimal.matchAll(/<Route path=(\{[^}]+\}|"[^"]+")/g)].map(m => m[1]);
    expect(paths).toEqual([...Object.keys(MINIMAL_ROUTES).map(k => `{MINIMAL_ROUTES.${k}}`), '"*"']);
  });

  it('desktopDestinations: every one in the full app; on minimal Libraries (the TV Series library) and Calendar', () => {
    expect(desktopDestinations(false).map(d => [d.name, d.href])).toEqual([
      ['Libraries', '/browse'], ['Explore', '/explore'], ['Calendar', '/settings/tv'], ['Watchlist', '/watchlist']]);
    expect(desktopDestinations(true).map(d => [d.name, d.href])).toEqual([
      ['Libraries', '/settings/tv?view=series'], ['Calendar', '/settings/tv']]);
    for (const d of desktopDestinations(true)) expect(minimalRouteExists(d.href)).toBe(true);
    window.__LUMA_MINIMAL__ = true;
    expect(desktopDestinations().map(d => d.id)).toEqual(['libraries', 'calendar']);
  });

  it('the bar draws Search, Libraries and Calendar; Explore and Watchlist are not drawn (no page), not disabled', () => {
    window.__LUMA_MINIMAL__ = true;
    render(<DesktopDock />);
    expect(names()).toEqual(['Search', 'Libraries', 'Calendar']);
    expect(screen.queryByRole('link', { name: 'Explore' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Watchlist' })).toBeNull();
    expect(screen.getByTestId('desktop-dock').querySelector('[disabled], [aria-disabled]')).toBeNull();
    expect([...document.querySelectorAll('.vgb-ruler span')].map(s => s.textContent)).toEqual(['Search', 'Libraries', 'Calendar']);
  });

  it.each([['Libraries', '/settings/tv', '?view=series'], ['Calendar', '/settings/tv', '']])('%s navigates (no silent no-op) to %s%s', (name, path, search) => {
    window.__LUMA_MINIMAL__ = true;
    const before = vi.fn();
    render(<DesktopDock onBeforeNavigate={before} />);
    fireEvent.click(screen.getByRole('link', { name }));
    expect([window.location.pathname, window.location.search]).toEqual([path, search]);
    expect(before).toHaveBeenCalledTimes(1);
  });

  it('no navigation is swallowed any more: the old minimal no-op guard is gone', () => {
    expect(src('components/home/DesktopDock.tsx')).not.toMatch(/desktopDestNavigates|startsWith\('\/settings'\)\) return/);
  });
});
