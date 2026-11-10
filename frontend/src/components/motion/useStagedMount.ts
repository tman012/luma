/** Below-the-fold sections of a detail sheet mount one per task, after the sheet has finished entering, instead of in
 *  the commit that composes it: the cast shelf and the other long lists are not on screen when a sheet opens, and
 *  rendering them in the same task as the hero held the main thread (the longest sheet-open task at 4x CPU).
 *
 *  - `StagedProvider` owns the stage: 0 until `enabled` and the surface is no longer entering or exiting, then one more
 *    per frame-and-task (utils/scheduling) until `total`. A key change (another title in the same sheet) starts over.
 *  - `StagedBox at={N}` renders its content once the stage reaches N. Until then it holds its reserved box, so nothing
 *    moves when the content arrives, and the content fades in (opacity only, the REVEAL token).
 *  - Outside a provider every box renders at once (a layout under test, or any use that doesn't stage). */
import { createContext, createElement, useContext, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { useSheetEntering } from '@/components/layout/SheetFrame';
import { afterNextFrame } from '@/utils/scheduling';

const Stage = createContext<number>(Number.POSITIVE_INFINITY);

export function StagedProvider({ total, enabled, entering = false, resetKey, children }: {
  total: number; enabled: boolean; entering?: boolean; resetKey?: string | number; children: ReactNode;
}) {
  const sheetMoving = useSheetEntering();
  const [state, setState] = useState({ key: resetKey, stage: 0 });
  const stage = state.key === resetKey ? state.stage : 0;
  const go = enabled && !entering && !sheetMoving;
  useEffect(() => {
    if (!go || stage >= total) return;
    return afterNextFrame(() => setState((s) => (s.key === resetKey ? { key: resetKey, stage: Math.min(total, s.stage + 1) } : { key: resetKey, stage: 1 })));
  }, [go, stage, total, resetKey]);
  return createElement(Stage.Provider, { value: stage }, children);
}

export const useStage = (): number => useContext(Stage);

/** Opacity-only reveal for content that mounts after its box (the REVEAL token; reduced motion shortens it). */
export const STAGED_REVEAL = 'm-crossfade var(--m-dur-reveal) var(--m-ease-reveal) both';

/** The box of a staged section: `reserve` px tall until its content mounts (then the content sets the height). */
export function StagedBox({ at, reserve, style, children }: { at: number; reserve: number; style?: CSSProperties; children: ReactNode }) {
  const shown = useStage() >= at;
  const arrivedLate = useRef(!shown);   // content that was there from the first render does not fade
  return createElement('div', {
    'data-staged': shown ? 'shown' : 'pending',
    style: { ...style, minHeight: shown ? undefined : reserve, animation: shown && arrivedLate.current ? STAGED_REVEAL : undefined },
  }, shown ? children : null);
}
