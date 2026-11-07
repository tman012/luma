/** Desktop top dock (approved D, top placement). Five original tile rasters in one
 * translucent bar. Search is a button (existing open-search). The other four are
 * links to the routes HomeSearchR3 already used: /browse, /explore, /settings/tv,
 * /watchlist. On the minimal build, only /settings/* is a real page, so the other
 * three keep that no-op. Home ("/") is never the current item. */
import { useEffect, useState, useSyncExternalStore, type MouseEvent } from 'react';
import { useLocation } from 'react-router';
import { DOCK_ICONS, type DockIconId } from '@/assets/desktop-nav/icons';
import { isMinimal } from '@/minimal';

/** Same event the header search button and '/' already dispatch (MinimalSearchHost.openSearch). */
function openSearch() {
  window.dispatchEvent(new Event('open-search'));
}

export const DOCK = { w: 356, h: 84, r: 26, icon: 56, gap: 12, pad: 14, top: 10 } as const;

/** Box at a viewport width. 1440 → (542,10)–(898,94), center (720, 52). */
export function dockBox(viewportWidth: number) {
  const left = viewportWidth / 2 - DOCK.w / 2;
  return {
    left, top: DOCK.top, right: left + DOCK.w, bottom: DOCK.top + DOCK.h,
    cx: viewportWidth / 2, cy: DOCK.top + DOCK.h / 2,
  };
}

/** Icon centers at 1440: 584, 652, 720, 788, 856. */
export function dockIconCenters(viewportWidth: number): number[] {
  const { left } = dockBox(viewportWidth);
  return [0, 1, 2, 3, 4].map(i => left + DOCK.pad + DOCK.icon / 2 + i * (DOCK.icon + DOCK.gap));
}

/** Paths already wired by HomeSearchR3. Not the historical mock notes. */
export const DESKTOP_DESTINATIONS = [
  { id: 'libraries', name: 'Libraries', href: '/browse' },
  { id: 'explore', name: 'Explore', href: '/explore' },
  { id: 'calendar', name: 'Calendar', href: '/settings/tv' },
  { id: 'watchlist', name: 'Watchlist', href: '/watchlist' },
] as const;

export function desktopDestActive(pathname: string, href: string): boolean {
  if (pathname === '/' || pathname === '') return false;
  return pathname === href;
}

/** Minimal has no /browse, /explore, or /watchlist page (the catch-all sends those home). */
export function desktopDestNavigates(href: string): boolean {
  return !(isMinimal() && !href.startsWith('/settings'));
}

const subscribePath = (on: () => void) => {
  window.addEventListener('popstate', on);
  return () => window.removeEventListener('popstate', on);
};

function useWindowPath(): string {
  return useSyncExternalStore(subscribePath, () => window.location.pathname, () => '/');
}

/** build.py dock(): blur 18, tints (232,228,236,64) then (20,18,28,46), 1px edge alpha 120,
 * panel shadow y6 blur 14 alpha 90. CSS backdrop-filter, not a cropped screenshot. */
export const dockCss = `
.ddock{position:fixed;z-index:56;top:${DOCK.top}px;left:50%;transform:translateX(-50%);width:${DOCK.w}px;height:${DOCK.h}px;box-sizing:border-box;
  padding:${DOCK.pad}px;display:flex;align-items:center;gap:${DOCK.gap}px;border-radius:${DOCK.r}px;overflow:visible;
  background-image:linear-gradient(rgba(20,18,28,.1804),rgba(20,18,28,.1804)),linear-gradient(rgba(232,228,236,.251),rgba(232,228,236,.251));
  -webkit-backdrop-filter:blur(18px);backdrop-filter:blur(18px);
  box-shadow:0 6px 14px rgba(0,0,0,.3529), inset 0 0 0 1px rgba(255,255,255,.4706)}
.ddock button,.ddock a{position:relative;width:${DOCK.icon}px;height:${DOCK.icon}px;min-width:44px;min-height:44px;padding:0;margin:0;border:0;background:none;
  border-radius:13px;display:block;flex:none;cursor:pointer;color:inherit;text-decoration:none}
.ddock-ico{width:${DOCK.icon}px;height:${DOCK.icon}px;display:block;pointer-events:none;filter:drop-shadow(0 2px 3px rgba(0,0,0,.3137));
  transition:filter var(--m-dur-fast, 120ms) var(--m-ease-standard, cubic-bezier(0.2,0,0,1))}
.ddock button:hover .ddock-ico,.ddock a:hover .ddock-ico{filter:drop-shadow(0 2px 3px rgba(0,0,0,.3137)) brightness(1.08)}
.ddock button:active .ddock-ico,.ddock a:active .ddock-ico{filter:drop-shadow(0 2px 3px rgba(0,0,0,.3137)) brightness(0.9)}
.ddock button:focus-visible,.ddock a:focus-visible{outline:2px solid #fff;outline-offset:3px;box-shadow:0 0 0 2px #0a0818}
.ddock a[aria-current="page"]::after{content:"";position:absolute;left:50%;margin-left:-2px;bottom:-7px;width:4px;height:4px;border-radius:50%;
  background:rgba(255,255,255,.92);pointer-events:none}
@media (prefers-reduced-motion: reduce){.ddock-ico{transition:none}}
`;

function DockArt({ id }: { id: DockIconId }) {
  const icon = DOCK_ICONS[id];
  return (
    <img className="ddock-ico" alt="" width={DOCK.icon} height={DOCK.icon} draggable={false}
      src={icon.s224} srcSet={`${icon.s56} 56w, ${icon.s144} 144w, ${icon.s224} 224w`} sizes={`${DOCK.icon}px`} />
  );
}

type Props = {
  /** Router path when one is available. Otherwise the window path (pushState). */
  pathname?: string;
  searchExpanded?: boolean;
  onSearch?: () => void;
  /** Runs only when a destination actually changes the page (closes Home search). */
  onBeforeNavigate?: () => void;
};

export function DesktopDock({ pathname, searchExpanded, onSearch, onBeforeNavigate }: Props) {
  const win = useWindowPath();
  const path = pathname ?? win;
  const [paletteOpen, setPaletteOpen] = useState(false);
  useEffect(() => {
    if (searchExpanded !== undefined) return;
    const on = (e: Event) => setPaletteOpen(!!(e as CustomEvent<{ open?: boolean }>).detail?.open);
    window.addEventListener('search-palette', on);
    return () => window.removeEventListener('search-palette', on);
  }, [searchExpanded]);
  const expanded = searchExpanded ?? paletteOpen;
  const go = (e: MouseEvent<HTMLAnchorElement>, href: string) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    if (!desktopDestNavigates(href)) return;
    onBeforeNavigate?.();
    window.history.pushState({}, '', href);
    window.dispatchEvent(new PopStateEvent('popstate'));
  };
  return (
    <>
    <style>{dockCss}</style>
    <nav className="ddock" data-testid="desktop-dock" data-desktop-dock="" aria-label="Main">
      <button type="button" aria-label="Search" aria-expanded={expanded} data-testid="dock-search"
        onClick={() => (onSearch ? onSearch() : openSearch())}>
        <DockArt id="search" />
      </button>
      {DESKTOP_DESTINATIONS.map(d => (
        <a key={d.id} href={d.href} aria-label={d.name} data-testid={`dock-${d.id}`}
          aria-current={desktopDestActive(path, d.href) ? 'page' : undefined}
          onClick={e => go(e, d.href)}>
          <DockArt id={d.id} />
        </a>
      ))}
    </nav>
    </>
  );
}

/** Same dock, current route taken from the router (settings and TV views). */
export function DesktopDockRouted(props: Omit<Props, 'pathname'>) {
  const { pathname } = useLocation();
  return <DesktopDock {...props} pathname={pathname} />;
}
