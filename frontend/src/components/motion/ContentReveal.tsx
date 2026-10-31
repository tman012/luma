/** Sheet content reveal (Designer, 2026-10-31 3:15 AM ET), shared by the TV Calendar, Activity and Rooms sheets.
 *
 * - One reveal per mount: the whole block fades 0 -> 1 over MOTION.dur.contentFade / MOTION.ease.contentFade once
 *   `ready`, never before the sheet's enter has landed (`landMs` after mount), on the same frame if it already has.
 *   No stagger, translate or scale. It never replays (paging, refetches and new rows just render).
 * - Ready on the first render (cached data): shown on frame 0, no fade. Reduced motion: no fade.
 * - Height: the block keeps min-height = the last height this block had (persisted per `id`), else one screen, from
 *   frame 0, so nothing below it moves when the content arrives.
 * - No skeletons, spinners or placeholder bars: before the reveal the block is empty (or its content is at opacity 0). */
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { MOTION, reducedMotion } from '@/theme/motion';

const KEY = (id: string) => `luma.contentReserve.${id}`;
export function readReserve(id: string): number | null {
  try { const v = Number(localStorage.getItem(KEY(id))); return Number.isFinite(v) && v > 0 && v < 20000 ? Math.round(v) : null; } catch { return null; }
}
function saveReserve(id: string, h: number) {
  try { if (h > 0) localStorage.setItem(KEY(id), String(Math.round(h))); } catch { /* private mode */ }
}
const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());

export type RevealState = 'hidden' | 'fading' | 'shown';

export function ContentReveal({ id, ready, landMs, children, testId }: {
  id: string; ready: boolean; landMs: number; children: ReactNode; testId?: string;
}) {
  const mountAt = useRef(now());
  const cached = useRef(ready);
  const done = useRef(ready);
  const [state, setState] = useState<RevealState>(ready ? 'shown' : 'hidden');
  const box = useRef<HTMLDivElement>(null);
  const reserve = useRef(readReserve(id));

  useLayoutEffect(() => {
    if (!ready || done.current) return;
    done.current = true;
    if (reducedMotion()) { setState('shown'); return; }
    const wait = Math.max(0, mountAt.current + landMs - now());
    let anim: Animation | null = null;
    const start = () => {
      setState('fading');
      const el = box.current;
      if (el && typeof el.animate === 'function') {
        anim = el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: MOTION.dur.contentFade, easing: MOTION.ease.contentFade, fill: 'backwards' });
        anim.onfinish = () => setState('shown');
      } else setState('shown');
    };
    if (wait <= 0) { start(); return; }
    const t = window.setTimeout(start, wait);
    return () => { window.clearTimeout(t); };
  }, [ready, landMs]);

  // The next open reserves what this one ended up as.
  useLayoutEffect(() => {
    if (state !== 'shown' || !box.current) return;
    const el = box.current;
    const prev = el.style.minHeight; el.style.minHeight = '0px';
    const h = el.offsetHeight; el.style.minHeight = prev;
    if (h) saveReserve(id, h);
  });

  return (
    <div ref={box} data-testid={testId} data-reveal={state} data-reveal-cached={cached.current ? 'true' : undefined}
      aria-busy={ready ? undefined : true}
      style={{ minHeight: reserve.current != null ? reserve.current : 'calc(var(--luma-screen-h, 100dvh) - 120px)',
        opacity: state === 'hidden' ? 0 : undefined }}>
      {ready ? children : null}
    </div>
  );
}

/** A row image in a fixed box: fades in over MOTION.dur.rowImage once loaded; already-decoded (cached) shows at once. */
export function FadeImg({ opacity = 1, style, onLoad, ...rest }: React.ImgHTMLAttributes<HTMLImageElement> & { opacity?: number }) {
  const [on, setOn] = useState(false);
  const instant = useRef(false);
  const ref = (el: HTMLImageElement | null) => {
    if (el && !on && el.complete && el.naturalWidth) { instant.current = true; setOn(true); }
  };
  return (
    <img ref={ref} {...rest}
      onLoad={e => { setOn(true); onLoad?.(e); }}
      style={{ ...style, opacity: on ? opacity : 0,
        transition: [style?.transition, instant.current || reducedMotion() ? '' : `opacity ${MOTION.dur.rowImage}ms ${MOTION.ease.contentFade}`].filter(Boolean).join(', ') || 'none' }} />
  );
}
