/** The phone bottom sheet of the Home sheets (Storage, Activity, TV Calendar, Rooms): one shell, so each of them moves
 *  exactly as the detail sheets do and none of them keeps its own timings.
 *
 * - Enter and exit: `useSurfacePresence` (kind 'sheet', one close per sheet, the exit length is the token) and
 *   `useModalMotion(…, 'bottom')` (the panel slides on transform with the sheet curves, the scrim fades on opacity; WAAPI
 *   from the first frame, nothing else is animated, reduced motion is a short fade).
 * - The scrim is its own layer that only ever animates opacity (never its background color, never a blur).
 * - Drag: the grab handle (`[data-sheet-handle]`) moves the panel with the finger on transform, and the scrim follows
 *   on opacity. A release past the threshold plays the shared exit from where the finger left it; otherwise the panel
 *   eases back on the sheet curve (--m-dur-sheet-drag-min).
 * - Escape closes it (unless `guard` takes it first), the page behind does not scroll while it is open.
 * - `behind`: a detail sheet opened over this one from a row; this one drops under it and its scrim clears. */
import { useCallback, useEffect, useLayoutEffect, useRef, type CSSProperties, type ReactNode, type TouchEvent } from 'react';
import { createPortal } from 'react-dom';
import { useModalMotion } from '@/components/layout/SheetFrame';
import { useSurfacePresence } from '@/components/motion/useSurfacePresence';
import { MOTION, SURFACE_FADE } from '@/theme/motion';

/** A release past this distance (px) closes the sheet. */
export const DISMISS_DISTANCE = 120;

export interface SimpleSheetApi { requestClose: () => void }

export function SimpleSheet({
  name, onClosed, children, rootClassName, rootStyle, panelClassName, panelStyle, behind = false, guard, scrimDismiss = true,
}: {
  name: string;
  /** Runs once the exit has played (the owner unmounts the sheet here). */
  onClosed: () => void;
  children: ReactNode | ((api: SimpleSheetApi) => ReactNode);
  rootClassName?: string;
  rootStyle?: CSSProperties;
  panelClassName?: string;
  panelStyle?: CSSProperties;
  behind?: boolean;
  /** Return true to take a dismiss (scrim tap, drag release, Escape) instead of closing: a layer inside closes first. */
  guard?: () => boolean;
  /** False: a tap on the scrim does not close the sheet (dragging and Escape still do). */
  scrimDismiss?: boolean;
}) {
  const scrimRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const drag = useRef({ active: false, startY: 0, dy: 0 });
  const guardRef = useRef(guard);
  guardRef.current = guard;
  // active: false, so Back (luma:modal-dismiss) and the hook's own Escape stay with the detail sheet that may sit above.
  const { phase, requestClose } = useSurfacePresence({ kind: 'sheet', onExited: onClosed, active: false });
  useModalMotion(phase, scrimRef, panelRef, 'bottom');

  useLayoutEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, []);

  const dismiss = useCallback(() => {
    if (guardRef.current?.()) return;
    requestClose();
  }, [requestClose]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      dismiss();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [dismiss]);

  const onTouchStart = useCallback((e: TouchEvent) => {
    const target = e.target as HTMLElement;
    if (phase !== 'visible' || !target.closest('[data-sheet-handle]') || !e.touches.length) return;
    drag.current = { active: true, startY: e.touches[0]!.clientY, dy: 0 };
    const panel = panelRef.current; const scrim = scrimRef.current;
    if (panel) panel.style.transition = 'none';
    if (scrim) scrim.style.transition = 'none';
  }, [phase]);
  const onTouchMove = useCallback((e: TouchEvent) => {
    const d = drag.current;
    const panel = panelRef.current; const scrim = scrimRef.current;
    if (!d.active || !e.touches.length || !panel) return;
    d.dy = Math.max(0, e.touches[0]!.clientY - d.startY);
    panel.style.transform = `translate3d(0, ${d.dy}px, 0)`;
    if (scrim) scrim.style.opacity = String(Math.max(0, 1 - d.dy / (panel.offsetHeight || window.innerHeight)));
  }, []);
  const onTouchEnd = useCallback(() => {
    const d = drag.current;
    const panel = panelRef.current; const scrim = scrimRef.current;
    if (!d.active) return;
    d.active = false;
    if (!panel) return;
    if (d.dy > DISMISS_DISTANCE && !guardRef.current?.()) { requestClose(); return; }
    const back = `${MOTION.dur.sheetDragMin}ms ${MOTION.ease.sheet}`;
    panel.style.transition = `transform ${back}`;
    panel.style.transform = '';
    if (scrim) { scrim.style.transition = `opacity ${back}`; scrim.style.opacity = ''; }
    window.setTimeout(() => { panel.style.transition = ''; if (scrim) scrim.style.transition = SURFACE_FADE; }, MOTION.dur.sheetDragMin + MOTION.dur.settleSlack);
  }, [requestClose]);

  if (phase === 'gone') return null;
  return createPortal(
    <div className={rootClassName} data-sheet={name} data-phase={phase}
      style={{
        position: 'fixed', inset: 0, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
        zIndex: behind ? MOTION.z.sheet - 1 : MOTION.z.sheet + 1,
        pointerEvents: phase === 'exiting' ? 'none' : undefined, ...rootStyle,
      }}>
      <div ref={scrimRef} data-sheet-scrim="" aria-hidden="true" onClick={scrimDismiss ? dismiss : undefined}
        style={{ position: 'absolute', inset: 0, background: `rgba(0,0,0,${MOTION.scrimLight})`, opacity: behind ? 0 : undefined, transition: SURFACE_FADE }} />
      <div ref={panelRef} className={panelClassName} data-sheet-panel=""
        onTouchStart={onTouchStart} onTouchMove={onTouchMove} onTouchEnd={onTouchEnd} onTouchCancel={onTouchEnd}
        style={{ position: 'relative', willChange: 'transform', backfaceVisibility: 'hidden', ...panelStyle }}>
        {typeof children === 'function' ? children({ requestClose: dismiss }) : children}
      </div>
    </div>,
    document.body,
  );
}
