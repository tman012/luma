/** Motion tokens (MOTION.md §0). The CSS twins live in index.css :root as --m-*; keep the two in sync. */
const SPRING = 'linear(0, 0.25 7%, 0.6 16%, 0.86 27%, 0.97 38%, 1.005 50%, 1.002 65%, 1)';

/** Phone sheet motion: the ONE place to change it (enter, exit, scrim, drag). Everything that moves the phone
 *  sheet (SheetFrame, useModalMotion 'bottom', useSwipeToDismiss, AppModalHost's Back wait) reads these through
 *  MOTION. Designer tokens, MOTION.md amendment 2026-10-30 11:32 PM ET. */
export const SHEET_MOTION = {
  enterMs: 560, enterEase: 'cubic-bezier(0.22,0.61,0.36,1)',            // soft decelerate, no overshoot or bounce
  scrimEnterMs: 560, scrimEnterEase: 'cubic-bezier(0.4,0,0.2,1)',       // starts on the same frame as the sheet
  exitMs: 340, exitEase: 'cubic-bezier(0.4,0,1,1)',                     // sheet exit
  scrimExitMs: 340, scrimExitEase: 'cubic-bezier(0.4,0,1,1)',           // scrim exit
  dragMinMs: 220,   // drag release: same curves, duration scaled by the distance left, never below this
} as const;
const SHEET_ENTER = SHEET_MOTION.enterMs, SHEET_EXIT = SHEET_MOTION.exitMs, MODAL_EXIT_DESK = 300;
export const MOTION = {
  dur: { instant: 0, fast: 120, standard: 240, emphasized: 360, sheetEnter: SHEET_ENTER, sheetExit: SHEET_EXIT, sheetDragMin: SHEET_MOTION.dragMinMs,
    sheetScrimEnter: SHEET_MOTION.scrimEnterMs, sheetScrimExit: SHEET_MOTION.scrimExitMs,
    press: 100, image: 240, crossfade: 200,
    /** Back (popstate) waits for the longest exit: the phone sheet's, or the desktop expanded modal's owner timer. */
    dismissExit: Math.max(SHEET_EXIT, MODAL_EXIT_DESK) },
  ease: {
    standard: 'cubic-bezier(0.2,0,0,1)',
    decel: 'cubic-bezier(0.05,0.7,0.1,1)',
    accel: 'cubic-bezier(0.3,0,0.8,0.15)',
    spring: SPRING,
    sheet: SHEET_MOTION.enterEase,
    sheetExit: SHEET_MOTION.exitEase,
    sheetScrimEnter: SHEET_MOTION.scrimEnterEase,
    sheetScrimExit: SHEET_MOTION.scrimExitEase,
  },
  spring: { stiffness: 380, damping: 36, mass: 1 },
  rise: 6, routeShift: 8, stagger: 30, staggerMax: 240, pressScale: 0.98, scrim: 0.6,
  z: { page: 0, nav: 100, overlay: 9000, sheet: 10000, sheetNested: 10050, toast: 10080, system: 10100 },
} as const;

/** The app background behind every screen and sheet (MOTION.md §7 check 12). */
export const APP_BG = '#111318';

export const reducedMotion = (): boolean =>
  typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** The CSS twins of SHEET_MOTION (--m-sheet-enter, --m-ease-sheet, ...), written from the tokens above so the
 *  values live in one place. */
export const sheetMotionCss = (): string => `:root{--m-sheet-enter:${SHEET_MOTION.enterMs}ms;--m-dur-sheet-enter:${SHEET_MOTION.enterMs}ms;`
  + `--m-ease-sheet:${SHEET_MOTION.enterEase};--m-sheet-scrim-enter:${SHEET_MOTION.scrimEnterMs}ms;--m-ease-sheet-scrim:${SHEET_MOTION.scrimEnterEase};`
  + `--m-sheet-exit:${SHEET_MOTION.exitMs}ms;--m-dur-sheet-exit:${SHEET_MOTION.exitMs}ms;--m-ease-sheet-exit:${SHEET_MOTION.exitEase};`
  + `--m-dur-sheet-drag-min:${SHEET_MOTION.dragMinMs}ms}`
  + `@media (prefers-reduced-motion: reduce){:root{--m-sheet-enter:${MOTION.dur.fast}ms;--m-dur-sheet-enter:${MOTION.dur.fast}ms;--m-sheet-exit:${MOTION.dur.fast}ms;--m-dur-sheet-exit:${MOTION.dur.fast}ms}}`;
if (typeof document !== 'undefined' && !document.getElementById('luma-sheet-motion')) {
  const el = document.createElement('style'); el.id = 'luma-sheet-motion'; el.textContent = sheetMotionCss();
  document.head.appendChild(el);
}
