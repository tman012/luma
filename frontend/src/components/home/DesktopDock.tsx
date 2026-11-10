/** Desktop top nav, Direction B "Violet Glass Bar". One slim glass bar, centered at the top of Home
 * (56px tall, centered on y=52 at any width). Segments in order: Search, Libraries, Explore, Calendar,
 * Watchlist. Search is a button that opens the search modal (HomeSearchR3). The others are links that go
 * straight to their page; nothing opens a menu, and no segment is a dead button: a destination is drawn
 * only when the running build has a page for it (desktopDestinations). The bar is not drawn on any
 * Settings route (dockVisible). Home ("/") is never the current item. */
import {
  useCallback, useLayoutEffect, useRef, useState, useSyncExternalStore,
  type CSSProperties, type MouseEvent, type Ref,
} from 'react';
import { DOCK_SYMBOLS, SYMBOL_VIEWBOX, type DockIconId } from '@/assets/desktop-nav/symbols';
import { FONT } from '@/components/start/StartUI';
import { isMinimal } from '@/minimal';
import { minimalRouteExists } from '@/minimalRoutes';

/** Same event the header search button and '/' already dispatch (MinimalSearchHost.openSearch). */
function openSearch() {
  window.dispatchEvent(new Event('open-search'));
}

/** Direction B tokens. cy is the bar's vertical center, so top = cy - h / 2 = 24. */
export const BAR = {
  h: 56, r: 28, cy: 52, top: 24, pad: 5,
  seg: 46, segR: 23, icon: 22, labelSize: 15, labelWeight: 600,
  blur: 24, saturate: 1.6,
  fill: 'rgba(21,16,42,.56)',
  sheen: 'linear-gradient(180deg,rgba(150,132,255,.16),rgba(150,132,255,0) 70%)',
  edge: 'rgba(196,186,255,.30)',
  pillFrom: 'rgba(104,64,232,.80)', pillTo: 'rgba(37,99,235,.76)',
  hoverLight: 'rgba(255,255,255,.10)', hoverBase: 'rgba(21,16,42,.26)',
  pressed: 'rgba(10,6,28,.34)',
  /** Smallest gap kept between the bar and the logo or the avatar. */
  clearance: 24,
} as const;

export type DockDensity = 'full' | 'tight' | 'icons';

/** Segment left and right padding, gap between segments, gap between icon and label. icons: 46px squares.
 * full is the mockup's 0 18px 0 14px segment with 2px between segments. */
export const DENSITY: Record<DockDensity, { padL: number; padR: number; gap: number; iconGap: number }> = {
  full: { padL: 14, padR: 18, gap: 2, iconGap: 8 },
  tight: { padL: 8, padR: 8, gap: 2, iconGap: 6 },
  icons: { padL: (BAR.seg - BAR.icon) / 2, padR: (BAR.seg - BAR.icon) / 2, gap: 4, iconGap: 0 },
};
const DENSITY_ORDER: DockDensity[] = ['full', 'tight', 'icons'];

/** The desktop header the bar sits in: logo 916x416 drawn 64px tall at 44px, a 48px avatar 60px from the right. */
export const HEADER = { padLeft: 44, padRight: 60, logoW: 916 * (64 / 416), avatar: 48 } as const;

/** Bar width for a density, from the measured label widths (same numbers the CSS uses). */
export function barWidth(density: DockDensity, labelWidths: readonly number[]): number {
  const d = DENSITY[density];
  const n = labelWidths.length;
  const segments = density === 'icons'
    ? n * BAR.seg
    : labelWidths.reduce((sum, w) => sum + d.padL + d.padR + BAR.icon + d.iconGap + w, 0);
  return 2 * BAR.pad + Math.max(0, n - 1) * d.gap + segments;
}

/** Widest centered bar that stays `clearance` away from the logo (its right edge) and the avatar (its left edge). */
export function barAvailable(viewportWidth: number, logoRight: number, avatarLeft: number, clearance: number = BAR.clearance): number {
  const half = Math.min(viewportWidth / 2 - logoRight, avatarLeft - viewportWidth / 2) - clearance;
  return Math.max(0, 2 * half);
}

/** Labels at full spacing when they fit, then tighter spacing, then icons only. */
export function barDensity(available: number, labelWidths: readonly number[]): DockDensity {
  for (const d of DENSITY_ORDER) if (barWidth(d, labelWidths) <= available) return d;
  return 'icons';
}

/** Box of the centered bar at a viewport width. */
export function barBox(viewportWidth: number, width: number) {
  const left = viewportWidth / 2 - width / 2;
  return { left, top: BAR.top, right: left + width, bottom: BAR.top + BAR.h, cx: viewportWidth / 2, cy: BAR.cy };
}

/** Logo and avatar edges: the known header layout, widened by whatever is really on screen ([data-dock-avoid]). */
export function headerEdges(viewportWidth: number, rects: readonly { left: number; right: number; width: number }[] = []) {
  let logoRight = HEADER.padLeft + HEADER.logoW;
  let avatarLeft = viewportWidth - HEADER.padRight - HEADER.avatar;
  for (const r of rects) {
    if (!r.width) continue;
    if (r.left + r.width / 2 < viewportWidth / 2) logoRight = Math.max(logoRight, r.right);
    else avatarLeft = Math.min(avatarLeft, r.left);
  }
  return { logoRight, avatarLeft };
}

export type DesktopDestination = { id: Exclude<DockIconId, 'search'>; name: string; href: string };

/** href: the full app's page. minimalHref: the page a LUMA_MINIMAL build has for the same thing, when its
 * path differs. The minimal app has no /browse, /explore or /watchlist page (App.tsx MinimalApp, app/minimal.py);
 * its one library browser is the TV Series view, which is where Libraries goes there. */
export const DESKTOP_DESTINATIONS: readonly (DesktopDestination & { minimalHref?: string })[] = [
  { id: 'libraries', name: 'Libraries', href: '/browse', minimalHref: '/settings/tv?view=series' },
  { id: 'explore', name: 'Explore', href: '/explore' },
  { id: 'calendar', name: 'Calendar', href: '/settings/tv' },
  { id: 'watchlist', name: 'Watchlist', href: '/watchlist' },
];

/** The destinations this build can really open, in bar order. On a minimal build a destination is kept only
 * when its route is in the minimal route table, so a page added there shows up here with no other change. */
export function desktopDestinations(minimal: boolean = isMinimal()): DesktopDestination[] {
  return DESKTOP_DESTINATIONS.flatMap(({ minimalHref, ...d }) => {
    if (!minimal) return [d];
    const href = minimalHref ?? d.href;
    return minimalRouteExists(href) ? [{ ...d, href }] : [];
  });
}

export function desktopDestActive(pathname: string, href: string): boolean {
  if (pathname === '/' || pathname === '') return false;
  return pathname === href;
}

/** The bar is not shown on any Settings route, /settings/tv (Calendar's destination) included. */
export function dockVisible(pathname: string): boolean {
  const p = pathname.replace(/\/+$/, '');
  return !(p === '/settings' || p.startsWith('/settings/'));
}

const subscribePath = (on: () => void) => {
  window.addEventListener('popstate', on);
  return () => window.removeEventListener('popstate', on);
};

function useWindowPath(): string {
  return useSyncExternalStore(subscribePath, () => window.location.pathname, () => '/');
}

const D = DENSITY;
const densityVars = (d: DockDensity) => `--vgb-pad-l:${D[d].padL}px;--vgb-pad-r:${D[d].padR}px;--vgb-gap:${D[d].gap}px;--vgb-icon-gap:${D[d].iconGap}px`;
const labelFont = `font-family:${FONT};font-size:${BAR.labelSize}px;font-weight:${BAR.labelWeight};line-height:1;letter-spacing:.1px`;

export const dockCss = `
.vgb{position:fixed;z-index:56;top:${BAR.top}px;left:50%;transform:translateX(-50%);height:${BAR.h}px;box-sizing:border-box;
  padding:${BAR.pad}px;display:flex;align-items:center;gap:var(--vgb-gap);border-radius:${BAR.r}px;overflow:visible;white-space:nowrap;
  ${densityVars('full')};
  background:${BAR.sheen},${BAR.fill};
  -webkit-backdrop-filter:blur(${BAR.blur}px) saturate(${BAR.saturate});backdrop-filter:blur(${BAR.blur}px) saturate(${BAR.saturate});
  box-shadow:0 8px 24px rgba(8,4,24,.36),inset 0 1px 0 rgba(255,255,255,.26),inset 0 0 0 1px ${BAR.edge};
  -webkit-font-smoothing:antialiased}
.vgb[data-density="tight"]{${densityVars('tight')}}
.vgb[data-density="icons"]{${densityVars('icons')}}
.vgb-item{position:relative;z-index:1;flex:none;height:${BAR.seg}px;min-width:${BAR.seg}px;box-sizing:border-box;padding:0 var(--vgb-pad-r) 0 var(--vgb-pad-l);margin:0;border:0;
  display:inline-flex;align-items:center;justify-content:center;gap:var(--vgb-icon-gap);border-radius:${BAR.segR}px;background:none;
  color:rgba(255,255,255,.92);${labelFont};text-decoration:none;text-shadow:0 1px 2px rgba(8,5,24,.28);cursor:pointer;outline:none;
  -webkit-tap-highlight-color:transparent;user-select:none;-webkit-user-select:none;
  transition:color var(--m-dur-fast, 120ms) var(--m-ease-standard, cubic-bezier(0.2,0,0,1))}
.vgb-item::before{content:"";position:absolute;inset:0;border-radius:inherit;opacity:0;pointer-events:none;
  background:linear-gradient(${BAR.hoverLight},${BAR.hoverLight}),${BAR.hoverBase};box-shadow:inset 0 0 0 1px rgba(255,255,255,.24);
  transition:opacity var(--m-dur-fast, 120ms) var(--m-ease-standard, cubic-bezier(0.2,0,0,1))}
.vgb-item:hover{color:#fff}
.vgb-item:hover::before{opacity:1}
.vgb-item[data-current="true"]{color:#fff}
.vgb-item[data-current="true"]::before{opacity:0}
.vgb-item:active::before,.vgb-item[data-current="true"]:active::before{opacity:1;background:${BAR.pressed};
  box-shadow:inset 0 2px 5px rgba(0,0,0,.30)}
.vgb-item:focus-visible{outline:2px solid #ede9fe;outline-offset:2px;box-shadow:0 0 0 4px rgba(21,16,42,.72),0 0 0 7px rgba(99,102,241,.78)}
.vgb-ico{position:relative;flex:none;width:${BAR.icon}px;height:${BAR.icon}px;display:block;fill:currentColor;pointer-events:none}
.vgb-label{position:relative;display:block;pointer-events:none}
.vgb[data-density="icons"] .vgb-label{position:absolute;width:1px;height:1px;margin:-1px;padding:0;border:0;overflow:hidden;
  clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap}
.vgb-pill{position:absolute;z-index:0;left:0;top:${BAR.pad}px;height:${BAR.seg}px;width:var(--vgb-pill-w, 0px);transform:translateX(var(--vgb-pill-x, 0px));
  border-radius:${BAR.segR}px;pointer-events:none;opacity:0;
  background:linear-gradient(180deg,rgba(255,255,255,.14),rgba(255,255,255,0) 70%),linear-gradient(135deg,${BAR.pillFrom},${BAR.pillTo});
  box-shadow:0 4px 12px rgba(60,40,190,.40),inset 0 1px 0 rgba(255,255,255,.45),inset 0 0 0 1px rgba(214,206,255,.55);
  transition:transform var(--m-dur-standard, 240ms) var(--m-ease-standard, cubic-bezier(0.2,0,0,1)),
    width var(--m-dur-standard, 240ms) var(--m-ease-standard, cubic-bezier(0.2,0,0,1)),
    opacity var(--m-dur-fast, 120ms) var(--m-ease-standard, cubic-bezier(0.2,0,0,1))}
.vgb-pill[data-visible="true"]{opacity:1}
.vgb-pill[data-instant="true"]{transition:opacity var(--m-dur-fast, 120ms) var(--m-ease-standard, cubic-bezier(0.2,0,0,1))}
.vgb-ruler{position:fixed;left:0;top:0;visibility:hidden;pointer-events:none;white-space:nowrap;${labelFont}}
.vgb-ruler span{display:inline-block}
@media (prefers-reduced-motion: reduce){.vgb-pill,.vgb-pill[data-instant="true"],.vgb-item,.vgb-item::before{transition:none}}
@media (max-width: 1023.98px){.vgb,.vgb-ruler{display:none}}
`;

function Symbol({ id, filled }: { id: DockIconId; filled: boolean }) {
  const s = DOCK_SYMBOLS[id];
  return (
    <svg className="vgb-ico" viewBox={SYMBOL_VIEWBOX} width={BAR.icon} height={BAR.icon} aria-hidden="true" focusable="false"
      data-symbol={s.name} data-fill={filled ? '1' : '0'}>
      <path d={filled ? s.fill : s.outline} />
    </svg>
  );
}

type Props = {
  /** Router path when one is available. Otherwise the window path (pushState). */
  pathname?: string;
  /** The search modal is open: Search is the current segment. */
  searchOpen?: boolean;
  onSearch?: () => void;
  /** Runs only when a destination actually changes the page (closes Home search). */
  onBeforeNavigate?: () => void;
  /** The Search button, so the search modal can hand focus back to it. */
  searchRef?: Ref<HTMLButtonElement>;
};

export function DesktopDock({ pathname, searchOpen = false, onSearch, onBeforeNavigate, searchRef }: Props) {
  const win = useWindowPath();
  const path = pathname ?? win;
  const visible = dockVisible(path);
  const navRef = useRef<HTMLElement>(null);
  const rulerRef = useRef<HTMLSpanElement>(null);
  const [density, setDensity] = useState<DockDensity>('full');
  const [measured, setMeasured] = useState(0);
  const [pill, setPill] = useState({ x: 0, w: 0, visible: false, instant: true });
  const lastCurrent = useRef<string | null>(null);

  // aria-current follows the page. The pill and the filled icon also mark Search while its modal is open.
  const destinations = desktopDestinations();
  const labels = ['Search', ...destinations.map(d => d.name)];
  const labelKey = labels.join('|');
  const pageId = destinations.find(d => desktopDestActive(path, d.href))?.id ?? null;
  const currentId = searchOpen ? 'search' : pageId;

  const measure = useCallback(() => {
    const ruler = rulerRef.current;
    if (!ruler) return;
    const vw = document.documentElement.clientWidth || window.innerWidth;
    const labels = [...ruler.children].map(c => Math.ceil(c.getBoundingClientRect().width));
    const rects = [...document.querySelectorAll('[data-dock-avoid]')].map(el => el.getBoundingClientRect());
    const { logoRight, avatarLeft } = headerEdges(vw, rects);
    setDensity(barDensity(barAvailable(vw, logoRight, avatarLeft), labels));
    setMeasured(n => n + 1);
  }, []);

  useLayoutEffect(() => {
    if (!visible) return;
    measure();
    window.addEventListener('resize', measure);
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    if (ro) for (const el of document.querySelectorAll('[data-dock-avoid]')) ro.observe(el);
    let live = true;
    void document.fonts?.ready?.then(() => { if (live) measure(); });
    return () => { live = false; window.removeEventListener('resize', measure); ro?.disconnect(); };
  }, [visible, measure, labelKey]);

  // The pill sits on the current segment. It slides only when the current segment changes;
  // a first appearance or a re-measure places it at once.
  useLayoutEffect(() => {
    const el = currentId ? navRef.current?.querySelector<HTMLElement>(`[data-dock-item="${currentId}"]`) : null;
    const moved = !!currentId && lastCurrent.current !== null && lastCurrent.current !== currentId;
    lastCurrent.current = currentId;
    setPill(p => {
      const next = el ? { x: el.offsetLeft, w: el.offsetWidth, visible: true, instant: !moved } : { ...p, visible: false };
      return next.x === p.x && next.w === p.w && next.visible === p.visible && next.instant === p.instant ? p : next;
    });
  }, [currentId, density, measured]);

  if (!visible) return null;

  const go = (e: MouseEvent<HTMLAnchorElement>, href: string) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    onBeforeNavigate?.();
    window.history.pushState({}, '', href);
    window.dispatchEvent(new PopStateEvent('popstate'));
  };
  return (
    <>
    <style>{dockCss}</style>
    <span ref={rulerRef} className="vgb-ruler" aria-hidden="true">
      {labels.map(l => <span key={l}>{l}</span>)}
    </span>
    <nav ref={navRef} className="vgb" data-testid="desktop-dock" data-desktop-dock="" data-density={density} aria-label="Main">
      <span className="vgb-pill" data-testid="dock-pill" aria-hidden="true" data-visible={pill.visible ? 'true' : 'false'}
        data-instant={pill.instant ? 'true' : 'false'}
        style={{ '--vgb-pill-x': `${pill.x}px`, '--vgb-pill-w': `${pill.w}px` } as CSSProperties} />
      <button ref={searchRef} type="button" className="vgb-item" data-testid="dock-search" data-dock-item="search"
        aria-haspopup="dialog" data-current={currentId === 'search' ? 'true' : undefined}
        onClick={() => (onSearch ? onSearch() : openSearch())}>
        <Symbol id="search" filled={currentId === 'search'} />
        <span className="vgb-label">Search</span>
      </button>
      {destinations.map(d => {
        const on = currentId === d.id;
        return (
          <a key={d.id} href={d.href} className="vgb-item" data-testid={`dock-${d.id}`} data-dock-item={d.id}
            aria-current={pageId === d.id ? 'page' : undefined} data-current={on ? 'true' : undefined}
            onClick={e => go(e, d.href)}>
            <Symbol id={d.id} filled={on} />
            <span className="vgb-label">{d.name}</span>
          </a>
        );
      })}
    </nav>
    </>
  );
}
