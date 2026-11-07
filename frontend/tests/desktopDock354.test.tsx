/** Approved desktop dock: original rasters, D geometry at the top, real routes, keyboard and active state. */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  DOCK, DESKTOP_DESTINATIONS, DesktopDock, DesktopDockRouted, dockBox, dockCss, dockIconCenters,
} from '@/components/home/DesktopDock';

const iconDir = resolve(__dirname, '../src/assets/desktop-nav/icons');
const sha = (name: string) => createHash('sha256').update(readFileSync(resolve(iconDir, name))).digest('hex');

/** Hashes from the verified approved-nav-assets.zip SHA256SUMS (zip 4178ef04…, 1871061 bytes). */
const HASHES: Record<string, string> = {
  'luma-dock-search-1024.png': '4ec5e8962ec61cd633838ae0e628647bbb36dba5bbc864d8591b3d25e4936dd8',
  'luma-dock-search-224.png': '2ed684db25fe8877b1e3466e600feaaf8017b3d6384587125254c15d10c16b87',
  'luma-dock-search-144.png': 'f647cde5a25521feb3cb19ea810154fa69f705274726902f83d274dfa5054016',
  'luma-dock-search-56.png': 'a1d0332db03c452804713caf1c55a48ec802d6da55f9ea350338470cf29dcf7f',
  'luma-dock-libraries-1024.png': '0228cd19ce8ffa3deca991e2936290450ca54bb308127629a983f49df5dae706',
  'luma-dock-libraries-224.png': 'd9b861a17346a3d4ab7e4708838f96e8c810f6c04efae97b7a5020abcc1c2652',
  'luma-dock-libraries-144.png': '9169f543684f003af488f19f12e0b6ed57facd63bc10766c0518d901cc68b8fa',
  'luma-dock-libraries-56.png': '9c4b04f8b0dd580e95530916b1a2d9df0341988e207f5e50a1d7f71e1f9517c2',
  'luma-dock-explore-1024.png': 'a6cfba31e8127971375a167a5cb890474137e424c8e9075e1cf23aebab712ada',
  'luma-dock-explore-224.png': '71173e67725119a29c0d83c50e6777fc419f01649e88e1f3c83069d8340deee6',
  'luma-dock-explore-144.png': 'f68f278fa3a27860d827ba25cc34bc34d391a08503f088fce3aea16fc8cf00b3',
  'luma-dock-explore-56.png': 'f7eff08604558c5f1e6a4f6a84a8da3daae87d939ff48c51f801dc5f42922eb5',
  'luma-dock-calendar-1024.png': 'b8f471eed4dd4bc689b24ec1fe61719940a9024dca8158b4584e43e255b995cd',
  'luma-dock-calendar-224.png': '38946c382488e0154902c2be823d156619cb06d09ff27bf33b047cb9677c92d2',
  'luma-dock-calendar-144.png': 'd46e4c526d2a335527adb943618a2568c57bea2328ea13025c21bdfdf5f4c5bb',
  'luma-dock-calendar-56.png': '618df4bdd881a0c23244877fcc5bfbb0875d910a948ce9cc746c35a6210ec576',
  'luma-dock-watchlist-1024.png': '0e629bfa6925735272cc250ad5bcafde4010897b3605a22a92e50b5a8b7572ea',
  'luma-dock-watchlist-224.png': '31fca83bec8d185c2c1902adae8dfaabf7663e7bdd3534cad5c4ec6604091b33',
  'luma-dock-watchlist-144.png': '22768f8e8008447d1b384893eec3a3067e8105e10c2301189de54d09edf7a436',
  'luma-dock-watchlist-56.png': '241a9e67dec17e38702e814dec2651db9e98db8bab2a8dbcdef162dacb025cda',
};

afterEach(() => {
  cleanup();
  delete window.__LUMA_MINIMAL__;
  window.history.pushState({}, '', '/');
});

describe('original icon bytes', () => {
  it('matches the handoff hashes, keeps alpha masters, and does not ship the 48px size', () => {
    for (const [name, hex] of Object.entries(HASHES)) expect(sha(name)).toBe(hex);
    for (const id of ['search', 'libraries', 'explore', 'calendar', 'watchlist']) {
      const buf = readFileSync(resolve(iconDir, `luma-dock-${id}-1024.png`));
      expect(buf.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a');
      expect(buf.readUInt32BE(16)).toBe(1024);
      expect(buf.readUInt32BE(20)).toBe(1024);
      expect(buf[25]).toBe(6); // RGBA
      expect(() => readFileSync(resolve(iconDir, `luma-dock-${id}-48.png`))).toThrow();
    }
  });
});

describe('D geometry, top center, header fit', () => {
  it('at 1440 the box and icon centers are the approved native D numbers', () => {
    expect(dockBox(1440)).toEqual({ left: 542, top: 10, right: 898, bottom: 94, cx: 720, cy: 52 });
    expect(dockIconCenters(1440)).toEqual([584, 652, 720, 788, 856]);
    expect(DOCK).toMatchObject({ w: 356, h: 84, r: 26, icon: 56, gap: 12, pad: 14, top: 10 });
  });

  it('fits the real header at desktop widths without covering the logo or the avatar', () => {
    // luma_wordmark.webp is 916×416, drawn at 64px. Home/settings pad 44 left, 60 right, avatar 48.
    const logoW = 916 * (64 / 416);
    for (const vw of [1024, 1100, 1280, 1440, 1920]) {
      const box = dockBox(vw);
      expect(box.left).toBeGreaterThan(44 + logoW);
      expect(box.right).toBeLessThan(vw - 60 - 48);
      expect(box.right - box.left).toBe(356);
      expect(box.left).toBeGreaterThanOrEqual(0);
      expect(box.right).toBeLessThanOrEqual(vw);
    }
    // Home header is 20+64+20. Settings logo row is 20+64; the 16px heading gap still clears the dock.
    expect(DOCK.top + DOCK.h).toBeLessThanOrEqual(20 + 64 + 20);
    expect(DOCK.top + DOCK.h).toBeLessThanOrEqual(20 + 64 + 16);
  });

  it('the bar is viewport-centered, not a flex leftover, and does not scale on hover', () => {
    render(<DesktopDock />);
    const css = document.querySelector('style')!.textContent!;
    expect(css).toContain('position:fixed');
    expect(css).toContain('left:50%');
    expect(css).toContain('transform:translateX(-50%)');
    expect(css).toContain('top:10px');
    expect(css).toContain('width:356px');
    expect(css).toContain('overflow:visible');
    expect(css).toContain('outline:2px solid #fff');
    expect(css).toContain('outline-offset:3px');
    expect(css).toContain('min-width:44px');
    expect(css).toContain('min-height:44px');
    expect(css).toContain('backdrop-filter:blur(18px)');
    expect(css).toContain('@media (prefers-reduced-motion: reduce){.ddock-ico{transition:none}}');
    expect(dockCss).not.toMatch(/scale\(|translateY|animation:|bounce/);
    expect(css).not.toContain('url('); // no baked screenshot
  });
});

describe('controls', () => {
  it('five named controls in order, search is a button, destinations are links, no visible text', () => {
    render(<DesktopDock pathname="/" />);
    const dock = screen.getByTestId('desktop-dock');
    const controls = [...dock.querySelectorAll('button, a')];
    expect(controls.map(el => el.getAttribute('aria-label'))).toEqual(['Search', 'Libraries', 'Explore', 'Calendar', 'Watchlist']);
    expect(controls.map(el => el.tagName)).toEqual(['BUTTON', 'A', 'A', 'A', 'A']);
    expect(controls.map(el => el.tabIndex)).toEqual([0, 0, 0, 0, 0]);
    expect((controls[0] as HTMLButtonElement).type).toBe('button');
    expect(controls.slice(1).map(el => el.getAttribute('href'))).toEqual(DESKTOP_DESTINATIONS.map(d => d.href));
    expect(dock.textContent).toBe('');
    expect(dock.querySelector('svg')).toBeNull();
    const imgs = [...dock.querySelectorAll('img')];
    expect(imgs.map(img => img.getAttribute('alt'))).toEqual(['', '', '', '', '']);
    expect(imgs.map(img => img.getAttribute('width'))).toEqual(['56', '56', '56', '56', '56']);
    for (const [img, id] of imgs.map((img, i) => [img, ['search', 'libraries', 'explore', 'calendar', 'watchlist'][i]!] as const)) {
      expect(img.getAttribute('src')).toContain(`luma-dock-${id}`);
      expect(img.getAttribute('srcset')).toContain('56w');
      expect(img.getAttribute('srcset')).toContain('144w');
      expect(img.getAttribute('srcset')).toContain('224w');
    }
  });

  it('Home selects nothing; only the exact destination path is current, including not Settings > TV', () => {
    const { unmount } = render(<MemoryRouter initialEntries={['/']}><DesktopDockRouted /></MemoryRouter>);
    for (const name of ['Libraries', 'Explore', 'Calendar', 'Watchlist']) {
      expect(screen.getByRole('link', { name }).hasAttribute('aria-current')).toBe(false);
    }
    expect(screen.getByRole('button', { name: 'Search' }).hasAttribute('aria-current')).toBe(false);
    unmount();
    render(<MemoryRouter initialEntries={['/settings/tv/settings']}><DesktopDockRouted /></MemoryRouter>);
    expect(screen.getByRole('link', { name: 'Calendar' }).hasAttribute('aria-current')).toBe(false);
    cleanup();
    render(<MemoryRouter initialEntries={['/browse']}><DesktopDockRouted /></MemoryRouter>);
    expect(screen.getByRole('link', { name: 'Libraries' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('link', { name: 'Explore' }).hasAttribute('aria-current')).toBe(false);
  });

  it('follows the window path after popstate', () => {
    render(<DesktopDock />);
    expect(screen.getByRole('link', { name: 'Explore' }).hasAttribute('aria-current')).toBe(false);
    act(() => { window.history.pushState({}, '', '/explore'); window.dispatchEvent(new PopStateEvent('popstate')); });
    expect(screen.getByRole('link', { name: 'Explore' }).getAttribute('aria-current')).toBe('page');
  });

  it('Search dispatches open-search unless a caller handles it', () => {
    const seen: Event[] = [];
    const on = (e: Event) => { seen.push(e); };
    window.addEventListener('open-search', on);
    const { unmount } = render(<DesktopDock />);
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(seen).toHaveLength(1);
    unmount();
    const custom = vi.fn();
    render(<DesktopDock onSearch={custom} searchExpanded />);
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(custom).toHaveBeenCalledTimes(1);
    expect(seen).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Search' }).getAttribute('aria-expanded')).toBe('true');
    window.removeEventListener('open-search', on);
  });

  it('Enter activates a link and Space does not; both activate Search (HTML behavior, jsdom does not synthesize it)', () => {
    delete window.__LUMA_MINIMAL__;
    const activate = (el: Element, key: 'Enter' | ' ') => {
      fireEvent.keyDown(el, { key });
      const button = el.tagName === 'BUTTON';
      const link = el.tagName === 'A';
      if ((button && (key === 'Enter' || key === ' ')) || (link && key === 'Enter')) fireEvent.click(el);
      fireEvent.keyUp(el, { key });
    };
    render(<DesktopDock />);
    const libraries = screen.getByRole('link', { name: 'Libraries' });
    activate(libraries, ' ');
    expect(window.location.pathname).toBe('/');
    activate(libraries, 'Enter');
    expect(window.location.pathname).toBe('/browse');
    const search = screen.getByRole('button', { name: 'Search' });
    const opens: Event[] = [];
    window.addEventListener('open-search', () => opens.push(new Event('open-search')));
    activate(search, ' ');
    activate(search, 'Enter');
    expect(opens).toHaveLength(2);
  });

  it('on the minimal build only Calendar navigates; the others stay put and do not close search', () => {
    window.__LUMA_MINIMAL__ = true;
    const beforeNav = vi.fn();
    render(<DesktopDock onBeforeNavigate={beforeNav} />);
    fireEvent.click(screen.getByRole('link', { name: 'Watchlist' }));
    fireEvent.click(screen.getByRole('link', { name: 'Libraries' }));
    fireEvent.click(screen.getByRole('link', { name: 'Explore' }));
    expect(window.location.pathname).toBe('/');
    expect(beforeNav).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('link', { name: 'Calendar' }));
    expect(window.location.pathname).toBe('/settings/tv');
    expect(beforeNav).toHaveBeenCalledTimes(1);
  });
});

describe('desktop only', () => {
  it('the phone header and the phone settings sheet do not mount the dock', () => {
    const home = readFileSync(resolve(__dirname, '../src/pages/MinimalHomePage.tsx'), 'utf8');
    const phone = home.slice(home.indexOf('data-testid="phone-header"'));
    expect(phone).toContain('data-testid="search-button"');
    expect(phone).not.toContain('DesktopDock');
    const settings = readFileSync(resolve(__dirname, '../src/pages/LibrariesPage.tsx'), 'utf8');
    expect(settings.slice(settings.indexOf('data-layout="phone"'))).not.toContain('DesktopDock');
    expect(settings).toContain('<DesktopDockRouted ');
  });
});
