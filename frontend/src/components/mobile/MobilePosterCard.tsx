import { useState, useRef, useCallback } from 'react';

interface MobilePosterCardProps {
  posterUrl: string | null;
  title: string;
  subtitle?: string;
  progressPercent?: number;
  owned?: boolean;
  inWatchlist?: boolean;
  showOrb?: boolean;
  voteAverage?: number | null;
  digitalReleaseLabel?: string | null;
  onTap: () => void;
  onLongPress?: () => void;
}

export function MobilePosterCard({
  posterUrl,
  title,
  subtitle,
  progressPercent,
  owned,
  inWatchlist,
  showOrb = true,
  onTap,
  onLongPress,
  digitalReleaseLabel,
}: MobilePosterCardProps) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const longTimerRef = useRef<number | null>(null);
  const didLongPressRef = useRef(false);
  const touchOriginRef = useRef<{ x: number; y: number } | null>(null);

  const clearLongPress = useCallback(() => {
    if (longTimerRef.current != null) {
      window.clearTimeout(longTimerRef.current);
      longTimerRef.current = null;
    }
  }, []);

  const fireLongPress = useCallback(() => {
    if (!onLongPress) return;
    didLongPressRef.current = true;
    clearLongPress();
    try { navigator.vibrate?.(12); } catch { /* ignore */ }
    onLongPress();
  }, [onLongPress, clearLongPress]);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    if (!onLongPress) return;
    const t = e.touches[0];
    if (!t) return;
    didLongPressRef.current = false;
    touchOriginRef.current = { x: t.clientX, y: t.clientY };
    clearLongPress();
    longTimerRef.current = window.setTimeout(() => fireLongPress(), 450);
  }, [onLongPress, clearLongPress, fireLongPress]);

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (!onLongPress || longTimerRef.current == null) return;
    const t = e.touches[0];
    const origin = touchOriginRef.current;
    if (!t || !origin) return;
    if (Math.abs(t.clientX - origin.x) > 10 || Math.abs(t.clientY - origin.y) > 10) {
      clearLongPress();
    }
  }, [onLongPress, clearLongPress]);

  const onTouchEnd = useCallback(() => {
    clearLongPress();
    touchOriginRef.current = null;
  }, [clearLongPress]);

  const showWatchlistOrb = inWatchlist && !owned;
  const orbColor = 'bg-blue-400';
  const orbGlow = '0 0 6px rgba(96,165,250,0.6)';
  const suppressNative = Boolean(onLongPress);

  return (
    <button
      type="button"
      onClick={() => {
        if (didLongPressRef.current) {
          didLongPressRef.current = false;
          return;
        }
        onTap();
      }}
      onContextMenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (onLongPress) fireLongPress();
      }}
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
      onTouchCancel={onTouchEnd}
      className={`mobile-poster-card w-[115px] flex-shrink-0 snap-start text-left${suppressNative ? ' longpress-poster' : ''}`}
      style={suppressNative ? {
        outline: 'none',
        WebkitTapHighlightColor: 'transparent',
        WebkitTouchCallout: 'none',
        WebkitUserSelect: 'none',
        userSelect: 'none',
        touchAction: 'manipulation',
      } : {
        outline: 'none',
        WebkitTapHighlightColor: 'transparent',
      }}
    >
      <div
        className="relative aspect-[2/3] overflow-hidden rounded-lg bg-white/5"
        style={suppressNative ? {
          WebkitTapHighlightColor: 'transparent',
          WebkitTouchCallout: 'none',
          WebkitUserSelect: 'none',
          userSelect: 'none',
        } : undefined}
      >
        {posterUrl ? (
          <img
            src={posterUrl}
            alt={title}
            draggable={false}
            onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); }}
            className="h-full w-full object-cover"
            style={{
              opacity: imgLoaded ? 1 : 0,
              filter: imgLoaded ? 'blur(0)' : 'blur(10px)',
              transform: imgLoaded ? 'scale(1)' : 'scale(1.04)',
              transition: 'opacity 0.4s ease-out, filter 0.4s ease-out, transform 0.4s ease-out',
              pointerEvents: suppressNative ? 'none' : undefined,
              WebkitTouchCallout: 'none',
              WebkitUserSelect: 'none',
              userSelect: 'none',
            }}
            onLoad={(e) => {
              setImgLoaded(true);
              setTimeout(() => {
                (e.target as HTMLImageElement).style.willChange = 'auto';
              }, 500);
            }}
            onError={(e) => {
              (e.target as HTMLImageElement).style.visibility = 'hidden';
            }}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-white/20 text-xs">
            No Poster
          </div>
        )}

        {showOrb && showWatchlistOrb && !progressPercent && (
          <span
            className={`absolute left-1.5 top-1.5 h-2.5 w-2.5 rounded-full ${orbColor}`}
            style={{ boxShadow: orbGlow }}
          />
        )}

        {progressPercent != null && progressPercent > 0 && (
          <div className="absolute bottom-0 left-0 right-0 h-1 bg-white/20">
            <div
              className="h-full bg-accent"
              style={{ width: `${Math.min(progressPercent, 100)}%` }}
            />
          </div>
        )}
      </div>

      {digitalReleaseLabel && !progressPercent && (
        <p className="mt-1 text-center text-[9px] font-semibold text-white/70">
          {digitalReleaseLabel}
        </p>
      )}
      <p className={`text-xs text-white/80 line-clamp-1 ${digitalReleaseLabel && !progressPercent ? 'mt-0.5' : 'mt-1'}`}>{title}</p>
      {subtitle && (
        <p className="text-[10px] text-white/50 line-clamp-1">{subtitle}</p>
      )}
    </button>
  );
}
