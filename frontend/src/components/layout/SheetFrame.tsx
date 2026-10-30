/** The phone bottom sheet frame (MOTION.md §1): scrim + card portaled at --z-sheet.
 *
 * - Enter: the card mounts at its final layout, translateY(100%), opaque, and slides to 0 over
 *   --m-dur-sheet-enter / --m-ease-decel; the scrim ramps 0 -> --m-sheet-scrim over --m-dur-standard,
 *   both from the first frame (WAAPI, no transition-less mount, no opacity gate).
 * - Exit: from wherever the card is now (mid-enter or mid-drag) to translateY(100%) over
 *   --m-dur-sheet-exit / --m-ease-accel; the scrim fades over 200ms. Re-entering cancels the exit.
 * - Reduced motion: opacity only, --m-dur-fast.
 * - Body scroll lock and the <html> background (#111318) from enter start to unmount.
 * `data-composed` stays on the root for the composed_ms hooks; it no longer gates visibility.
 */
import { createContext, useContext, useLayoutEffect, useRef, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { APP_BG, MOTION, reducedMotion } from '@/theme/motion';
import { BACKDROP_HEIGHT } from '@/hooks/useBackdropEffects';
import { tapShell } from '@/services/modalPrefetch';

export type SheetPhase = 'entering' | 'visible' | 'exiting' | 'gone';

const CardRef = createContext<React.RefObject<HTMLDivElement | null> | null>(null);

/** Duration the owner should wait after phase 'exiting' before unmounting. */
export const SHEET_EXIT_MS = MOTION.dur.sheetExit;

function currentY(el: HTMLElement): number {
  const t = getComputedStyle(el).transform;
  if (!t || t === 'none') return 0;
  const m = t.match(/matrix(3d)?\(([^)]+)\)/);
  if (!m) return 0;
  const v = m[2]!.split(',').map(Number);
  return m[1] ? v[13]! : v[5]!;
}

export function SheetFrame({ name, variant = 'mobile', phase, composed, onScrimClick, children }: {
  name: string; variant?: string; phase: SheetPhase; composed: boolean;
  onScrimClick?: () => void; children: ReactNode;
}) {
  const cardRef = useRef<HTMLDivElement>(null);
  const scrimRef = useRef<HTMLDivElement>(null);
  const anims = useRef<Animation[]>([]);
  const entered = useRef(false);
  const prevPhase = useRef<SheetPhase>(phase);

  // Scroll lock + app background paint, from enter start until unmount.
  useLayoutEffect(() => {
    const prevOverflow = document.body.style.overflow;
    const prevBg = document.documentElement.style.background;
    document.body.style.overflow = 'hidden';
    document.documentElement.style.background = APP_BG;
    return () => {
      document.body.style.overflow = prevOverflow;
      document.documentElement.style.background = prevBg;
    };
  }, []);

  useLayoutEffect(() => {
    const card = cardRef.current;
    const scrim = scrimRef.current;
    if (!card || !scrim || typeof card.animate !== 'function') return;
    const rm = reducedMotion();
    const stop = () => { anims.current.forEach((a) => a.cancel()); anims.current = []; };
    if (phase === 'exiting') {
      prevPhase.current = phase;
      const y = currentY(card);
      const h = card.offsetHeight || window.innerHeight;
      stop();
      card.style.willChange = 'transform';
      const exitMs = rm ? MOTION.dur.fast : Math.max(160, MOTION.dur.sheetExit * Math.max(0, h - y) / h);
      anims.current = [
        rm
          ? card.animate([{ opacity: 1 }, { opacity: 0 }], { duration: exitMs, easing: MOTION.ease.standard, fill: 'forwards' })
          : card.animate([{ transform: `translate3d(0, ${y}px, 0)` }, { transform: 'translate3d(0, 100%, 0)' }],
            { duration: exitMs, easing: MOTION.ease.accel, fill: 'forwards' }),
        scrim.animate([{ opacity: getComputedStyle(scrim).opacity }, { opacity: 0 }],
          { duration: rm ? MOTION.dur.fast : 200, easing: MOTION.ease.accel, fill: 'forwards' }),
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
            { duration: MOTION.dur.sheetEnter, easing: MOTION.ease.decel })];
        }
      }
      return;
    }
    entered.current = true;
    card.style.willChange = 'transform';
    const enter = rm
      ? card.animate([{ opacity: 0 }, { opacity: 1 }], { duration: MOTION.dur.fast, easing: MOTION.ease.standard })
      : card.animate([{ transform: 'translate3d(0, 100%, 0)' }, { transform: 'translate3d(0, 0, 0)' }],
        { duration: MOTION.dur.sheetEnter, easing: MOTION.ease.decel });
    const fade = scrim.animate([{ opacity: 0 }, { opacity: MOTION.scrim }],
      { duration: rm ? MOTION.dur.fast : MOTION.dur.standard, easing: MOTION.ease.standard });
    anims.current = [enter, fade];
    enter.finished.then(() => {
      card.style.willChange = '';
      anims.current = anims.current.filter((a) => a !== enter && a !== fade);
      card.dispatchEvent(new CustomEvent('luma:sheet-entered', { bubbles: true }));
    }, () => {});
  }, [phase]);

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
      <CardRef.Provider value={cardRef}>{children}</CardRef.Provider>
    </div>,
    document.body,
  );
}

/** The sheet card: rounded top, full height under the safe area, slides with the frame. */
export function SheetCard({ background, children }: { background: string; children: ReactNode }) {
  const ref = useContext(CardRef);
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
  return <div ref={ref} className="luma-bleed-down" data-sheet-card="" style={style}>{children}</div>;
}

/** The sheet's first frame while details load (MOTION.md §1.2-1.3): the final layout's top (backdrop box,
 *  title block, reserved rows) filled from what the tapped card showed; the details crossfade in over it. */
export function SeedShell({ overlap }: { overlap: number }) {
  const seed = tapShell();
  return (
    <div data-seed-shell="" aria-busy="true">
      <div style={{ position: 'relative', height: BACKDROP_HEIGHT, overflow: 'hidden', background: 'var(--m-placeholder)' }}>
        {seed?.poster && (
          <img src={seed.poster} alt="" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', opacity: 0.6 }} />
        )}
        <div style={{ position: 'absolute', inset: 0, background: `linear-gradient(to bottom, transparent 40%, ${APP_BG})` }} />
      </div>
      <div style={{ position: 'relative', marginTop: -overlap, textAlign: 'center', padding: '0 16px' }}>
        <div style={{ height: 140, display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }}>
          <h1 style={{ fontSize: 28, fontWeight: 700, color: '#fff', lineHeight: 1.2, margin: 0, textShadow: '0 2px 8px rgba(0,0,0,0.5)' }}>
            {seed?.title ?? ''}
          </h1>
        </div>
        <div style={{ height: 20, margin: '24px auto 0', width: '60%', borderRadius: 6, background: 'var(--m-placeholder)' }} />
        <div style={{ height: 44, margin: '20px auto 0', width: '70%', borderRadius: 10, background: 'var(--m-placeholder)' }} />
        <div style={{ height: 72, margin: '20px auto 0', width: '84%', borderRadius: 12, background: 'var(--m-placeholder)' }} />
      </div>
    </div>
  );
}

/** Content that replaces a seed shell crossfades in (--m-dur-crossfade); content there from the first frame doesn't. */
export const SHELL_CROSSFADE = 'm-crossfade var(--m-dur-crossfade) var(--m-ease-standard) both';
