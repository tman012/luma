/**
 * The one primary-action style: Luma blue. Inline-style screens use
 * primaryButtonColors; Tailwind screens use PRIMARY_BUTTON_CLASS (the same
 * --color-accent token from index.css). Plex sign-in keeps Plex amber.
 */
export const LUMA_BLUE = '#3b82f6';
export const LUMA_BLUE_HOVER = '#60a5fa';
export const PLEX_AMBER = '#e5a00d';

export const primaryButtonColors = { background: LUMA_BLUE, color: '#fff' } as const;

export const PRIMARY_BUTTON_CLASS = 'bg-accent text-white hover:bg-accent-hover';
