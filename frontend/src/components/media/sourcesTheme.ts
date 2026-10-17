/** Sources surfaces shared by the phone, full-screen and desktop (Expanded)
 * Sources views, so every path draws the same navy theme. */
import type React from 'react';
import { START_GLOW } from '@/components/start/StartUI';

export const SRC_NAVY = '#15102a';
/** Primary blue (#3b82f6) via the theme token. */
export const SRC_BLUE = 'var(--color-accent)';

/** Panel / full-screen backdrop: navy with the Start page glow. */
export const srcPanel: React.CSSProperties = {
  backgroundColor: 'var(--color-kiosk-black)', backgroundImage: START_GLOW,
};

/** Content that must sit above a dim or glow layer (text stays readable). */
export const srcAbove: React.CSSProperties = { position: 'relative', zIndex: 1 };

/** Empty, error and loading headings. */
export const SRC_HEADING_COLOR = 'rgba(255,255,255,0.7)';

/** Confirm card: navy glass, 8% white border, radius 20. */
export const srcConfirmCard: React.CSSProperties = {
  background: `linear-gradient(rgba(255,255,255,0.06), rgba(255,255,255,0.06)), ${SRC_NAVY}`,
  border: '1px solid rgba(255,255,255,0.08)', borderRadius: '20px',
  ...srcAbove,
};

/** Release rows: 4% white, 8% when expanded or hovered. */
export const SRC_ROW_BG = 'rgba(255,255,255,0.04)';
export const SRC_ROW_BG_ACTIVE = 'rgba(255,255,255,0.08)';

/** Primary Download. */
export const srcPrimaryBtn: React.CSSProperties = { background: SRC_BLUE, border: `1px solid ${SRC_BLUE}`, color: '#fff' };

/** Cancel: glass with a 12% white border. */
export const srcCancelBtn: React.CSSProperties = {
  background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.12)', color: '#fff',
};

/** Hover rule for [data-source-row] / [data-source-tile] (inline styles can't hover). */
export const SRC_HOVER_CSS = `@media (hover: hover) {
  [data-source-tile]:hover, [data-source-row]:hover { background: ${SRC_ROW_BG_ACTIVE} !important; }
}`;

/** "1 source", "2 sources". */
export const sourcesCount = (n: number) => `${n} ${n === 1 ? 'source' : 'sources'}`;

/** One empty-state line everywhere (phone and desktop). */
export const NO_SOURCES = 'No sources';
