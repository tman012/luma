/** Theme tokens for inline styles: CSS variables from index.css (@theme),
 * so inline-style screens follow the same palette as Tailwind ones. */
export const T = {
  navy: 'var(--color-kiosk-black)',
  /** The flat navy page base (#15102a), the glow's middle stop. */
  navyBase: 'var(--color-kiosk-black)',
  accent: 'var(--color-accent)',
  accentHover: 'var(--color-accent-hover)',
  danger: 'var(--color-danger)',
  dangerText: 'var(--color-danger-text)',
  success: 'var(--color-success)',
  warning: 'var(--color-warning)',
  text: 'var(--color-text-primary)',
  text2: 'var(--color-text-secondary)',
  text3: 'var(--color-text-muted)',
  text4: 'var(--color-text-faint)',
  glass: 'var(--color-glass-6)',
  glass8: 'var(--color-glass-8)',
  glass12: 'var(--color-glass-12)',
  white: 'var(--color-white-pure)',
} as const;

/** Navy glass surfaces (Designer): the navy base (--color-kiosk-black, #15102a)
 * under the 6% white glass. PANEL is a full-height sheet (Settings popup);
 * POPOVER floats over the page, so it is slightly see-through and blurred. */
export const NAVY_GLASS_PANEL = 'radial-gradient(ellipse at 50% 0%, rgb(38 42 102 / 0.55) 0%, rgb(21 16 42 / 0) 60%), var(--color-kiosk-black)';
export const NAVY_GLASS_POPOVER = 'linear-gradient(var(--color-glass-6), var(--color-glass-6)), rgb(21 16 42 / 0.94)';
export const NAVY_GLASS_BORDER = '1px solid var(--color-glass-12)';
