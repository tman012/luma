/** The enter / exit lifecycle every sheet, modal and overlay shares (Movie and TV detail sheets, PersonDetail, PageModal).
 *
 * - `phase`: 'entering' on mount, 'visible' on the next frame, 'exiting' once a close starts, 'gone' when it has
 *   played. Surfaces animate from `phase` (SheetFrame, useModalMotion).
 * - `requestClose`: one close per surface (a second one inside the exit window is dropped), then the exit plays for
 *   exactly exitMs(kind) and `onExited` runs. The timer belongs to this mounted surface: a surface that was replaced
 *   or unmounted never reports itself closed afterwards (a stale timer once closed the title opened right after it).
 * - Escape and Back (`luma:modal-dismiss`) are handled only while `active`, so a parent that stays mounted under a
 *   child leaves both to the child.
 * No timing literal lives here: exit lengths come from the motion tokens. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { exitMs, MOTION, type SurfaceKind } from '@/theme/motion';

export type SurfacePhase = 'entering' | 'visible' | 'exiting' | 'gone';

/** One close per surface: Back (luma:modal-dismiss) and the close button can both fire within
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

export interface SurfacePresenceOptions {
  kind: SurfaceKind;
  /** Runs once the exit has played (the owner unmounts the surface here). */
  onExited: () => void;
  /** False for a surface under another one: no Escape, no Back. Default true. */
  active?: boolean;
  /** True while a nested layer inside the surface should take Escape first. */
  escapeBlocked?: boolean;
  /** Runs when a close starts (the owner closes its nested layers here). */
  onClosing?: () => void;
}

export function useSurfacePresence({ kind, onExited, active = true, escapeBlocked = false, onClosing }: SurfacePresenceOptions) {
  const [phase, setPhase] = useState<SurfacePhase>('entering');
  const closeOnce = useCloseOnce();
  const exitedRef = useRef(onExited);
  const closingRef = useRef(onClosing);
  const timer = useRef(0);
  const phaseRef = useRef<SurfacePhase>('entering');
  exitedRef.current = onExited;
  closingRef.current = onClosing;
  phaseRef.current = phase;

  useEffect(() => {
    const frame = requestAnimationFrame(() => setPhase((p) => (p === 'entering' ? 'visible' : p)));
    return () => cancelAnimationFrame(frame);
  }, []);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const requestClose = useCallback(() => {
    if (phaseRef.current === 'exiting' || phaseRef.current === 'gone' || !closeOnce()) return;
    closingRef.current?.();
    phaseRef.current = 'exiting';
    setPhase('exiting');
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      phaseRef.current = 'gone';
      setPhase('gone');
      exitedRef.current();
    }, exitMs(kind));
  }, [kind, closeOnce]);

  // Back: the host waits out the exit itself and unmounts this surface, so only the phase changes here.
  useEffect(() => {
    if (!active) return;
    const onDismiss = (e: Event) => {
      e.preventDefault();
      if (phaseRef.current === 'exiting' || phaseRef.current === 'gone' || !closeOnce()) return;
      phaseRef.current = 'exiting';
      setPhase('exiting');
    };
    window.addEventListener('luma:modal-dismiss', onDismiss);
    return () => window.removeEventListener('luma:modal-dismiss', onDismiss);
  }, [active, closeOnce]);

  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !escapeBlocked) requestClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [active, escapeBlocked, requestClose]);

  return { phase, requestClose, exiting: phase === 'exiting' || phase === 'gone' };
}
