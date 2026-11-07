/** Original revision-dock tile rasters (Pillow, transparent RGBA). Not glyphs. */
import search56 from './icons/luma-dock-search-56.png';
import search144 from './icons/luma-dock-search-144.png';
import search224 from './icons/luma-dock-search-224.png';
import libraries56 from './icons/luma-dock-libraries-56.png';
import libraries144 from './icons/luma-dock-libraries-144.png';
import libraries224 from './icons/luma-dock-libraries-224.png';
import explore56 from './icons/luma-dock-explore-56.png';
import explore144 from './icons/luma-dock-explore-144.png';
import explore224 from './icons/luma-dock-explore-224.png';
import calendar56 from './icons/luma-dock-calendar-56.png';
import calendar144 from './icons/luma-dock-calendar-144.png';
import calendar224 from './icons/luma-dock-calendar-224.png';
import watchlist56 from './icons/luma-dock-watchlist-56.png';
import watchlist144 from './icons/luma-dock-watchlist-144.png';
import watchlist224 from './icons/luma-dock-watchlist-224.png';

export type DockIconId = 'search' | 'libraries' | 'explore' | 'calendar' | 'watchlist';

/** 56 CSS px at 1× / ~2.5× / 4×. Masters (1024) stay on disk; they are not shipped in the bundle. */
export const DOCK_ICONS: Record<DockIconId, { s56: string; s144: string; s224: string }> = {
  search: { s56: search56, s144: search144, s224: search224 },
  libraries: { s56: libraries56, s144: libraries144, s224: libraries224 },
  explore: { s56: explore56, s144: explore144, s224: explore224 },
  calendar: { s56: calendar56, s144: calendar144, s224: calendar224 },
  watchlist: { s56: watchlist56, s144: watchlist144, s224: watchlist224 },
};
