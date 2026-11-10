/** Material Symbols Rounded, weight 500 (Apache-2.0, see symbols/LICENSE and SOURCES.md).
 * The upstream SVG files are bundled as text and drawn inline, so nothing loads from a CDN at runtime. */
import search from './symbols/search.svg?raw';
import videoLibrary from './symbols/video_library.svg?raw';
import videoLibraryFill from './symbols/video_library-fill.svg?raw';
import explore from './symbols/explore.svg?raw';
import exploreFill from './symbols/explore-fill.svg?raw';
import calendarMonth from './symbols/calendar_month.svg?raw';
import calendarMonthFill from './symbols/calendar_month-fill.svg?raw';
import bookmark from './symbols/bookmark.svg?raw';
import bookmarkFill from './symbols/bookmark-fill.svg?raw';
import close from './symbols/close.svg?raw';

export type DockIconId = 'search' | 'libraries' | 'explore' | 'calendar' | 'watchlist';

export const SYMBOL_VIEWBOX = '0 -960 960 960';

/** The single path of an upstream Material Symbols SVG. */
export function symbolPath(svg: string): string {
  const m = / d="([^"]+)"/.exec(svg);
  if (!m) throw new Error('Material Symbols SVG without a path');
  return m[1]!;
}

/** Symbol name, the outlined path (FILL 0) and the filled path (FILL 1). search has no separate fill. */
export const DOCK_SYMBOLS: Record<DockIconId, { name: string; outline: string; fill: string }> = {
  search: { name: 'search', outline: symbolPath(search), fill: symbolPath(search) },
  libraries: { name: 'video_library', outline: symbolPath(videoLibrary), fill: symbolPath(videoLibraryFill) },
  explore: { name: 'explore', outline: symbolPath(explore), fill: symbolPath(exploreFill) },
  calendar: { name: 'calendar_month', outline: symbolPath(calendarMonth), fill: symbolPath(calendarMonthFill) },
  watchlist: { name: 'bookmark', outline: symbolPath(bookmark), fill: symbolPath(bookmarkFill) },
};

export const CLOSE_SYMBOL = symbolPath(close);
