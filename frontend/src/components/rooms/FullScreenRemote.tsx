import { useState, useRef, useCallback, useEffect } from 'react';
import { createPortal } from 'react-dom';
import type { Room } from '@/hooks/useNotifications';
import { useInterpolatedProgress } from '@/hooks/useInterpolatedProgress';
import { cn } from '@/utils/cn';
import { apiFetch } from '../../apiClient';
import { apiUrl } from '@/urlHelpers';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FullScreenRemoteProps {
  room: Room;
  deviceId: string;
  onClose: () => void;
}

type Command =
  | 'power' | 'home' | 'back'
  | 'up' | 'down' | 'left' | 'right' | 'select'
  | 'channel_up' | 'channel_down'
  | 'vol_up' | 'vol_down'
  | 'play_pause' | 'stop' | 'mute';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DISMISS_THRESHOLD = 120;

// ---------------------------------------------------------------------------
// Send command helper
// ---------------------------------------------------------------------------

function sendCommand(roomId: string, command: Command, deviceId?: string) {
  apiFetch(`/api/room/${roomId}/remote`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command, ...(deviceId ? { device_id: deviceId } : {}) }),
  }).catch(() => {});
}

// ---------------------------------------------------------------------------
// Format duration
// ---------------------------------------------------------------------------

function formatDuration(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

// ---------------------------------------------------------------------------
// Hold-to-repeat hook
// ---------------------------------------------------------------------------

function useHoldRepeat(onFire: () => void) {
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clear = useCallback(() => {
    if (timeoutRef.current) { clearTimeout(timeoutRef.current); timeoutRef.current = null; }
    if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null; }
  }, []);

  const start = useCallback(() => {
    onFire();
    navigator.vibrate?.(10);
    timeoutRef.current = setTimeout(() => {
      intervalRef.current = setInterval(() => {
        onFire();
        navigator.vibrate?.(10);
      }, 200);
    }, 400);
  }, [onFire]);

  useEffect(() => clear, [clear]);

  return {
    onTouchStart: (e: React.TouchEvent) => { e.preventDefault(); start(); },
    onTouchEnd: clear,
    onTouchCancel: clear,
    onMouseDown: start,
    onMouseUp: clear,
    onMouseLeave: clear,
  };
}

// ---------------------------------------------------------------------------
// Shared inline styles
// ---------------------------------------------------------------------------

const concaveButton: React.CSSProperties = {
  background: 'linear-gradient(180deg, rgba(255,255,255,0.06) 0%, rgba(255,255,255,0.02) 100%)',
  boxShadow: `
    inset 0 2px 4px rgba(0,0,0,0.5),
    inset 0 -1px 1px rgba(255,255,255,0.06),
    0 1px 2px rgba(0,0,0,0.4),
    0 0 0 1px rgba(255,255,255,0.04)
  `,
};

const concaveButtonActive: React.CSSProperties = {
  background: 'linear-gradient(180deg, rgba(255,255,255,0.02) 0%, rgba(255,255,255,0.01) 100%)',
  boxShadow: `
    inset 0 3px 6px rgba(0,0,0,0.7),
    inset 0 -1px 1px rgba(255,255,255,0.03),
    0 0 12px rgba(120,160,255,0.12)
  `,
};

const convexTop: React.CSSProperties = {
  background: 'linear-gradient(180deg, rgba(255,255,255,0.10) 0%, rgba(255,255,255,0.03) 50%, rgba(0,0,0,0.05) 100%)',
  boxShadow: `
    inset 0 1px 1px rgba(255,255,255,0.12),
    inset 0 -2px 3px rgba(0,0,0,0.3),
    0 2px 6px rgba(0,0,0,0.5),
    0 0 0 1px rgba(255,255,255,0.05)
  `,
};

const convexBottom: React.CSSProperties = {
  background: 'linear-gradient(180deg, rgba(0,0,0,0.05) 0%, rgba(255,255,255,0.03) 50%, rgba(255,255,255,0.10) 100%)',
  boxShadow: `
    inset 0 2px 3px rgba(0,0,0,0.3),
    inset 0 -1px 1px rgba(255,255,255,0.12),
    0 2px 6px rgba(0,0,0,0.5),
    0 0 0 1px rgba(255,255,255,0.05)
  `,
};

const rockerActive: React.CSSProperties = {
  background: 'linear-gradient(180deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0.01) 100%)',
  boxShadow: `
    inset 0 3px 6px rgba(0,0,0,0.6),
    0 0 10px rgba(120,160,255,0.10)
  `,
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function PremiumButton({
  label,
  onClick,
  size = 48,
  className = '',
  variant = 'default',
  children,
}: {
  label: string;
  onClick: () => void;
  size?: number;
  className?: string;
  variant?: 'default' | 'power';
  children: React.ReactNode;
}) {
  const [pressed, setPressed] = useState(false);

  const powerRing: React.CSSProperties = variant === 'power' ? {
    boxShadow: pressed
      ? 'inset 0 3px 6px rgba(0,0,0,0.7), 0 0 16px rgba(239,68,68,0.3)'
      : `
        inset 0 2px 4px rgba(0,0,0,0.5),
        inset 0 -1px 1px rgba(255,255,255,0.06),
        0 1px 2px rgba(0,0,0,0.4),
        0 0 0 1px rgba(239,68,68,0.15),
        0 0 8px rgba(239,68,68,0.10)
      `,
    background: pressed
      ? 'linear-gradient(180deg, rgba(239,68,68,0.08) 0%, rgba(239,68,68,0.02) 100%)'
      : 'linear-gradient(180deg, rgba(239,68,68,0.12) 0%, rgba(239,68,68,0.04) 100%)',
  } : {};

  const style: React.CSSProperties = {
    width: size,
    height: size,
    ...(variant === 'power' ? powerRing : (pressed ? concaveButtonActive : concaveButton)),
  };

  return (
    <button
      type="button"
      onClick={onClick}
      onPointerDown={() => setPressed(true)}
      onPointerUp={() => setPressed(false)}
      onPointerLeave={() => setPressed(false)}
      className={`flex items-center justify-center rounded-full transition-transform duration-100 active:scale-[0.97] ${className}`}
      style={style}
      aria-label={label}
    >
      {children}
    </button>
  );
}

function DpadSection({ onCommand }: { onCommand: (cmd: Command) => void }) {
  const [activeDir, setActiveDir] = useState<string | null>(null);

  const clearActive = useCallback(() => setActiveDir(null), []);

  const directionZone = (dir: 'up' | 'down' | 'left' | 'right') => {
    const rotation = { up: 0, right: 90, down: 180, left: 270 }[dir];
    const isActive = activeDir === dir;

    const positionClass = {
      up: 'top-0 left-1/2 -translate-x-1/2',
      down: 'bottom-0 left-1/2 -translate-x-1/2',
      left: 'left-0 top-1/2 -translate-y-1/2',
      right: 'right-0 top-1/2 -translate-y-1/2',
    }[dir];

    const sizeClass = (dir === 'up' || dir === 'down')
      ? 'w-[72px] h-[58px]'
      : 'h-[72px] w-[58px]';

    return (
      <button
        type="button"
        className={`absolute ${positionClass} ${sizeClass} flex items-center justify-center transition-colors duration-75`}
        style={{
          background: isActive ? 'rgba(255,255,255,0.06)' : 'transparent',
          borderRadius: '16px',
        }}
        aria-label={dir.charAt(0).toUpperCase() + dir.slice(1)}
        onClick={() => { onCommand(dir); navigator.vibrate?.(10); }}
        onPointerDown={() => setActiveDir(dir)}
        onPointerUp={clearActive}
        onPointerLeave={clearActive}
        onPointerCancel={clearActive}
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="text-white/70"
          style={{ transform: `rotate(${rotation}deg)` }}
        >
          <polyline points="18 15 12 9 6 15" />
        </svg>
      </button>
    );
  };

  const [okPressed, setOkPressed] = useState(false);

  return (
    <div
      className="relative"
      style={{
        width: 190,
        height: 190,
        borderRadius: '50%',
        background: 'linear-gradient(145deg, rgba(255,255,255,0.05) 0%, rgba(0,0,0,0.1) 100%)',
        boxShadow: `
          inset 0 1px 1px rgba(255,255,255,0.08),
          inset 0 -2px 4px rgba(0,0,0,0.3),
          0 4px 16px rgba(0,0,0,0.6),
          0 0 0 1px rgba(255,255,255,0.05),
          0 0 0 3px rgba(0,0,0,0.3)
        `,
      }}
    >
      <div
        className="absolute inset-0 rounded-full pointer-events-none"
        style={{
          background: 'radial-gradient(ellipse at 50% 20%, rgba(255,255,255,0.04) 0%, transparent 60%)',
        }}
      />

      {/* D-pad separator — diagonal lines between adjacent arrows */}
      <svg style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }} width="190" height="190" viewBox="0 0 190 190">
        <line x1="28" y1="28" x2="70" y2="70" stroke="rgba(255,255,255,0.08)" strokeWidth="1" />
        <line x1="120" y1="70" x2="162" y2="28" stroke="rgba(255,255,255,0.08)" strokeWidth="1" />
        <line x1="120" y1="120" x2="162" y2="162" stroke="rgba(255,255,255,0.08)" strokeWidth="1" />
        <line x1="28" y1="162" x2="70" y2="120" stroke="rgba(255,255,255,0.08)" strokeWidth="1" />
      </svg>

      {directionZone('up')}
      {directionZone('down')}
      {directionZone('left')}
      {directionZone('right')}

      <button
        type="button"
        onClick={() => { onCommand('select'); navigator.vibrate?.(10); }}
        onPointerDown={() => setOkPressed(true)}
        onPointerUp={() => setOkPressed(false)}
        onPointerLeave={() => setOkPressed(false)}
        className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 flex items-center justify-center transition-transform duration-100 active:scale-[0.96]"
        style={{
          width: 64,
          height: 64,
          borderRadius: '50%',
          background: okPressed
            ? 'linear-gradient(180deg, rgba(255,255,255,0.02) 0%, rgba(255,255,255,0.00) 100%)'
            : 'linear-gradient(180deg, rgba(255,255,255,0.08) 0%, rgba(255,255,255,0.03) 100%)',
          boxShadow: okPressed
            ? 'inset 0 4px 8px rgba(0,0,0,0.7), inset 0 -1px 1px rgba(255,255,255,0.03), 0 0 16px rgba(120,160,255,0.15)'
            : 'inset 0 2px 4px rgba(0,0,0,0.5), inset 0 -1px 2px rgba(255,255,255,0.08), 0 2px 8px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.06)',
        }}
        aria-label="OK / select"
      >
        <span
          className="text-[13px] font-semibold tracking-wide"
          style={{
            color: okPressed ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.65)',
            textShadow: okPressed ? '0 0 8px rgba(120,160,255,0.4)' : 'none',
          }}
        >
          OK
        </span>
      </button>
    </div>
  );
}

function RockerButton({
  label,
  position,
  onFire,
  children,
}: {
  label: string;
  position: 'top' | 'bottom';
  onFire: () => void;
  children: React.ReactNode;
}) {
  const hold = useHoldRepeat(onFire);
  const [pressed, setPressed] = useState(false);
  const radiusClass = position === 'top'
    ? 'rounded-t-[26px] rounded-b-[5px]'
    : 'rounded-b-[26px] rounded-t-[5px]';

  const baseStyle = position === 'top' ? convexTop : convexBottom;

  return (
    <button
      type="button"
      className={`flex w-[56px] h-[78px] items-center justify-center transition-transform duration-100 active:scale-[0.97] ${radiusClass}`}
      style={pressed ? rockerActive : baseStyle}
      aria-label={label}
      onPointerDown={() => setPressed(true)}
      onPointerUp={() => setPressed(false)}
      onPointerLeave={() => setPressed(false)}
      {...hold}
    >
      <span className={pressed ? 'text-white/90' : 'text-white/60'}>
        {children}
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Now Playing — backdrop card with title overlay
// ---------------------------------------------------------------------------

function NowPlayingBackdrop({ room }: { room: Room }) {
  const s = room.session;
  const a = room.activity;
  const hasPlayback = !!s || !!a;

  const isTV = !!s?.grandparentTitle;
  // Prefer backdrop (art), fall back to thumb
  const backdropPath = s?.art || null;
  const backdrop = backdropPath
    ? apiUrl(`/api/img/plex?url=${encodeURIComponent(backdropPath)}`)
    : a?.art || a?.thumb || null;
  const isPlaying = s ? s.state === 'playing' : a?.state === 'playing';

  const serverOffset = s ? s.viewOffset : (a?.position ?? 0);
  const timeTotal = s ? s.duration : (a?.duration ?? 0);
  const { displayOffset, pct } = useInterpolatedProgress(serverOffset, timeTotal, !!isPlaying);

  const title = s
    ? (isTV ? s.grandparentTitle : s.title)
    : a?.title;
  const year = s
    ? (isTV ? null : s.year)
    : a?.year;

  if (!hasPlayback) {
    return (
      <div className="flex items-center justify-center rounded-xl bg-white/5" style={{ height: 190 }}>
        <span className="text-[13px] text-white/20">Nothing playing</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      {/* Backdrop with title overlay */}
      <div
        className="relative overflow-hidden rounded-xl bg-white/5"
        style={{
          height: 190,
          boxShadow: '0 4px 20px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.04)',
        }}
      >
        {backdrop ? (
          <img src={backdrop} alt="" className="h-full w-full object-cover" />
        ) : (
          <div className="h-full w-full bg-white/3" />
        )}
        {/* Bottom gradient + title */}
        <div
          className="absolute inset-x-0 bottom-0 flex items-end px-3 pb-2.5"
          style={{
            paddingTop: 40,
            background: 'linear-gradient(to top, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0.4) 60%, transparent 100%)',
          }}
        >
          {title && (
            <p className="truncate text-[13px] font-semibold text-white/90">
              {title}
              {year && (
                <span className="ml-1 font-normal text-white/50">({year})</span>
              )}
            </p>
          )}
        </div>
      </div>

      {/* Progress bar underneath */}
      {timeTotal > 0 && (
        <div className="mt-2 px-1">
          <div className="h-[3px] w-full overflow-hidden rounded-full bg-white/10">
            <div
              className={cn(
                'h-full rounded-full transition-[width] duration-500',
                isPlaying ? 'bg-green-500' : 'bg-amber-500',
              )}
              style={{ width: `${pct}%` }}
            />
          </div>
          <p className="mt-1 text-center text-[10px] text-white/30">
            {formatDuration(displayOffset)} / {formatDuration(timeTotal)}
          </p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component — bottom sheet pattern
// ---------------------------------------------------------------------------

export function FullScreenRemote({ room, deviceId, onClose }: FullScreenRemoteProps) {
  // Sheet animation state
  const sheetRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });
  const [dragOffset, setDragOffset] = useState(0);
  const [dismissing, setDismissing] = useState(false);
  const [entered, setEntered] = useState(false);

  useEffect(() => {
    requestAnimationFrame(() => setEntered(true));
    const prev = document.documentElement.style.background;
    document.documentElement.style.background = '#000';
    return () => { document.documentElement.style.background = prev; };
  }, []);

  // ESC to close
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation();
        handleDismiss();
      }
    }
    window.addEventListener('keydown', handleKey, true);
    return () => window.removeEventListener('keydown', handleKey, true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onClose]);

  const send = useCallback(
    (cmd: Command) => sendCommand(room.roomId, cmd, deviceId),
    [room.roomId, deviceId],
  );

  const handleDismiss = useCallback(() => {
    setDismissing(true);
    setTimeout(onClose, 250);
  }, [onClose]);

  // Drag-to-dismiss (touch) — same pattern as MobileSourcesPanel
  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const target = e.target as HTMLElement;
    const isHandle = target.closest('[data-sheet-handle]');
    if (isHandle && e.touches.length > 0) {
      dragRef.current = { active: true, startY: e.touches[0]!.clientY, currentY: e.touches[0]!.clientY };
    }
  }, []);

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragRef.current.active || !e.touches.length) return;
    dragRef.current.currentY = e.touches[0]!.clientY;
    const dy = Math.max(0, dragRef.current.currentY - dragRef.current.startY);
    setDragOffset(dy);
  }, []);

  const onTouchEnd = useCallback(() => {
    const dy = dragRef.current.currentY - dragRef.current.startY;
    dragRef.current.active = false;
    if (dy > DISMISS_THRESHOLD) {
      handleDismiss();
    } else {
      setDragOffset(0);
    }
  }, [handleDismiss]);

  const sheetTranslate = dismissing ? '100%' : entered ? `${dragOffset}px` : '100%';

  return createPortal(
    <div className="luma-bleed"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10002,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'flex-end',
        background: entered && !dismissing ? 'rgba(0,0,0,0.4)' : 'transparent',
        transition: 'background 0.25s ease',
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) handleDismiss();
      }}
    >
      <div className="luma-bleed-panel"
        ref={sheetRef}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        style={{
          height: 'calc(100% - env(safe-area-inset-top, 0px) - 6px)',
          maxHeight: 'calc(100% - env(safe-area-inset-top, 0px) - 6px)',
          display: 'flex',
          flexDirection: 'column',
          background: 'var(--color-kiosk-black)',
          borderRadius: '20px 20px 0 0',
          overflow: 'hidden',
          transform: `translateY(${sheetTranslate})`,
          transition: dragOffset > 0 ? 'none' : 'transform 0.3s cubic-bezier(0.32, 0.72, 0, 1)',
          willChange: 'transform',
        }}
      >
        {/* Grab handle */}
        <div
          data-sheet-handle
          style={{
            flexShrink: 0,
            display: 'flex',
            justifyContent: 'center',
            padding: '12px 0 8px',
            cursor: 'grab',
          }}
        >
          <div style={{
            width: '40px',
            height: '5px',
            borderRadius: '3px',
            background: 'rgba(255,255,255,0.3)',
          }} />
        </div>

        {/* Header: Home (left) + Room Name + Power (right) */}
        <div className="shrink-0 px-5 pb-2">
          <div className="flex items-center justify-between">
            <PremiumButton label="Home" onClick={() => send('home')} size={48}>
              <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="text-white/55">
                <path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
                <polyline points="9 22 9 12 15 12 15 22" />
              </svg>
            </PremiumButton>
            <span className="text-[14px] font-medium tracking-wide" style={{ color: 'rgba(120,160,255,0.8)' }}>
              {room.roomName}
            </span>
            <PremiumButton label="Power" onClick={() => send('power')} size={48} variant="power">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-red-400">
                <path d="M18.36 6.64a9 9 0 1 1-12.73 0" /><line x1="12" y1="2" x2="12" y2="12" />
              </svg>
            </PremiumButton>
          </div>
        </div>

        {/* Now Playing — backdrop card */}
        <div className="shrink-0 px-5 py-2">
          <NowPlayingBackdrop room={room} />
        </div>

        {/* D-pad + Rockers */}
        <div className="flex items-center justify-center gap-4 shrink-0 pt-4 pb-4">
          {/* Volume rocker */}
          <div className="flex flex-col items-center gap-[3px]">
            <span className="mb-1.5 text-[9px] font-semibold" style={{ color: 'rgba(255,255,255,0.25)' }}>
              Vol
            </span>
            <RockerButton label="Volume up" position="top" onFire={() => send('vol_up')}>
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            </RockerButton>
            <RockerButton label="Volume down" position="bottom" onFire={() => send('vol_down')}>
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            </RockerButton>
          </div>

          {/* D-pad disc */}
          <DpadSection onCommand={send} />

          {/* Channel rocker */}
          <div className="flex flex-col items-center gap-[3px]">
            <span className="mb-1.5 text-[9px] font-semibold" style={{ color: 'rgba(255,255,255,0.25)' }}>
              Ch
            </span>
            <RockerButton label="Channel up" position="top" onFire={() => send('channel_up')}>
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="18 15 12 9 6 15" />
              </svg>
            </RockerButton>
            <RockerButton label="Channel down" position="bottom" onFire={() => send('channel_down')}>
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </RockerButton>
          </div>
        </div>

        {/* Bottom transport bar: Back, Play/Pause, Stop, Mute */}
        <div className="shrink-0 px-5 pt-8 pb-6">
          <div className="flex items-center justify-center gap-5">
            <PremiumButton label="Back" onClick={() => send('back')} size={64}>
              <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="text-white/55">
                <path d="M9 14 4 9l5-5" />
                <path d="M20 20v-7a4 4 0 0 0-4-4H4" />
              </svg>
            </PremiumButton>
            <PremiumButton label="Play / pause" onClick={() => send('play_pause')} size={64}>
              <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="none" className="text-white/65">
                <path d="M6 4l12 8-12 8z" />
              </svg>
            </PremiumButton>
            <PremiumButton label="Stop" onClick={() => send('stop')} size={64}>
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="currentColor" stroke="none" className="text-white/65">
                <rect x="5" y="5" width="14" height="14" rx="2" />
              </svg>
            </PremiumButton>
            <PremiumButton label="Mute" onClick={() => send('mute')} size={64}>
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-white/65">
                <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                <line x1="23" y1="9" x2="17" y2="15" /><line x1="17" y1="9" x2="23" y2="15" />
              </svg>
            </PremiumButton>
          </div>
        </div>

        {/* Flex spacer — pushes content up; safe area at bottom */}
        <div style={{ flex: '1 0 auto', minHeight: 'calc(env(safe-area-inset-bottom, 12px) + 16px)' }} />
      </div>
    </div>,
    document.body,
  );
}
