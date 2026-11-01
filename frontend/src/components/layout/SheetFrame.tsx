/** The phone bottom sheet frame (MOTION.md §1): scrim + card portaled at --z-sheet.
 *
 * - Enter: the card mounts at its final layout, translateY(100%), opaque, and slides to 0 over
 *   MOTION.dur.sheetEnter / MOTION.ease.sheet; the scrim ramps 0 -> MOTION.scrim over MOTION.dur.sheetScrimEnter /
 *   MOTION.ease.sheetScrimEnter, both from the same first frame (WAAPI, no transition-less mount, no opacity gate).
 * - Exit: from wherever the card is now (mid-enter or mid-drag) to translateY(100%) over
 *   MOTION.dur.sheetExit / MOTION.ease.sheetExit; the scrim with MOTION.dur.sheetScrimExit / ease.sheetScrimExit.
 *   All of these are SHEET_MOTION in theme/motion.ts (the one place to change them). Re-entering cancels the exit.
 * - Reduced motion: opacity only, --m-dur-fast.
 * - Body scroll lock and the <html> background (#111318) from enter start to unmount.
 * `data-composed` stays on the root for the composed_ms hooks; it no longer gates visibility.
 */
import { createContext, useCallback, useContext, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { APP_BG, MOTION, reducedMotion } from '@/theme/motion';
import { BACKDROP_HEIGHT } from '@/hooks/useBackdropEffects';
import { budgetLeft, SHEET_MOVING_EVENT, tapShell } from '@/services/modalPrefetch';
import type { UltraBlurColors } from '@/utils/modalParsers';

export type SheetPhase = 'entering' | 'visible' | 'exiting' | 'gone';

const CardRef = createContext<React.RefObject<HTMLDivElement | null> | null>(null);
/** True while the enter or the exit animation runs (MOTION.md §6: no detail render on the animating frames). */
const Entering = createContext(false);
export const useSheetEntering = () => useContext(Entering);

/** Duration the owner should wait after phase 'exiting' before unmounting. */
export const SHEET_EXIT_MS = MOTION.dur.sheetExit;
/** Enter fallback: the sheet counts as entered this long after the enter duration even without `finished`. */
export const ENTER_FALLBACK_SLACK_MS = 200;

/** One close per sheet: Back (luma:modal-dismiss) and the close button can both fire within
 *  MOTION.dur.dismissExit; only the first runs, so a second history step is never taken. */
export function useCloseOnce(): () => boolean {
  const at = useRef(-Infinity);
  return useCallback(() => {
    const t = performance.now();
    if (t - at.current < MOTION.dur.dismissExit) return false;
    at.current = t;
    return true;
  }, []);
}

function currentY(el: HTMLElement): number {
  const t = getComputedStyle(el).transform;
  if (!t || t === 'none') return 0;
  const m = t.match(/matrix(3d)?\(([^)]+)\)/);
  if (!m) return 0;
  const v = m[2]!.split(',').map(Number);
  return m[1] ? v[13]! : v[5]!;
}

/** When the phone sheet may start its slide: once the details are composed, or when the tap's open budget
 *  (OPEN_BUDGET_MS = 150 ms from the tap, MOTION.md §1.1) is spent, whichever is first. Past the budget the sheet
 *  opens on the seeded hero (useSeededHero). State, not derived: the render that flips it holds those children. */
export function useSheetGo(composed: boolean): boolean {
  const [go, setGo] = useState(() => composed || budgetLeft() === 0);
  useLayoutEffect(() => {
    if (go) return;
    if (composed) { setGo(true); return; }
    const t = window.setTimeout(() => setGo(true), budgetLeft());
    return () => window.clearTimeout(t);
  }, [composed, go]);
  return go;
}

/** The phone hero when the sheet has to start before the details are composed (Designer 10:16 PM): built once,
 *  at the slide's start, from what the tapped card already had (its poster art, its poster-seeded colors, its
 *  title) and kept for the sheet's life, so the art, wash, body tint and title never restyle; only the detail
 *  boxes fade in when the data lands. `seeded` false: the composed details drive the hero as before. */
export function useSeededHero(composed: boolean, colors: UltraBlurColors | null) {
  const go = useSheetGo(composed);
  const tap = useRef(tapShell()).current;
  const seeded = useRef<boolean | null>(null);
  if (go && seeded.current === null) seeded.current = !composed && !!tap;
  const frozen = useRef<{ colors: UltraBlurColors | null } | null>(null);
  if (seeded.current && !frozen.current) frozen.current = { colors };
  const on = seeded.current === true;
  return { go, seeded: on, art: on ? tap!.poster : null, title: on ? tap!.title : '', colors: on ? frozen.current!.colors : colors };
}

/** Seeded hero: the wash box has one fixed height in the seed and in the details (its blobs scale with the box),
 *  so both draw the same pixels; it fades out at the bottom instead of ending at the content's edge. */
export const SEEDED_WASH: CSSProperties = {
  bottom: 'auto', height: '100vh',
  WebkitMaskImage: 'linear-gradient(to bottom, transparent 0%, black 80px, black calc(100% - 160px), transparent 100%)',
  maskImage: 'linear-gradient(to bottom, transparent 0%, black 80px, black calc(100% - 160px), transparent 100%)',
};

export function SheetFrame({ name, variant = 'mobile', phase, composed, go, onScrimClick, children }: {
  name: string; variant?: string; phase: SheetPhase; composed: boolean; go: boolean;
  onScrimClick?: () => void; children: ReactNode;
}) {
  const cardRef = useRef<HTMLDivElement>(null);
  const scrimRef = useRef<HTMLDivElement>(null);
  const anims = useRef<Animation[]>([]);
  const entered = useRef(false);
  const prevPhase = useRef<SheetPhase>(phase);
  const [entering, setEntering] = useState(true);
  // Unmount: nothing left running on a detached card (Reviewer: the prime's rAF; the prime is gone, the enter is
  // started synchronously, so cancel whatever animations are still live).
  useLayoutEffect(() => () => { anims.current.forEach((a) => a.cancel()); anims.current = []; }, []);

  // Scroll lock + app background paint, from enter start until unmount.
  useLayoutEffect(() => {
    const prevOverflow = document.body.style.overflow;
    const h = document.documentElement;
    const n = Number(h.dataset.lumaSheet || 0) + 1;   // index.css: html[data-luma-sheet] paints #111318
    document.body.style.overflow = 'hidden';
    h.dataset.lumaSheet = String(n);
    return () => {
      document.body.style.overflow = prevOverflow;
      const left = Number(h.dataset.lumaSheet || 1) - 1;
      if (left > 0) h.dataset.lumaSheet = String(left); else delete h.dataset.lumaSheet;
    };
  }, []);

  useLayoutEffect(() => {
    const card = cardRef.current;
    const scrim = scrimRef.current;
    if (!card || !scrim || typeof card.animate !== 'function') { setEntering(false); return; }
    // Held off-screen until it can show composed: no frame is drawn before the slide.
    // Closed during the hold and the details landed later: it never entered, so there is no exit to play.
    if ((!go || phase === 'exiting') && !entered.current) {
      // The scrim stays hit-testable (opacity 0): a tap on it closes the sheet during the hold (Reviewer).
      card.style.visibility = 'hidden'; scrim.style.opacity = '0';
      prevPhase.current = phase;
      return;
    }
    card.style.visibility = '';
    const rm = reducedMotion();
    const stop = () => { anims.current.forEach((a) => a.cancel()); anims.current = []; };
    if (phase === 'exiting') {
      prevPhase.current = phase;
      const y = currentY(card);
      const h = card.offsetHeight || window.innerHeight;
      stop();
      // Compositor-only exit: transform/opacity on promoted layers, nothing that re-rasters mid-exit.
      card.style.willChange = rm ? 'opacity' : 'transform';
      scrim.style.willChange = 'opacity';
      const exitMs = rm ? MOTION.dur.fast : Math.max(MOTION.dur.sheetDragMin, MOTION.dur.sheetExit * Math.max(0, h - y) / h);
      anims.current = [
        rm
          ? card.animate([{ opacity: 1 }, { opacity: 0 }], { duration: exitMs, easing: MOTION.ease.standard, fill: 'forwards' })
          : card.animate([{ transform: `translate3d(0, ${y}px, 0)` }, { transform: 'translate3d(0, 100%, 0)' }],
            { duration: exitMs, easing: MOTION.ease.sheetExit, fill: 'forwards' }),
        scrim.animate([{ opacity: getComputedStyle(scrim).opacity }, { opacity: 0 }],
          { duration: rm ? exitMs : Math.round(exitMs * MOTION.dur.sheetScrimExit / MOTION.dur.sheetExit), easing: MOTION.ease.sheetScrimExit, fill: 'forwards' }),
      ];
      return;
    }
    const wasExiting = prevPhase.current === 'exiting';
    prevPhase.current = phase;
    if (entered.current) {
      // A re-open while exiting cancels the exit: back to rest from where the card is.
      if (wasExiting) {
        const y = currentY(card);
        stop();
        if (y > 0 && !rm) {
          anims.current = [card.animate([{ transform: `translate3d(0, ${y}px, 0)` }, { transform: 'translate3d(0, 0, 0)' }],
            { duration: MOTION.dur.sheetEnter, easing: MOTION.ease.sheet })];
        }
      }
      return;
    }
    entered.current = true;
    card.style.willChange = 'transform';
    // WAAPI from the first frame (a re-render can't reset it the way it resets inline styles).
    const enter = rm
      ? card.animate([{ opacity: 0 }, { opacity: 1 }], { duration: MOTION.dur.fast, easing: MOTION.ease.standard })
      : card.animate([{ transform: 'translate3d(0, 100%, 0)' }, { transform: 'translate3d(0, 0, 0)' }],
        { duration: MOTION.dur.sheetEnter, easing: MOTION.ease.sheet });
    const fade = scrim.animate([{ opacity: 0 }, { opacity: MOTION.scrim }],
      { duration: rm ? MOTION.dur.fast : MOTION.dur.sheetScrimEnter, easing: rm ? MOTION.ease.standard : MOTION.ease.sheetScrimEnter });
    anims.current = [enter, fade];
    // No primed frame (the 150 ms tap-to-motion budget, Designer 10:16 PM): the enter plays from the first painted frame.
    const start = () => {
    window.dispatchEvent(new Event(SHEET_MOVING_EVENT));   // releases the tapped card's press
    anims.current = [enter, fade];
    // The held details commit when the slide ends, then fade in place (Designer 7:30 PM: the fade must not drop
    // slide frames; a commit at 60% of the slide dropped 5-9 frames at 1x/4x, sheetmeasure.py).
    let done = false;
    const finish = () => {
      if (done) return; done = true;
      window.clearTimeout(fallback);
      setEntering(false);
      card.style.willChange = '';
      anims.current = anims.current.filter((a) => a !== enter && a !== fade);
      card.dispatchEvent(new CustomEvent('luma:sheet-entered', { bubbles: true }));
    };
    // A finish that never resolves (throttled tab, missed animation event) can't hang the sheet.
    const fallback = window.setTimeout(finish, (rm ? MOTION.dur.fast : MOTION.dur.sheetEnter) + ENTER_FALLBACK_SLACK_MS);
    enter.finished.then(finish, () => { window.clearTimeout(fallback); setEntering(false); });
    };
    start();
  }, [phase, go]);

  if (phase === 'gone') return null;
  return createPortal(
    <div className="luma-fullscreen luma-bleed"
      data-modal={name}
      data-variant={variant}
      data-composed={composed ? 'true' : 'false'}
      data-phase={phase}
      style={{ position: 'fixed', inset: 0, zIndex: MOTION.z.sheet }}
    >
      <div ref={scrimRef} data-sheet-scrim="" aria-hidden="true"
        onClick={onScrimClick}
        style={{ position: 'absolute', inset: 0, background: '#000', opacity: MOTION.scrim }} />
      <Entering.Provider value={(go && entering) || phase === 'exiting'}><CardRef.Provider value={cardRef}>{children}</CardRef.Provider></Entering.Provider>
    </div>,
    document.body,
  );
}

/** The sheet card: rounded top, full height under the safe area, slides with the frame. */
export function SheetCard({ background, children }: { background: string; children: ReactNode }) {
  const ref = useContext(CardRef);
  const entering = useContext(Entering);
  // While the card slides in, keep the first frame's content (same element identity, so React skips the
  // subtree): late detail/ratings/cast commit once, at enter end, instead of on the animating frames.
  const snap = useRef<ReactNode>(children);
  if (!entering) snap.current = children;
  const style: CSSProperties = {
    position: 'absolute',
    top: 'max(env(safe-area-inset-top, 0px), 6px)',
    left: 0, right: 0, bottom: 0,
    borderTopLeftRadius: '14px', borderTopRightRadius: '14px',
    overflow: 'hidden',
    backgroundColor: background,
    display: 'flex', flexDirection: 'column',
    transform: 'translate3d(0, 0, 0)',
    backfaceVisibility: 'hidden',
  };
  return <div ref={ref} className="luma-bleed-down" data-sheet-card="" style={style}>{snap.current}</div>;
}

/** The sheet's first frame while details load (MOTION.md §1.2-1.3): the final layout's top (backdrop box,
 *  title block, reserved rows) filled from what the tapped card showed; the details crossfade in over it. */
export interface SeedHero { art: ReactNode; wash: ReactNode; title: ReactNode }
export function SeedShell({ overlap, rows, hero }: { overlap: number; rows?: ReactNode; hero?: SeedHero }) {
  const seed = tapShell();
  // Seeded hero: the layout's own BackdropHero, wash and title block with the frozen seed inputs (same pixels
  // as the details' hero underneath); only the reserved rows differ (invisible here).
  if (hero) return (
    <div data-seed-shell="" data-seed-hero="" aria-busy="true">
      {hero.art}
      <div style={{ position: 'relative', zIndex: 3, marginTop: -overlap, textAlign: 'center' }}>
        {hero.wash}{hero.title}
        {/* ReservedRows' 24px top margin collapses with the title block's 24px bottom margin, as in the details. */}
        <div style={{ padding: '0 16px' }}>{rows}</div>
      </div>
    </div>
  );
  return (
    <div data-seed-shell="" aria-busy="true">
      <div style={{ position: 'relative', height: BACKDROP_HEIGHT, overflow: 'hidden', background: APP_BG }}>
        {seed?.poster && (
          <img src={seed.poster} alt="" decoding="sync" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover' }} />
        )}
        <div style={{ position: 'absolute', inset: 0, background: `linear-gradient(to bottom, transparent 40%, ${APP_BG})` }} />
      </div>
      <div style={{ position: 'relative', marginTop: -overlap, textAlign: 'center', padding: '0 16px' }}>
        <div style={{ height: 140, display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }}>
          <h1 style={{ fontSize: 28, fontWeight: 700, color: '#fff', lineHeight: 1.2, margin: 0, textShadow: '0 2px 8px rgba(0,0,0,0.5)' }}>
            {seed?.title ?? ''}
          </h1>
        </div>
        {rows}
      </div>
    </div>
  );
}

/** The seed stays on top (hero + title, nothing else drawn) until the details can show composed (title
 *  revealed, hero image ready), then fades out (CSS animation, so it starts on the frame the details
 *  actually commit, also when SheetCard held them during the slide) while the details fade in at their
 *  final boxes underneath (SHELL_CROSSFADE): no placeholder bars, never a blank hero. Removed on animationend. */
export function SeedLayer({ show, ready, overlap, rows, hero }: { show: boolean; ready: boolean; overlap: number; rows?: ReactNode; hero?: SeedHero }) {
  const [done, setDone] = useState(false);
  useLayoutEffect(() => { if (!ready) setDone(false); }, [ready]);
  // animationend can be skipped (animations off, hidden tab): drop the seed after the fade's length + slack anyway.
  useLayoutEffect(() => {
    if (!show || !ready || done) return;
    const t = window.setTimeout(() => setDone(true), MOTION.dur.crossfade + SEED_FALLBACK_SLACK_MS);
    return () => window.clearTimeout(t);
  }, [show, ready, done]);
  // A seeded hero leaves at once: the details' hero underneath is pixel-identical; their boxes fade in themselves.
  if (!show || done || (hero && ready)) return null;
  return (
    <div data-seed-layer={ready ? 'fading' : 'on'} aria-hidden="true"
      onAnimationEnd={(e) => { if (e.target === e.currentTarget && ready) setDone(true); }}
      style={{ position: 'absolute', top: 0, left: 0, right: 0, zIndex: 5, pointerEvents: 'none',
        animation: ready ? SEED_FADE_OUT : 'none' }}>
      <SeedShell overlap={overlap} rows={rows} hero={hero} />
    </div>
  );
}
export const SEED_FALLBACK_SLACK_MS = 200;
export const SEED_FADE_OUT = 'm-crossfade var(--m-dur-crossfade) var(--m-ease-standard) reverse both';

/** Content that replaces a seed shell crossfades in (--m-dur-crossfade); content there from the first frame doesn't. */
export const SHELL_CROSSFADE = 'm-crossfade var(--m-dur-crossfade) var(--m-ease-standard) both';

/** Desktop modal / side-sheet motion (MOTION.md §1 table, right column), shared by the ExpandedLayouts,
 *  PersonDetail, PageModal and KioskSearch. Enter on mount: scrim 0 -> 1 over --m-dur-standard, card
 *  scale(0.96)+opacity 0 -> rest over 320ms --m-ease-decel (side: translateX(24px)+fade). Exit when `phase`
 *  becomes 'exiting': from the current state to scale(0.98)+opacity 0 over 200ms --m-ease-accel; the scrim
 *  fades over 200ms. Reduced motion: opacity only, --m-dur-fast. WAAPI, transform/opacity only. */
export const MODAL_ENTER_MS = 320;
export const MODAL_EXIT_MS = 200;
export function useModalMotion(phase: string, scrimRef: React.RefObject<HTMLElement | null>,
  cardRef: React.RefObject<HTMLElement | null>, kind: 'modal' | 'side' | 'bottom' = 'modal') {
  const anims = useRef<Animation[]>([]);
  const entered = useRef(false);
  const [entering, setEntering] = useState(true);
  // html[data-luma-modal] while a modal is up: surfaces under it drop their backdrop blur (index.css /
  // the palette css), so the enter/exit stay compositor-only instead of re-blurring every frame.
  useLayoutEffect(() => {
    const h = document.documentElement;
    h.dataset.lumaModal = String(Number(h.dataset.lumaModal || 0) + 1);
    return () => { const n = Number(h.dataset.lumaModal || 1) - 1; if (n > 0) h.dataset.lumaModal = String(n); else delete h.dataset.lumaModal; };
  }, []);
  useLayoutEffect(() => {
    const card = cardRef.current; const scrim = scrimRef.current;
    const ok = (el: HTMLElement | null): el is HTMLElement => !!el && typeof el.animate === 'function';
    card?.setAttribute('data-modal-card', kind);
    const rm = reducedMotion();
    const stop = () => { anims.current.forEach((a) => a.cancel()); anims.current = []; };
    // 'bottom': the phone sheet rule (opaque card slides from translateY(100%); --m-dur-sheet-enter/exit).
    const bottom = kind === 'bottom';
    const from = kind === 'side' ? 'translate3d(24px, 0, 0)' : bottom ? 'translate3d(0, 100%, 0)' : 'scale(0.96)';
    const out = kind === 'side' ? 'translate3d(24px, 0, 0)' : bottom ? 'translate3d(0, 100%, 0)' : 'scale(0.98)';
    const o0 = bottom ? 1 : 0;
    if (phase === 'exiting') {
      const cs = ok(card) ? getComputedStyle(card) : null;
      const curT = cs && cs.transform !== 'none' ? cs.transform : 'none';
      const curO = cs ? cs.opacity : '1';
      const so = ok(scrim) ? getComputedStyle(scrim).opacity : '1';
      stop();
      const d = rm ? MOTION.dur.fast : bottom ? MOTION.dur.sheetExit : MODAL_EXIT_MS;
      if (ok(card)) anims.current.push(card.animate(rm ? [{ opacity: curO }, { opacity: 0 }]
        : [{ transform: curT, opacity: curO }, { transform: out, opacity: o0 }], { duration: d, easing: bottom ? MOTION.ease.sheetExit : MOTION.ease.accel, fill: 'forwards' }));
      if (ok(scrim) && scrim !== card) anims.current.push(scrim.animate([{ opacity: so }, { opacity: 0 }], { duration: rm || !bottom ? d : MOTION.dur.sheetScrimExit, easing: bottom ? MOTION.ease.sheetScrimExit : MOTION.ease.accel, fill: 'forwards' }));
      return;
    }
    if (entered.current) {
      if (anims.current.length) stop(); // a re-open during exit cancels the exit (back to rest)
      return;
    }
    entered.current = true;
    if (ok(scrim) && scrim !== card) anims.current.push(scrim.animate([{ opacity: 0 }, { opacity: 1 }],
      { duration: rm ? MOTION.dur.fast : bottom ? MOTION.dur.sheetScrimEnter : MOTION.dur.standard, easing: bottom && !rm ? MOTION.ease.sheetScrimEnter : MOTION.ease.standard }));
    if (ok(card)) anims.current.push(card.animate(rm ? [{ opacity: 0 }, { opacity: 1 }]
      : [{ transform: from, opacity: o0 }, { transform: 'none', opacity: 1 }],
      { duration: rm ? MOTION.dur.fast : bottom ? MOTION.dur.sheetEnter : MODAL_ENTER_MS, easing: bottom ? MOTION.ease.sheet : MOTION.ease.decel }));
    const enter = anims.current[anims.current.length - 1];
    if (!enter) { setEntering(false); return; }
    let done = false;
    const finish = () => {
      if (done) return; done = true; window.clearTimeout(fallback);
      setEntering(false);
      anims.current = anims.current.filter((x) => x !== enter);
      card?.dispatchEvent(new CustomEvent('luma:sheet-entered', { bubbles: true }));
    };
    const fallback = window.setTimeout(finish, (rm ? MOTION.dur.fast : bottom ? MOTION.dur.sheetEnter : MODAL_ENTER_MS) + ENTER_FALLBACK_SLACK_MS);
    enter.finished.then(finish, () => { window.clearTimeout(fallback); setEntering(false); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase === 'exiting']);
  return entering;
}

/** Holds the first committed children while `hold` is true (same element identity, so React skips the
 *  subtree): late detail/ratings/images commit once the enter animation ends, never on its frames. */
export function Hold({ hold, children }: { hold: boolean; children: ReactNode }) {
  const snap = useRef<ReactNode>(children);
  if (!hold) snap.current = children;
  return <>{snap.current}</>;
}
