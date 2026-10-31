import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { type Room, type SessionData } from '@/hooks/useNotifications';
import { useNotificationsContext } from '@/contexts/NotificationsContext';
import { useInterpolatedProgress } from '@/hooks/useInterpolatedProgress';
import { FullScreenRemote } from '@/components/rooms/FullScreenRemote';
import { isLiveTvNoArt, liveTvDisplayTitle, LiveTvGlyph } from '@/components/rooms/liveTvArt';
import { fetchRooms, type DeviceData } from '@/services/settingsApi';
import { cn } from '@/utils/cn';
import { apiUrl } from '@/urlHelpers';
import { ContentReveal, FadeImg } from '@/components/motion/ContentReveal';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DISMISS_THRESHOLD = 120;
const ENTER_DURATION = 600;
const EXIT_DURATION = 400;
const SPRING = 'cubic-bezier(0.16, 1, 0.3, 1)';

// ---------------------------------------------------------------------------
// Helpers (moved from RoomsPage)
// ---------------------------------------------------------------------------

function formatDuration(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function PosterPlaceholder() {
  return (
    <div className="flex h-full w-full items-center justify-center text-white/20">
      <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2" y="2" width="20" height="20" rx="2" /><circle cx="8" cy="8" r="2" /><path d="m21 15-5-5L5 21" /></svg>
    </div>
  );
}

const APP_COLORS: Record<string, { badge: string; dot: string; bar: string }> = {
  YouTube: { badge: 'bg-red-600 text-white', dot: 'bg-red-500', bar: 'bg-red-500' },
  Netflix: { badge: 'bg-red-700 text-white', dot: 'bg-red-600', bar: 'bg-red-600' },
  'Prime Video': { badge: 'bg-blue-600 text-white', dot: 'bg-blue-500', bar: 'bg-blue-500' },
  Plex: { badge: 'bg-amber-600 text-white', dot: 'bg-amber-500', bar: 'bg-amber-500' },
  Kodi: { badge: 'bg-sky-600 text-white', dot: 'bg-sky-500', bar: 'bg-sky-500' },
  'Disney+': { badge: 'bg-blue-800 text-white', dot: 'bg-blue-600', bar: 'bg-blue-600' },
  TiViMate: { badge: 'bg-purple-600 text-white', dot: 'bg-purple-500', bar: 'bg-purple-500' },
  'Live TV': { badge: 'bg-sky-600 text-white', dot: 'bg-sky-500', bar: 'bg-sky-500' },
  Spotify: { badge: 'bg-green-600 text-white', dot: 'bg-green-500', bar: 'bg-green-500' },
  Hulu: { badge: 'bg-green-700 text-white', dot: 'bg-green-600', bar: 'bg-green-600' },
};
const DEFAULT_APP_COLOR = { badge: 'bg-white/20 text-white', dot: 'bg-green-500', bar: 'bg-green-500' };

function resolveActiveDeviceId(
  room: Room,
  roomDevices: DeviceData[] | undefined,
): string | undefined {
  if (room.activity?.deviceId) return room.activity.deviceId;
  if (room.session && roomDevices) {
    const plexDev = roomDevices.find((d) =>
      d.services?.some((svc) => svc.type === 'plex'),
    );
    if (plexDev) return plexDev.id;
  }
  if (roomDevices?.length === 1) return roomDevices[0]!.id;
  return undefined;
}

const remoteButtonStyle: React.CSSProperties = {
  background: 'linear-gradient(180deg, rgba(255,255,255,0.10) 0%, rgba(255,255,255,0.05) 100%)',
  boxShadow: `
    inset 0 1px 0 rgba(255,255,255,0.10),
    inset 0 -1px 2px rgba(0,0,0,0.2),
    0 2px 8px rgba(0,0,0,0.3),
    0 0 0 1px rgba(255,255,255,0.04)
  `,
  color: 'rgba(255,255,255,0.75)',
};

const remoteIcon = (
  <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.7 }}>
    <rect x="2" y="2" width="20" height="20" rx="4" />
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2v4" /><path d="M12 18v4" /><path d="M2 12h4" /><path d="M18 12h4" />
  </svg>
);

// ---------------------------------------------------------------------------
// RoomCard
// ---------------------------------------------------------------------------

function RoomCard({
  room,
  expanded,
  onToggle,
  onOpenRemote,
  onNeedDevice,
  roomDevices,
  hasHub,
}: {
  room: Room;
  expanded: boolean;
  onToggle: () => void;
  onOpenRemote: (deviceId: string) => void;
  onNeedDevice: () => void;
  roomDevices: DeviceData[] | undefined;
  hasHub: boolean;
}) {
  const s = room.session;
  const a = room.activity;
  const hasPlayback = !!s || !!a;
  const isTV = !!s?.grandparentTitle;
  const plexThumb = s ? (isTV ? (s.grandparentThumb || s.thumb) : s.thumb) : null;
  const poster = plexThumb
    ? apiUrl(`/api/img/plex?url=${encodeURIComponent(plexThumb)}`)
    : a?.thumb || null;
  const liveNoArt = isLiveTvNoArt(a, !!s);
  const isPlaying = s ? s.state === 'playing' : a?.state === 'playing';

  const serverOffset = s ? s.viewOffset : (a?.position ?? 0);
  const timeTotal = s ? s.duration : (a?.duration ?? 0);
  const { displayOffset, pct } = useInterpolatedProgress(serverOffset, timeTotal, !!isPlaying);

  const appColors = a ? (APP_COLORS[a.appName] ?? DEFAULT_APP_COLOR) : null;
  const autoDeviceId = resolveActiveDeviceId(room, roomDevices);
  const needsDevicePicker = !hasPlayback && roomDevices && roomDevices.length > 1 && !autoDeviceId;
  const activityTitle = a
    ? (liveNoArt ? liveTvDisplayTitle(a) : a.title)
    : null;

  return (
    <div className="rounded-2xl bg-white/5 overflow-hidden">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-4 p-4 text-left transition-colors active:bg-white/5"
      >
        {(s || poster || liveNoArt) ? (
          <div
            className={cn(
              'relative h-[90px] w-[60px] shrink-0 overflow-hidden rounded-lg',
              liveNoArt && !poster ? 'bg-sky-950' : 'bg-white/5',
            )}
            data-live-tv-poster={liveNoArt && !poster ? '1' : undefined}
          >
            {liveNoArt && !poster && (
              <div className="absolute inset-0 bg-sky-500/15" />
            )}
            {poster ? (
              <FadeImg src={poster} alt="" className="relative h-full w-full object-cover" />
            ) : liveNoArt ? (
              <div className="relative flex h-full w-full items-center justify-center text-white/70">
                <LiveTvGlyph size={28} />
              </div>
            ) : (
              <PosterPlaceholder />
            )}
          </div>
        ) : (
          <div className="flex h-[52px] w-[52px] shrink-0 items-center justify-center rounded-xl bg-white/5 text-white/20">
            <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="7" width="20" height="15" rx="2" ry="2" /><polyline points="17 2 12 7 7 2" /></svg>
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="text-[14px] font-medium leading-snug text-blue-400">
            {room.roomName}
            {s && <span className="text-white/30"> · </span>}
            {s && <span>Plex</span>}
            {!s && a && <span className="text-white/30"> · </span>}
            {!s && a && <span>{a.appName}</span>}
          </p>
          {room.deviceCount > 0 && !hasPlayback && (
            <p className="text-[12px] text-white/30">
              {room.deviceCount} {room.deviceCount === 1 ? 'device' : 'devices'}
            </p>
          )}
          {s ? (
            <>
              <p className="mt-0.5 text-[16px] font-semibold leading-snug text-white">
                {isTV ? (
                  <>
                    {s.grandparentTitle}
                    <span className="ml-1.5 font-normal text-white/40">
                      S{s.parentIndex?.padStart(2, '0')}E{s.index?.padStart(2, '0')}
                    </span>
                  </>
                ) : (
                  <>
                    {s.title}
                    {s.year && (
                      <span className="ml-1.5 font-normal text-white/40">({s.year})</span>
                    )}
                  </>
                )}
              </p>
              <div className="mt-1.5 flex items-center gap-2">
                <span className={cn('inline-block h-2 w-2 rounded-full', isPlaying ? 'bg-green-500' : 'bg-amber-500')} />
                <span className={cn('text-[13px]', isPlaying ? 'text-green-400' : 'text-amber-400')}>
                  {isPlaying ? 'Playing' : 'Paused'}
                </span>
              </div>
            </>
          ) : a ? (
            <>
              {activityTitle && (
                <p className="mt-0.5 text-[16px] font-semibold leading-snug text-white">
                  {activityTitle}
                  {a.year && !liveNoArt && (
                    <span className="ml-1.5 font-normal text-white/40">({a.year})</span>
                  )}
                </p>
              )}
              <div className="mt-1.5 flex items-center gap-2">
                <span className={cn('inline-block h-2 w-2 rounded-full', isPlaying ? appColors!.dot : 'bg-amber-500')} />
                <span className={cn('text-[13px]', isPlaying ? 'text-green-400' : 'text-amber-400')}>
                  {isPlaying ? 'Playing' : 'Paused'}
                </span>
              </div>
            </>
          ) : (
            <p className="mt-1 text-[14px] text-white/30">Nothing Playing</p>
          )}
          {hasPlayback && (
            <>
              <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-white/10">
                <div
                  className={cn(
                    'h-full rounded-full transition-[width] duration-500',
                    s ? (isPlaying ? 'bg-green-500' : 'bg-amber-500')
                      : (isPlaying ? (appColors?.bar ?? 'bg-green-500') : 'bg-amber-500'),
                  )}
                  style={{ width: `${pct}%` }}
                />
              </div>
              {timeTotal > 0 && (
                <p className="mt-1 text-[12px] text-white/40">
                  {formatDuration(displayOffset)} / {formatDuration(timeTotal)}
                </p>
              )}
            </>
          )}
        </div>
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={cn('shrink-0 text-white/30 transition-transform', expanded && 'rotate-180')}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {expanded && (
        <div className="border-t border-white/8 px-4 py-3">
          {needsDevicePicker ? (
            <div className="flex flex-col gap-2">
              <p className="text-[12px] text-white/40 text-center mb-1">Select device to control</p>
              {roomDevices!.map((dev) => (
                <button
                  key={dev.id}
                  type="button"
                  onClick={() => onOpenRemote(dev.id)}
                  className="flex w-full items-center justify-center gap-2.5 rounded-xl py-3 text-[14px] font-medium transition-all duration-150 active:scale-[0.98]"
                  style={remoteButtonStyle}
                >
                  {remoteIcon}
                  {dev.name}
                </button>
              ))}
            </div>
          ) : (
            <button
              type="button"
              onClick={() => {
                // v294: never silent no-op. Prefer activity/session resolve, else first
                // configured device, else still open for Harmony (empty deviceId — backend
                // falls back to devices[0] / hub fields). No devices + no Hub → toast.
                const devId = autoDeviceId || roomDevices?.[0]?.id || '';
                if (devId || hasHub) {
                  onOpenRemote(devId);
                } else {
                  onNeedDevice();
                }
              }}
              className="flex w-full items-center justify-center gap-2.5 rounded-xl py-3.5 text-[15px] font-medium transition-all duration-150 active:scale-[0.98]"
              style={remoteButtonStyle}
            >
              {remoteIcon}
              Open Remote
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// UnmatchedSessionCard
// ---------------------------------------------------------------------------

function UnmatchedSessionCard({ session }: { session: SessionData }) {
  const isTV = !!session.grandparentTitle;
  const plexThumb = isTV ? (session.grandparentThumb || session.thumb) : session.thumb;
  const poster = plexThumb ? apiUrl(`/api/img/plex?url=${encodeURIComponent(plexThumb)}`) : null;
  const isPlaying = session.state === 'playing';
  const pct = session.duration > 0 ? (session.viewOffset / session.duration) * 100 : 0;

  return (
    <div className="flex items-center gap-4 rounded-2xl bg-white/5 p-4">
      <div className="h-[90px] w-[60px] shrink-0 overflow-hidden rounded-lg bg-white/5">
        {poster ? (
          <FadeImg src={poster} alt="" className="h-full w-full object-cover" />
        ) : (
          <PosterPlaceholder />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-[14px] font-medium leading-snug text-blue-400">
          {session.userName || 'Unknown'}
        </p>
        <p className="mt-0.5 text-[16px] font-semibold leading-snug text-white">
          {isTV ? (
            <>
              {session.grandparentTitle}
              <span className="ml-1.5 font-normal text-white/40">
                S{session.parentIndex?.padStart(2, '0')}E{session.index?.padStart(2, '0')}
              </span>
            </>
          ) : (
            <>
              {session.title}
              {session.year && (
                <span className="ml-1.5 font-normal text-white/40">({session.year})</span>
              )}
            </>
          )}
        </p>
        <div className="mt-1.5 flex items-center gap-2">
          <span className={cn('inline-block h-2 w-2 rounded-full', isPlaying ? 'bg-green-500' : 'bg-amber-500')} />
          <span className={cn('text-[13px]', isPlaying ? 'text-green-400' : 'text-amber-400')}>
            {isPlaying ? 'Playing' : 'Paused'}
          </span>
        </div>
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-white/10">
          <div
            className={cn('h-full rounded-full', isPlaying ? 'bg-green-500' : 'bg-amber-500')}
            style={{ width: `${pct}%` }}
          />
        </div>
        {session.duration > 0 && (
          <p className="mt-1 text-[12px] text-white/40">
            {formatDuration(session.viewOffset)} / {formatDuration(session.duration)}
          </p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// RoomsSheet — bottom sheet portal
// Mirrors ActivitySheet chrome: near-full height, phase enter/exit, grab-handle
// drag-to-dismiss, tap-outside to close. Rooms content unchanged.
// ---------------------------------------------------------------------------

interface RoomsSheetProps {
  open: boolean;
  onClose: () => void;
}

/** The last device config (this session): a reopened sheet renders its rooms on frame 0. */
let deviceCache: { map: Record<string, DeviceData[]>; hubs: Record<string, string> } | null = null;
export function _clearRoomsCache() { deviceCache = null; }

export function RoomsSheet({ open, onClose }: RoomsSheetProps) {
  const { rooms, roomsLoading, unmatchedSessions } = useNotificationsContext();
  const [expandedRoom, setExpandedRoom] = useState<string | null>(null);
  const [remoteTarget, setRemoteTarget] = useState<{ roomId: string; deviceId: string } | null>(null);
  const [deviceMap, setDeviceMap] = useState<Record<string, DeviceData[]>>(() => deviceCache?.map ?? {});
  const [hubMap, setHubMap] = useState<Record<string, string>>(() => deviceCache?.hubs ?? {});
  const [devicesLoaded, setDevicesLoaded] = useState(() => !!deviceCache);
  const [deviceNotice, setDeviceNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!deviceNotice) return;
    const t = window.setTimeout(() => setDeviceNotice(null), 3200);
    return () => window.clearTimeout(t);
  }, [deviceNotice]);

  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });
  const [dragOffset, setDragOffset] = useState(0);
  const [phase, setPhase] = useState<'entering' | 'visible' | 'exiting' | 'gone'>('gone');

  useEffect(() => {
    if (open) {
      setPhase('entering');
      setDragOffset(0);
      requestAnimationFrame(() => {
        requestAnimationFrame(() => setPhase('visible'));
      });
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prevOverflow; };
  }, [open]);

  // Fetch device config when sheet opens
  useEffect(() => {
    if (!open) return;
    fetchRooms()
      .then((data) => {
        const map: Record<string, DeviceData[]> = {};
        const hubs: Record<string, string> = {};
        for (const r of data.rooms) {
          map[r.id] = r.devices || [];
          hubs[r.id] = (r.harmonyHubIp || '').trim();
        }
        deviceCache = { map, hubs };
        setDeviceMap(map);
        setHubMap(hubs);
      })
      .catch(() => {})
      .finally(() => setDevicesLoaded(true));
  }, [open]);

  const toggleRoom = useCallback((roomId: string) => {
    setExpandedRoom((prev) => (prev === roomId ? null : roomId));
  }, []);

  // v294: deviceId may be '' for Harmony rooms (backend falls back to devices[0] / hub).
  const openRemote = useCallback((room: Room, deviceId: string) => {
    setRemoteTarget({ roomId: room.roomId, deviceId });
  }, []);

  const handleDismiss = useCallback(() => {
    // If remote is open, close it first instead of the whole sheet
    if (remoteTarget) {
      setRemoteTarget(null);
      setDragOffset(0);
      return;
    }
    setDragOffset(0);
    requestAnimationFrame(() => {
      setPhase('exiting');
      setTimeout(() => {
        setPhase('gone');
        setExpandedRoom(null);
        setRemoteTarget(null);
        onClose();
      }, EXIT_DURATION);
    });
  }, [onClose, remoteTarget]);

  // ESC to close (capture phase)
  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation();
        handleDismiss();
      }
    }
    window.addEventListener('keydown', handleKey, true);
    return () => window.removeEventListener('keydown', handleKey, true);
  }, [open, handleDismiss]);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest('[data-sheet-handle]') && e.touches.length > 0) {
      dragRef.current = { active: true, startY: e.touches[0]!.clientY, currentY: e.touches[0]!.clientY };
    }
  }, []);
  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragRef.current.active || !e.touches.length) return;
    dragRef.current.currentY = e.touches[0]!.clientY;
    setDragOffset(Math.max(0, dragRef.current.currentY - dragRef.current.startY));
  }, []);
  const onTouchEnd = useCallback(() => {
    if (!dragRef.current.active) return;
    const dy = dragRef.current.currentY - dragRef.current.startY;
    dragRef.current.active = false;
    if (dy > DISMISS_THRESHOLD) handleDismiss();
    else setDragOffset(0);
  }, [handleDismiss]);

  if (phase === 'gone' && !open) return null;

  const sheetTransform =
    dragOffset > 0 ? `translate3d(0, ${dragOffset}px, 0)`
      : phase === 'visible' ? 'translate3d(0, 0, 0)'
        : 'translate3d(0, 100%, 0)';

  const sheetTransition =
    dragOffset > 0 ? 'none'
      : phase === 'exiting' ? `transform ${EXIT_DURATION}ms cubic-bezier(0.4, 0, 1, 1)`
        : `transform ${ENTER_DURATION}ms ${SPRING}`;

  return createPortal(
    <>
      <div className="luma-fullscreen luma-bleed"
        data-sheet="RoomsSheet"
        style={{
          position: 'fixed', inset: 0,
          zIndex: 10001,
          display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
          backgroundColor: phase === 'visible' ? 'rgba(0,0,0,0.4)' : 'rgba(0,0,0,0)',
          transition: phase === 'exiting'
            ? `background-color ${EXIT_DURATION}ms ease`
            : `background-color ${ENTER_DURATION}ms ease`,
        }}
        onClick={(e) => { if (e.target === e.currentTarget) handleDismiss(); }}
      >
        <div className="luma-bleed-panel"
          onTouchStart={onTouchStart}
          onTouchMove={onTouchMove}
          onTouchEnd={onTouchEnd}
          style={{
            height: 'calc(100% - env(safe-area-inset-top, 0px) - 6px)',
            display: 'flex', flexDirection: 'column',
            background: 'var(--color-kiosk-black)',
            borderRadius: '20px 20px 0 0',
            overflow: 'hidden',
            transform: sheetTransform,
            transition: remoteTarget
              ? `${sheetTransition}, filter 0.3s ease`
              : sheetTransition,
            willChange: 'transform',
            backfaceVisibility: 'hidden',
            filter: remoteTarget ? 'brightness(0.15)' : 'none',
          }}
        >
          {/* Grab handle */}
          <div data-sheet-handle
            style={{ flexShrink: 0, display: 'flex', justifyContent: 'center', padding: '12px 0 8px', cursor: 'grab' }}
          >
            <div style={{ width: 40, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)' }} />
          </div>

          {/* Title — same chrome as Activity (h2 / 18px); Rooms label kept */}
          <div className="shrink-0 px-4 pb-3">
            <h2 className="text-[18px] font-bold text-white">Rooms</h2>
          </div>

          {/* Scrollable content */}
          <div
            data-scroll-container
            className="luma-scroll-y flex-1 px-3 pb-2"
            style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}
          >
            {/* One reveal once rooms and their devices are ready (no skeletons, no per-list pop-in). */}
            <ContentReveal id="rooms-sheet" landMs={ENTER_DURATION} ready={!roomsLoading && devicesLoaded} testId="rooms-reveal">
            {rooms.length === 0 ? (
              <p className="text-center text-[15px] text-white/40 py-12">
                No rooms configured
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                {rooms.map((room) => (
                  <RoomCard
                    key={room.roomId}
                    room={room}
                    expanded={expandedRoom === room.roomId}
                    onToggle={() => toggleRoom(room.roomId)}
                    onOpenRemote={(deviceId) => openRemote(room, deviceId)}
                    onNeedDevice={() => setDeviceNotice('Add a device in Settings.')}
                    roomDevices={deviceMap[room.roomId]}
                    hasHub={!!(hubMap[room.roomId] || '').trim()}
                  />
                ))}
              </div>
            )}

            {unmatchedSessions.length > 0 && (
              <div className="mt-6">
                <h2 className="text-[18px] font-bold mb-3 text-white/60">Friends & Family</h2>
                <div className="flex flex-col gap-3">
                  {unmatchedSessions.map((session) => (
                    <UnmatchedSessionCard key={session.ratingKey} session={session} />
                  ))}
                </div>
              </div>
            )}
            </ContentReveal>

            <div className="shrink-0" style={{ height: 'env(safe-area-inset-bottom, 12px)' }} />
          </div>
        </div>
      </div>

      {deviceNotice && (
        <div
          role="status"
          data-testid="rooms-device-notice"
          className="fixed left-1/2 z-[10003] -translate-x-1/2 rounded-xl border border-white/10 bg-[rgba(21,16,42,0.96)] px-4 py-2.5 text-[13px] text-white/90 shadow-lg"
          style={{ top: 'calc(env(safe-area-inset-top, 0px) + 12px)', width: 'min(420px, calc(100vw - 24px))' }}
        >
          {deviceNotice}
        </div>
      )}

      {/* FullScreenRemote layers above at z-10002 */}
      {remoteTarget && (() => {
        const liveRoom = rooms.find((r) => r.roomId === remoteTarget.roomId);
        return liveRoom ? (
          <FullScreenRemote
            room={liveRoom}
            deviceId={remoteTarget.deviceId}
            harmonyHubIp={hubMap[remoteTarget.roomId]}
            onClose={() => setRemoteTarget(null)}
          />
        ) : null;
      })()}
    </>,
    document.body,
  );
}
