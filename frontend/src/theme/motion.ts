/** Motion tokens (MOTION.md §0). The CSS twins live in index.css :root as --m-*; keep the two in sync. */
const SPRING = 'linear(0, 0.25 7%, 0.6 16%, 0.86 27%, 0.97 38%, 1.005 50%, 1.002 65%, 1)';

export const MOTION = {
  dur: { instant: 0, fast: 120, standard: 240, emphasized: 360, sheetEnter: 380, sheetExit: 260, press: 100, image: 240, crossfade: 200, dismissExit: 300 },
  ease: {
    standard: 'cubic-bezier(0.2,0,0,1)',
    decel: 'cubic-bezier(0.05,0.7,0.1,1)',
    accel: 'cubic-bezier(0.3,0,0.8,0.15)',
    spring: SPRING,
  },
  spring: { stiffness: 380, damping: 36, mass: 1 },
  rise: 6, routeShift: 8, stagger: 30, staggerMax: 240, pressScale: 0.98, scrim: 0.6,
  z: { page: 0, nav: 100, overlay: 9000, sheet: 10000, sheetNested: 10050, toast: 10080, system: 10100 },
} as const;

/** The app background behind every screen and sheet (MOTION.md §7 check 12). */
export const APP_BG = '#111318';

export const reducedMotion = (): boolean =>
  typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
