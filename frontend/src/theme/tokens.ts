/** Theme tokens for inline styles: CSS variables from index.css (@theme),
 * so inline-style screens follow the same palette as Tailwind ones. */
export const T = {
  navy: 'var(--color-kiosk-black)',
  accent: 'var(--color-accent)',
  accentHover: 'var(--color-accent-hover)',
  danger: 'var(--color-danger)',
  dangerText: 'var(--color-danger-text)',
  success: 'var(--color-success)',
  text: 'var(--color-text-primary)',
  text2: 'var(--color-text-secondary)',
  text3: 'var(--color-text-muted)',
  text4: 'var(--color-text-faint)',
  glass: 'var(--color-glass-6)',
  glass8: 'var(--color-glass-8)',
  glass12: 'var(--color-glass-12)',
  white: 'var(--color-white-pure)',
} as const;
