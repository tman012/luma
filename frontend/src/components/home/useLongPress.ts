/** v254: prod 1b4ea74 MobilePosterCard's long press, as a hook for Home's cards (What you're missing):
 * 450 ms hold, cancelled by a move over 10 px, a 12 ms buzz, the right-click menu opens it too, and the tap that
 * ends a long press doesn't open the card. */
import { useCallback, useEffect, useRef } from 'react';

export const LONG_PRESS_MS = 450;
export const LONG_PRESS_SLOP_PX = 10;

export interface LongPressHandlers {
  onTouchStart: (e: React.TouchEvent) => void;
  onTouchMove: (e: React.TouchEvent) => void;
  onTouchEnd: () => void;
  onTouchCancel: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
  /** True once: the click that follows a long press is swallowed. */
  consumeClick: () => boolean;
}

export function useLongPress(onLongPress?: () => void): LongPressHandlers | null {
  const timer = useRef<number | null>(null);
  const fired = useRef(false);
  const origin = useRef<{ x: number; y: number } | null>(null);

  const clear = useCallback(() => {
    if (timer.current != null) { window.clearTimeout(timer.current); timer.current = null; }
  }, []);
  useEffect(() => clear, [clear]);

  const fire = useCallback(() => {
    if (!onLongPress) return;
    fired.current = true;
    clear();
    try { navigator.vibrate?.(12); } catch { /* unsupported */ }
    onLongPress();
  }, [onLongPress, clear]);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const t = e.touches[0];
    if (!t) return;
    fired.current = false;
    origin.current = { x: t.clientX, y: t.clientY };
    clear();
    timer.current = window.setTimeout(fire, LONG_PRESS_MS);
  }, [clear, fire]);

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (timer.current == null) return;
    const t = e.touches[0];
    const o = origin.current;
    if (!t || !o) return;
    if (Math.abs(t.clientX - o.x) > LONG_PRESS_SLOP_PX || Math.abs(t.clientY - o.y) > LONG_PRESS_SLOP_PX) clear();
  }, [clear]);

  const onTouchEnd = useCallback(() => { clear(); origin.current = null; }, [clear]);

  const onContextMenu = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    fire();
  }, [fire]);

  const consumeClick = useCallback(() => {
    if (!fired.current) return false;
    fired.current = false;
    return true;
  }, []);

  if (!onLongPress) return null;
  return { onTouchStart, onTouchMove, onTouchEnd, onTouchCancel: onTouchEnd, onContextMenu, consumeClick };
}

/** Prod's styles on a long-pressable poster: no callout, no selection, no tap flash. */
export const LONG_PRESS_STYLE: React.CSSProperties = {
  WebkitTapHighlightColor: 'transparent', WebkitTouchCallout: 'none', WebkitUserSelect: 'none', userSelect: 'none',
  touchAction: 'manipulation',
} as React.CSSProperties;
