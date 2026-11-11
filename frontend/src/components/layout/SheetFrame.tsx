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
import { createContext, useContext, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { APP_BG, MODAL_MOTION, MOTION, REVEAL_TRANSITION, SHEET_NEUTRAL, reducedMotion } from '@/theme/motion';
import { useCloseOnce } from '@/components/motion/useSurfacePresence';
import { BACKDROP_HEIGHT, type BackdropEffects } from '@/hooks/useBackdropEffects';
import { SHEET_MOVING_EVENT, tapShell } from '@/services/modalPrefetch';
import type { UltraBlurColors } from '@/utils/modalParsers';

export type SheetPhase = 'entering' | 'visible' | 'exiting' | 'gone';

const CardRef = createContext<React.RefObject<HTMLDivElement | null> | null>(null);
/** True while the enter or the exit animation runs (MOTION.md §6: no detail render on the animating frames). */
const Entering = createContext(false);
export const useSheetEntering = () => useContext(Entering);

/** Duration the owner should wait after phase 'exiting' before unmounting. */
export const SHEET_EXIT_MS = MOTION.dur.sheetExit;
/** Enter fallback: the sheet counts as entered this long after the enter duration even without `finished`. */
export const ENTER_FALLBACK_SLACK_MS = MOTION.dur.settleSlack;
export { useCloseOnce };

function currentY(el: HTMLElement): number {
  const t = getComputedStyle(el).transform;
  if (!t || t === 'none') return 0;
  const m = t.match(/matrix(3d)?\(([^)]+)\)/);
  if (!m) return 0;
  const v = m[2]!.split(',').map(Number);
  return m[1] ? v[13]! : v[5]!;
}

/** The phone sheet opens on the tap, never held for the network: this is its hero while the details are not composed
 *  yet (Designer 10:16 PM): built once,
 *  at the slide's start, from what the tapped card already had (its poster art, its poster-seeded colors, its
 *  title) and kept for the sheet's life, so the art, wash, body tint and title never restyle; only the detail
 *  boxes fade in when the data lands. `seeded` false: the composed details drive the hero as before. */
export function useSeededHero(composed: boolean, colors: UltraBlurColors | null) {
  const tap = useRef(tapShell()).current;
  const seeded = useRef<boolean | null>(null);
  if (seeded.current === null) seeded.current = !composed && !!tap;
  const on = seeded.current === true;
  return { go: true, seeded: on, art: on ? tap!.poster : null, title: on ? tap!.title : '', colors };
}

/** When the sheet's palette arrived: null while it is not here, -1 when it was here at the sheet's first render (it
 *  shows at once, nothing fades), else the time it landed. The blur and the sheet's tint both start their one fade
 *  from it, so a wash that mounts later (the details under the seed) continues the same fade. */
export function useSheetPaletteAt(colors: UltraBlurColors | null): number | null {
  const first = useRef<boolean | null>(null);
  if (first.current === null) first.current = !!colors;
  const at = useRef<number | null>(null);
  if (colors && at.current === null) at.current = first.current ? -1 : performance.now();
  return at.current;
}

/** The sheet's palette blur fades in once, from the neutral sheet color, on the sheet itself (opacity only): a negative
 *  delay resumes the fade at its progress when this wrapper mounts after the palette landed. A palette that was there
 *  from the first render, or whose fade has run, draws at full opacity. */
export function PaletteFade({ colors, at, children }: { colors: UltraBlurColors | null; at: number | null; children: ReactNode }) {
  return colors ? <PaletteFadeIn at={at}>{children}</PaletteFadeIn> : null;
}
function PaletteFadeIn({ at, children }: { at: number | null; children: ReactNode }) {
  const age = useRef(at === null || at < 0 ? null : Math.round(performance.now() - at)).current;
  const fading = age !== null && age < MOTION.dur.reveal && !reducedMotion();
  return (
    <div data-palette-fade={fading ? '' : undefined}
      style={{ position: 'absolute', inset: 0, pointerEvents: 'none',
        ...(fading ? { animation: 'm-crossfade var(--m-dur-reveal) var(--m-ease-reveal) both', animationDelay: `${-age}ms` } : null) }}>
      {children}
    </div>
  );
}

/** Seeded hero: the wash box has one fixed height in the seed and in the details (its blobs scale with the box),
 *  so both draw the same pixels; it fades out at the bottom instead of ending at the content's edge. */
export const SEEDED_WASH: CSSProperties = {
  bottom: 'auto', height: '100vh',
  WebkitMaskImage: 'linear-gradient(to bottom, transparent 0%, black 80px, black calc(100% - 160px), transparent 100%)',
  maskImage: 'linear-gradient(to bottom, transparent 0%, black 80px, black calc(100% - 160px), transparent 100%)',
};

export function SheetFrame({ name, variant = 'mobile', phase, composed, go = true, onScrimClick, children }: {
  name: string; variant?: string; phase: SheetPhase; composed: boolean; go?: boolean;
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

  // Scroll lock + app background paint, from the frame the sheet is actually shown until unmount.
  useLayoutEffect(() => {
    if (!go) return;
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
  }, [go]);

  useLayoutEffect(() => {
    if (!go) {
      anims.current.forEach((a) => a.cancel());
      anims.current = [];
      setEntering(true);
      return;
    }
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
    setEntering(true);
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

  // Closed until the slide may start, and closed again if readiness drops (a reload).
  // `entered` must reset or the next open skips its slide and a held reload stays on screen.
  if (!go) entered.current = false;
  if (phase === 'gone' || !go) return null;
  return createPortal(
    <div className="luma-fullscreen luma-bleed"
      data-modal={name}
      data-variant={variant}
      data-composed={composed ? 'true' : 'false'}
      data-phase={phase}
      style={{ position: 'fixed', inset: 0, zIndex: MOTION.z.sheet, pointerEvents: phase === 'exiting' ? 'none' : undefined }}
    >
      <div ref={scrimRef} data-sheet-scrim="" aria-hidden="true"
        onClick={entering ? undefined : onScrimClick}
        style={{ position: 'absolute', inset: 0, background: '#000', opacity: MOTION.scrim }} />
      <Entering.Provider value={(go && entering) || phase === 'exiting'}><CardRef.Provider value={cardRef}>{children}</CardRef.Provider></Entering.Provider>
    </div>,
    document.body,
  );
}

export { SHEET_NEUTRAL };

/** The sheet card: rounded top, full height under the safe area, slides with the frame. */
export function SheetCard({ background, tint, hold = true, children }: { background: string; tint?: string | null; hold?: boolean; children: ReactNode }) {
  const ref = useContext(CardRef);
  const entering = useContext(Entering);
  // `hold`: while the card slides in, freeze the content as of the render that set it (same element identity, so
  // React skips the subtree on the slide's frames); what lands later commits once, at enter end. A sheet that opens
  // before its details exist passes hold={false} until its content has revealed, so seed, details and reveal all
  // commit the moment they are ready and fade in on the sliding card (transform and opacity only), and nothing after
  // that re-renders the shelves, cast and buttons on the animating frames.
  const snap = useRef<ReactNode>(children);
  const frozen = useRef(false);
  if (!entering || !hold) { snap.current = children; frozen.current = false; }
  else if (!frozen.current) { snap.current = children; frozen.current = true; }
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
  // The poster-derived base color is a layer that fades in (opacity, REVEAL token) when it arrives: the card's own
  // color never changes, so a palette landing after the open can't flip the sheet's color in one frame.
  return (
    <div ref={ref} className="luma-bleed-down" data-sheet-card="" style={style}>
      <div aria-hidden="true" data-sheet-tint="" style={{ position: 'absolute', inset: 0, pointerEvents: 'none',
        background: tint ?? 'transparent', opacity: tint ? 1 : 0, transition: REVEAL_TRANSITION }} />
      {snap.current}
    </div>
  );
}

/** The sheet's first frame while details load (MOTION.md §1.2-1.3): the final layout's top (backdrop box,
 *  title block, reserved rows) filled from what the tapped card showed; the details crossfade in over it. */
export interface SeedHero { art: ReactNode; wash: ReactNode; title: ReactNode;
  /** The seed's art is the tapped card's poster, not the details' own hero: it fades out over the details' reveal
   *  (SEED_HERO_FADE_OUT) instead of leaving at once. */
  fade?: boolean }

/** The tapped card's poster as the sliding sheet's hero from the slide's first frame (the card already decoded it, so
 *  it paints with the sheet, nothing fades in): the details' own hero box, mask and crop, so the sheet that rises
 *  already reads as the sheet it becomes. The details' backdrop fades in over it at the reveal. */
export function SeedArt({ src, effects }: { src: string; effects: BackdropEffects }) {
  return (
    <div data-seed-art="" style={{ position: 'sticky', top: 0, width: '100%', height: BACKDROP_HEIGHT, overflow: 'hidden', zIndex: 0,
      borderTopLeftRadius: '14px', borderTopRightRadius: '14px', background: APP_BG, ...effects.headerContainerMask }}>
      <img src={src} alt="" decoding="sync" draggable={false} style={{ ...effects.headerImgStyle, objectPosition: '50% 25%' }} />
      <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: '35%', pointerEvents: 'none',
        background: 'linear-gradient(to bottom, transparent 0%, rgba(0,0,0,0.25) 100%)' }} />
    </div>
  );
}
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
    const t = window.setTimeout(() => setDone(true), Math.max(MOTION.dur.crossfade, hero?.fade ? MOTION.dur.reveal : 0) + SEED_FALLBACK_SLACK_MS);
    return () => window.clearTimeout(t);
  }, [show, ready, done, hero?.fade]);
  // A seeded hero whose art is the details' own leaves at once: the details' hero underneath is pixel-identical; their boxes fade in themselves.
  if (!show || done || (hero && ready && !hero.fade)) return null;
  return (
    <div data-seed-layer={ready ? 'fading' : 'on'} aria-hidden="true"
      onAnimationEnd={(e) => { if (e.target === e.currentTarget && ready) setDone(true); }}
      style={{ position: 'absolute', top: 0, left: 0, right: 0, zIndex: 5, pointerEvents: 'none',
        animation: ready ? (hero?.fade ? SEED_HERO_FADE_OUT : SEED_FADE_OUT) : 'none' }}>
      <SeedShell overlap={overlap} rows={rows} hero={hero} />
    </div>
  );
}
export const SEED_FALLBACK_SLACK_MS = MOTION.dur.settleSlack;
export const SEED_FADE_OUT = 'm-crossfade var(--m-dur-crossfade) var(--m-ease-standard) reverse both';
/** A poster seed leaves on the details' own reveal fade (the backdrop and title fade in over it with the same duration and ease). */
export const SEED_HERO_FADE_OUT = 'm-crossfade var(--m-dur-reveal) var(--m-ease-reveal) reverse both';

/** Content that replaces a seed shell crossfades in (--m-dur-crossfade); content there from the first frame doesn't. */
export const SHELL_CROSSFADE = 'm-crossfade var(--m-dur-crossfade) var(--m-ease-standard) both';

/** Desktop modal / side-sheet motion (MOTION.md §1 table, right column), shared by the ExpandedLayouts,
 *  PersonDetail, PageModal and KioskSearch. Enter on mount: scrim 0 -> 1 over --m-dur-standard, card
 *  scale(0.96)+opacity 0 -> rest over 320ms --m-ease-decel (side: translateX(24px)+fade). Exit when `phase`
 *  becomes 'exiting': from the current state to scale(0.98)+opacity 0 over 200ms --m-ease-accel; the scrim
 *  fades over 200ms. Reduced motion: opacity only, --m-dur-fast. WAAPI, transform/opacity only. */
export const MODAL_ENTER_MS = MODAL_MOTION.enterMs;
export const MODAL_EXIT_MS = MODAL_MOTION.exitMs;
export function useModalMotion(phase: string, scrimRef: React.RefObject<HTMLElement | null>,
  cardRef: React.RefObject<HTMLElement | null>, kind: 'modal' | 'side' | 'bottom' = 'modal', armed = true) {
  const anims = useRef<Animation[]>([]);
  const entered = useRef(false);
  const [entering, setEntering] = useState(true);
  // html[data-luma-modal] while a modal is up: surfaces under it drop their backdrop blur (index.css /
  // the palette css), so the enter/exit stay compositor-only instead of re-blurring every frame.
  useLayoutEffect(() => {
    if (!armed) return;
    const h = document.documentElement;
    h.dataset.lumaModal = String(Number(h.dataset.lumaModal || 0) + 1);
    return () => { const n = Number(h.dataset.lumaModal || 1) - 1; if (n > 0) h.dataset.lumaModal = String(n); else delete h.dataset.lumaModal; };
  }, [armed]);
  useLayoutEffect(() => {
    if (!armed) return;
    const card = cardRef.current; const scrim = scrimRef.current;
    const ok = (el: HTMLElement | null): el is HTMLElement => !!el && typeof el.animate === 'function';
    card?.setAttribute('data-modal-card', kind);
    const rm = reducedMotion();
    const stop = () => { anims.current.forEach((a) => a.cancel()); anims.current = []; };
    // 'bottom': the phone sheet rule (opaque card slides from translateY(100%); --m-dur-sheet-enter/exit).
    const bottom = kind === 'bottom';
    const shift = `translate3d(${MODAL_MOTION.sideShift}px, 0, 0)`;
    const from = kind === 'side' ? shift : bottom ? 'translate3d(0, 100%, 0)' : `scale(${MODAL_MOTION.enterScale})`;
    const out = kind === 'side' ? shift : bottom ? 'translate3d(0, 100%, 0)' : `scale(${MODAL_MOTION.exitScale})`;
    const o0 = bottom ? 1 : 0;
    if (phase === 'exiting') {
      const cs = ok(card) ? getComputedStyle(card) : null;
      const curT = cs && cs.transform !== 'none' ? cs.transform : 'none';
      const curO = cs ? cs.opacity : '1';
      const so = ok(scrim) ? getComputedStyle(scrim).opacity : '1';
      stop();
      const d = rm ? MOTION.dur.fast : bottom ? MOTION.dur.sheetExit : MODAL_EXIT_MS;
      if (ok(card)) anims.current.push(card.animate(rm ? [{ opacity: curO }, { opacity: 0 }]
        : [{ transform: curT, opacity: curO }, { transform: out, opacity: o0 }], { duration: d, easing: bottom ? MOTION.ease.sheetExit : MOTION.ease.modalExit, fill: 'forwards' }));
      if (ok(scrim) && scrim !== card) anims.current.push(scrim.animate([{ opacity: so }, { opacity: 0 }], { duration: rm || !bottom ? d : MOTION.dur.sheetScrimExit, easing: bottom ? MOTION.ease.sheetScrimExit : MOTION.ease.modalExit, fill: 'forwards' }));
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
      { duration: rm ? MOTION.dur.fast : bottom ? MOTION.dur.sheetEnter : MODAL_ENTER_MS, easing: bottom ? MOTION.ease.sheet : MOTION.ease.modalEnter }));
    const enter = anims.current[anims.current.length - 1];
    window.dispatchEvent(new Event(SHEET_MOVING_EVENT));   // releases the tapped card's press, as the phone sheet does
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
  }, [phase === 'exiting', armed]);
  return entering;
}

/** Holds the first committed children while `hold` is true (same element identity, so React skips the
 *  subtree): late detail/ratings/images commit once the enter animation ends, never on its frames. */
export function Hold({ hold, children }: { hold: boolean; children: ReactNode }) {
  const snap = useRef<ReactNode>(children);
  if (!hold) snap.current = children;
  return <>{snap.current}</>;
}
