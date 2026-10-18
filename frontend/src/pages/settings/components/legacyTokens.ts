/**
 * v174 (Designer, legacy-settings-modal-fix.md): tokens for the legacy Settings surfaces. Navy brand
 * surfaces, glass cards, blue primary; no black or near-black, no green or amber outside real state.
 */
export const LEGACY = {
  scrim: 'rgba(21,16,42,0.72)',
  shell: 'rgba(21,16,42,0.92)',
  shellBorder: 'rgba(255,255,255,0.08)',
  navDivider: 'rgba(255,255,255,0.06)',
  card: 'rgba(255,255,255,0.06)',
  cardBorder: 'rgba(255,255,255,0.08)',
  inset: 'rgba(255,255,255,0.04)',
  input: 'rgba(255,255,255,0.06)',
  inputBorder: 'rgba(255,255,255,0.12)',
  primary: '#3b82f6',
  primaryHover: '#60a5fa',
  secondary: 'rgba(255,255,255,0.08)',
  secondaryBorder: 'rgba(255,255,255,0.12)',
  activeNav: 'rgba(59,130,246,0.16)',
  toggleOff: 'rgba(255,255,255,0.2)',
} as const;

/** Glass secondary button (Reset, Run now, Preview pick, Rebuild). */
export const SECONDARY_BTN =
  'inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.12] bg-white/[0.08] px-4 min-h-[40px] text-[13px] font-semibold text-white/90 transition-colors hover:bg-white/[0.12] disabled:opacity-40 disabled:cursor-not-allowed';

/** Primary button (Save): blue, hover lighter, disabled at 40%. */
export const PRIMARY_BTN =
  'inline-flex items-center justify-center gap-2 rounded-[10px] bg-[#3b82f6] px-4 min-h-[40px] text-[13px] font-semibold text-white transition-colors hover:bg-[#60a5fa] disabled:opacity-40 disabled:hover:bg-[#3b82f6] disabled:cursor-not-allowed';

/** Inset box (stat tiles, Last run). */
export const INSET_BOX = 'rounded-[12px] border border-white/[0.08] bg-white/[0.04]';

/** Inputs and selects: white 6%, 12% border, radius 10, blue focus ring. */
export const INPUT_CLASS =
  'w-full min-h-[44px] rounded-[10px] border border-white/[0.12] bg-white/[0.06] px-3 py-2.5 text-[14px] font-normal text-white placeholder:text-white/30 outline-none transition-[border-color,box-shadow] focus:border-[#3b82f6] focus:shadow-[0_0_0_3px_rgba(59,130,246,0.25)] disabled:opacity-40';
