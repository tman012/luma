# Desktop top nav: Direction B, "Violet Glass Bar"

Development record. Nothing in this file is shown in the UI.

## What ships

`components/home/DesktopDock.tsx` draws the bar and `components/search/HomeSearchR3.tsx` mounts it on the
desktop Home (1024px and wider) together with the search modal. The values live in one place, `BAR` and
`DENSITY` in `DesktopDock.tsx`, and the CSS is generated from them.

| Token | Value |
| --- | --- |
| Bar | 56px tall, radius 28, centered on x = 50% and y = 52 (top 24) |
| Glass | `backdrop-filter: blur(24px) saturate(1.6)` over `rgba(21,16,42,.56)` |
| Sheen | violet, top to bottom: `rgba(167,139,250,.24)` to `.07` at 48% to `0` |
| Edge | 1px inset `rgba(196,181,253,.30)` |
| Segment | 46px tall, radius 23, 5px inside the bar |
| Icon | 22px Material Symbols Rounded, weight 500, outlined; FILL 1 when current |
| Label | 15px, weight 600, the app font stack (`FONT` in `components/start/StartUI.tsx`) |
| Current pill | `linear-gradient(135deg, rgba(104,64,232,.80), rgba(37,99,235,.76))`, slides between items |
| Hover | `rgba(255,255,255,.10)` over `rgba(21,16,42,.26)` |
| Pressed | `rgba(10,7,24,.44)` with an inset shadow |
| Focus ring | 2px `#ede9fe` at 2px offset, on a navy gap, with a 3px `rgba(99,102,241,.78)` halo |

The bar, fill, pill, hover, blur, radius, heights, icon size and label values are the ones given in the
approved Direction B spec. The sheen stops, the edge alpha, the pressed fill, the shadows, the segment
padding and gaps, and the focus ring were not available as numbers when this was built (the mockup's
`tokens.json` and `build.py` were not in the repository or the build environment), so they were chosen to
match the description. Check them against those files and correct `BAR` and `DENSITY` if they differ.

## Width

At full spacing the bar is about 600px wide (606px with Chrome's Linux fallback font). The bar measures
its label widths and the logo and avatar on screen (`[data-dock-avoid]`), and keeps 24px clear of both:
labels at full spacing when they fit, then tighter padding and gaps, then icons only (labels stay as the
accessible names). `barWidth`, `barAvailable`, `barDensity` and `headerEdges` are the whole rule.

## Icons

Material Symbols Rounded, weight 500, grade 0, optical size 24, from the npm package
`@material-symbols/svg-500` version 0.48.3 (Apache-2.0, github.com/marella/material-symbols, built from
Google's Material Symbols). The upstream files are in `symbols/`, unmodified, with the package's
`LICENSE` and a `SHA256SUMS` of every file:

| Segment | Symbol | Files |
| --- | --- | --- |
| Search | `search` | `search.svg` (the symbol has no separate fill) |
| Libraries | `video_library` | `video_library.svg`, `video_library-fill.svg` |
| Explore | `explore` | `explore.svg`, `explore-fill.svg` |
| Calendar | `calendar_month` | `calendar_month.svg`, `calendar_month-fill.svg` |
| Watchlist | `bookmark` | `bookmark.svg`, `bookmark-fill.svg` |
| Search modal close | `close` | `close.svg` |

`symbols.ts` imports them as text and the bar draws the paths inline with `currentColor`. No icon font
and no CDN request at runtime.

## Replaced

The five raster Mac-style tiles (`icons/*.png`, `icons.ts`) and their handoff notes were removed with the
old dock; nothing else used them. They remain in git history at 7f519eb0.
