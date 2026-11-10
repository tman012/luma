/** Motion tokens (MOTION.md §0): the one place durations and easings are defined. The sheet, modal, reveal and state
 *  tokens' CSS twins are written from here (motionCss); the older press/standard ones live in index.css :root as --m-*. */
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
/** Desktop and tablet modal / side-panel motion (the ExpandedLayouts, PersonDetail, PageModal, KioskSearch): scale 0.96 and
 *  opacity in with a decelerate, a lighter accelerate out. Transform and opacity only, no overshoot. */
export const MODAL_MOTION = {
  enterMs: 320, enterEase: 'cubic-bezier(0.05,0.7,0.1,1)',
  exitMs: 200, exitEase: 'cubic-bezier(0.3,0,0.8,0.15)',
  enterScale: 0.96, exitScale: 0.98, sideShift: 24,
} as const;
/** Content and artwork that arrive after a surface is already open fade in over this (one reveal, opacity only). */
export const REVEAL_MOTION = { ms: 320, ease: 'cubic-bezier(0.22,0.61,0.36,1)' } as const;
/** Pressed, hover, focus and selected feedback (index.css .m-press / .m-hover / .m-focus / .m-selected read the CSS twins). */
export const STATE_MOTION = { pressMs: 100, hoverMs: 160, selectedMs: 160, pressScale: 0.98 } as const;
const SHEET_ENTER = SHEET_MOTION.enterMs, SHEET_EXIT = SHEET_MOTION.exitMs, MODAL_EXIT_DESK = 300;
export const MOTION = {
  dur: { instant: 0, spin: 800, fast: 120, standard: 240, emphasized: 360, sheetEnter: SHEET_ENTER, sheetExit: SHEET_EXIT, sheetDragMin: SHEET_MOTION.dragMinMs,
    sheetScrimEnter: SHEET_MOTION.scrimEnterMs, sheetScrimExit: SHEET_MOTION.scrimExitMs,
    press: STATE_MOTION.pressMs, image: 240, crossfade: 200,
    hover: STATE_MOTION.hoverMs, selected: STATE_MOTION.selectedMs,
    modalEnter: MODAL_MOTION.enterMs, modalExit: MODAL_MOTION.exitMs,
    /** Reveal of content and artwork into an open surface (REVEAL_MOTION). */
    reveal: REVEAL_MOTION.ms,
    /** The ambient color wash crossfading from the tap seed to the fetched palette. */
    palette: 600,
    /** Safety margin for timers that stand in for a missed animation event (never a visible delay). */
    settleSlack: 200,
    /** Sheet content (TV Calendar, Activity, Rooms): one fade of the whole block once its data is ready; row images fade on their own. */
    contentFade: 240, rowImage: 200, backdropSwap: 200,
    /** Back (popstate) waits for the longest exit: the phone sheet's, or the desktop expanded modal's owner timer. */
    dismissExit: Math.max(SHEET_EXIT, MODAL_EXIT_DESK) },
  ease: {
    standard: 'cubic-bezier(0.2,0,0,1)',
    decel: 'cubic-bezier(0.05,0.7,0.1,1)',
    accel: 'cubic-bezier(0.3,0,0.8,0.15)',
    spring: SPRING,
    contentFade: 'cubic-bezier(0.4,0,0.2,1)',
    sheet: SHEET_MOTION.enterEase,
    sheetExit: SHEET_MOTION.exitEase,
    sheetScrimEnter: SHEET_MOTION.scrimEnterEase,
    sheetScrimExit: SHEET_MOTION.scrimExitEase,
    reveal: REVEAL_MOTION.ease,
    modalEnter: MODAL_MOTION.enterEase,
    modalExit: MODAL_MOTION.exitEase,
  },
  spring: { stiffness: 380, damping: 36, mass: 1 },
  rise: 6, routeShift: 8, stagger: 30, staggerMax: 240, pressScale: STATE_MOTION.pressScale, scrim: 0.6,
  /** The lighter scrim of the Home sheets (Storage, Activity, TV Calendar, Rooms): their own alpha, animated on opacity only. */
  scrimLight: 0.4,
  z: { page: 0, nav: 100, overlay: 9000, sheet: 10000, sheetNested: 10050, toast: 10080, system: 10100 },
} as const;

/** Startup splash. Entrance: the L ribbon mark scales and fades in, then "uma" reveals beside it. The splash then holds
 *  (background never moves) until Home is ready and the entrance has finished. Handoff: the centered logo itself scales
 *  and translates into Home's logo while Home fades in around it (opacity and a few px of rise). index.html paints the
 *  entrance before any script runs, so its CSS repeats these values as literals (tests/startupSplash363.test.tsx keeps
 *  the two in step). */
export const SPLASH_MOTION = {
  markMs: 520, markEase: MOTION.ease.decel, markScale: 0.72,
  umaDelayMs: 340, umaMs: 560, umaEase: MOTION.ease.reveal, umaShift: 5, umaClipFrom: 41,
  /** The entrance's length: the minimum the splash stays up (the last of "uma" lands here). */
  introMs: 340 + 560,
  handoffMs: 640, handoffEase: MOTION.ease.contentFade,
  /** Home's own fade and rise during the handoff, started with the logo's flight and never longer than it. */
  homeFadeMs: 520, homeEase: MOTION.ease.contentFade, homeRisePx: MOTION.rise,
  /** Reduced motion: the entrance is a short fade and the handoff one crossfade, nothing scales, travels or rises. */
  reducedIntroMs: MOTION.dur.fast, reducedHandoffMs: MOTION.dur.crossfade,
  /** Hard cap from the start of the page load: Home is shown with whatever has loaded. */
  capMs: 7000,
  /** The logo's width on the splash and on the screen that stands in for Home (same box, so nothing jumps). */
  logoWidth: 'clamp(180px, 46vw, 340px)',
  /** The splash and launch background: Home's own background, the manifest's background_color and theme_color. */
  bg: '#15102a',
} as const;

/** The app background behind every screen and sheet (MOTION.md §7 check 12). */
export const APP_BG = '#111318';

export const reducedMotion = (): boolean =>
  typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** `transition` for opacity that reveals content or artwork into an open surface (the CSS twins follow reduced motion). */
export const REVEAL_TRANSITION = 'opacity var(--m-dur-reveal) var(--m-ease-reveal)';

/** `transition` for opacity that follows a state change on an open surface (content hidden behind a shelf, a toast). */
export const SURFACE_FADE = 'opacity var(--m-dur-standard) var(--m-ease-standard)';

/** How long a surface plays its exit before its owner unmounts it: the phone sheet (SHEET_MOTION), or the desktop and
 *  tablet modal (MODAL_MOTION). One place, so an owner's timer always equals the animation it waits for. */
export type SurfaceKind = 'sheet' | 'modal';
export const exitMs = (kind: SurfaceKind): number => (kind === 'sheet' ? MOTION.dur.sheetExit : MOTION.dur.modalExit);

/** The CSS twins of the motion tokens above (--m-sheet-enter, --m-ease-sheet, --m-dur-reveal, ...), written from the
 *  tokens so the values live in one place. Reduced motion shortens every move to a quick opacity change. */
export const motionCss = (): string => `:root{--m-sheet-enter:${SHEET_MOTION.enterMs}ms;--m-dur-sheet-enter:${SHEET_MOTION.enterMs}ms;`
  + `--m-ease-sheet:${SHEET_MOTION.enterEase};--m-sheet-scrim-enter:${SHEET_MOTION.scrimEnterMs}ms;--m-ease-sheet-scrim:${SHEET_MOTION.scrimEnterEase};`
  + `--m-sheet-exit:${SHEET_MOTION.exitMs}ms;--m-dur-sheet-exit:${SHEET_MOTION.exitMs}ms;--m-ease-sheet-exit:${SHEET_MOTION.exitEase};`
  + `--m-dur-sheet-drag-min:${SHEET_MOTION.dragMinMs}ms;`
  + `--m-dur-modal-enter:${MODAL_MOTION.enterMs}ms;--m-dur-modal-exit:${MODAL_MOTION.exitMs}ms;`
  + `--m-ease-modal-enter:${MODAL_MOTION.enterEase};--m-ease-modal-exit:${MODAL_MOTION.exitEase};`
  + `--m-dur-reveal:${REVEAL_MOTION.ms}ms;--m-ease-reveal:${REVEAL_MOTION.ease};`
  + `--m-dur-hover:${STATE_MOTION.hoverMs}ms;--m-dur-selected:${STATE_MOTION.selectedMs}ms}`
  + `@media (prefers-reduced-motion: reduce){:root{--m-sheet-enter:${MOTION.dur.fast}ms;--m-dur-sheet-enter:${MOTION.dur.fast}ms;--m-sheet-exit:${MOTION.dur.fast}ms;--m-dur-sheet-exit:${MOTION.dur.fast}ms;`
  + `--m-dur-modal-enter:${MOTION.dur.fast}ms;--m-dur-modal-exit:${MOTION.dur.fast}ms;--m-dur-reveal:${MOTION.dur.fast}ms;--m-dur-hover:0ms;--m-dur-selected:${MOTION.dur.fast}ms}}`;
export const sheetMotionCss = motionCss;
if (typeof document !== 'undefined' && !document.getElementById('luma-sheet-motion')) {
  const el = document.createElement('style'); el.id = 'luma-sheet-motion'; el.textContent = motionCss();
  document.head.appendChild(el);
}

/** Delay (ms) of the item at `index` in a staggered entrance: MOTION.stagger steps, never more than MOTION.staggerMax. */
export const staggerMs = (index: number): number => Math.min(Math.max(index, 0) * MOTION.stagger, MOTION.staggerMax);
