/**
 * UI Density Tokens — standardized sizing tokens driven by device context.
 *
 * Replaces ad-hoc `isTablet ? X : Y` ternaries with a centralized token system.
 * Tokens are selected based on UiDensity (primarily isTablet: expanded+touch vs
 * expanded+mouse). All values match existing component behavior exactly.
 *
 * To extend:
 *   1. Add a new field to UiDensityTokens
 *   2. Add tablet/desktop values in uiDensityTokens()
 *   3. Use in components: `const d = useUiDensity(); const tk = uiDensityTokens(d);`
 */

import type { UiDensity } from '@/hooks/useUiDensity';

export interface UiDensityTokens {
  /** Touch/click target minimums */
  targets: {
    /** min-h + min-w for interactive elements (buttons, clear icons) */
    minBtn: string;
  };

  /** Spacing: gaps, padding */
  spacing: {
    /** Rail scroll container gap */
    railGap: string;
    /** Top-bar horizontal padding */
    headerPadX: string;
    /** Top-bar nav items gap */
    navGap: string;
    /** Top-bar right-side actions gap (time, bell, voice) */
    actionsGap: string;
    /** Search panel horizontal padding */
    searchPadX: string;
  };

  /** Font sizes */
  typography: {
    /** Rail section title (h2) */
    railTitle: string;
    /** Top-bar nav item + time display text */
    navText: string;
  };

  /** Top-bar navigation sizing */
  nav: {
    /** Nav item minimum height (touch target) */
    itemMinH: string;
  };

  /** Search panel positioning and sizing */
  search: {
    /** Search bar + results panel max-width */
    barMaxW: string;
    /** Panel top offset when search is active */
    activeTop: string;
    /** Panel top offset when idle (centered position) */
    idleTop: string;
    /** Tab pill padding + font size + optional min-height */
    tabPill: string;
  };

  /** Person card sizing (search results) */
  person: {
    /** Person photo dimensions */
    cardImg: string;
    /** Person name/role text container width */
    cardTextW: string;
    /** Person role/department font size */
    roleText: string;
  };
}

export function uiDensityTokens(d: UiDensity): UiDensityTokens {
  const t = d.isTablet; // expanded + touch (tablet/kiosk)

  return {
    targets: {
      minBtn: t
        ? 'min-h-[48px] min-w-[48px]'
        : 'min-h-[44px] min-w-[44px]',
    },

    spacing: {
      railGap: t ? 'gap-4' : 'gap-5',
      headerPadX: t ? 'px-6' : 'px-[60px]',
      navGap: t ? 'gap-6' : 'gap-14',
      actionsGap: t ? 'gap-6' : 'gap-12',
      searchPadX: t ? 'px-4' : 'px-[clamp(16px,4vw,60px)]',
    },

    typography: {
      railTitle: t ? 'text-[22px]' : 'text-[28px]',
      navText: t ? 'text-[36px]' : 'text-[clamp(28px,2.5vw,48px)]',
    },

    nav: {
      itemMinH: t ? 'min-h-[48px]' : 'min-h-[64px]',
    },

    search: {
      barMaxW: t ? 'max-w-[700px]' : 'max-w-[900px]',
      activeTop: t ? 'top-[80px]' : 'top-[110px]',
      idleTop: t
        ? 'top-[calc(var(--vvh,100vh)*0.30)]'
        : 'top-[40vh]',
      tabPill: t
        ? 'px-4 py-2 text-[14px] min-h-[48px]'
        : 'px-5 py-1.5 text-[16px]',
    },

    person: {
      cardImg: t ? 'h-[195px] w-[130px]' : 'h-[180px] w-[120px]',
      cardTextW: t ? 'w-[130px]' : 'w-[120px]',
      roleText: t ? 'text-[15px]' : 'text-[14px]',
    },
  };
}
