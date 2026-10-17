import { useState, useEffect, useRef, useCallback } from 'react';
import { Link, useLocation } from 'react-router';
import { cn } from '@/utils/cn';
import { type NotificationItem, type DownloadItem, type Room } from '@/hooks/useNotifications';
import { useNotificationsContext } from '@/contexts/NotificationsContext';
import { useInterpolatedProgress } from '@/hooks/useInterpolatedProgress';
import { useModalHost } from '@/contexts/ModalHostContext';
import { useUiDensity } from '@/hooks/useUiDensity';
import { uiDensityTokens } from '@/ui/uiDensityTokens';
import { posterUrl } from '@/utils/image';
import { useFocusable } from '@/hooks/useFocusable';
import { FocusZone } from '@/components/tv/FocusZone';
import { apiFetch } from '../../apiClient';
import { apiUrl } from '@/urlHelpers';
import { useVoice } from '@/contexts/VoiceContext';
import { NAVY_GLASS_BORDER, NAVY_GLASS_POPOVER } from '@/theme/tokens';

const NAV_ITEMS = [
  { label: 'Explore', to: '/explore' },
  { label: 'AI', to: null as string | null },   // Opens the Vibe Engine (AI discovery) modal
  { label: 'Library', to: '/browse' },           // Browse the actual Plex library
  { label: 'Watchlist', to: '/watchlist' },
] as const;

type NavItemType = (typeof NAV_ITEMS)[number];

function NavItem({ item, isActive }: { item: NavItemType; isActive: boolean }) {
  const { ref, focusProps } = useFocusable({
    focusKey: `topbar-${item.label.toLowerCase()}`,
    zoneId: 'topbar-nav',
  });
  const { openVibeEngine } = useModalHost();
  const d = useUiDensity();
  const tk = uiDensityTokens(d);

  const className = cn(
    'flex items-center font-medium transition-colors hover:text-white',
    tk.nav.itemMinH,
    tk.typography.navText,
    isActive ? 'text-white' : 'text-white/85',
    focusProps.className,
  );

  // Library opens as a modal instead of navigating
  if (!item.to) {
    return (
      <button
        ref={ref as React.RefCallback<HTMLButtonElement>}
        type="button"
        onClick={openVibeEngine}
        data-focus-key={focusProps['data-focus-key']}
        tabIndex={focusProps.tabIndex}
        className={className}
      >
        {item.label}
      </button>
    );
  }

  return (
    <Link
      ref={ref as React.RefCallback<HTMLAnchorElement>}
      to={item.to}
      data-focus-key={focusProps['data-focus-key']}
      tabIndex={focusProps.tabIndex}
      className={className}
    >
      {item.label}
    </Link>
  );
}

function formatRelativeTime(timestamp: number): string {
  const now = Date.now() / 1000;
  const diff = now - timestamp;

  if (diff < 60) return 'Just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;

  return new Date(timestamp * 1000).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  });
}

// ---------------------------------------------------------------------------
// Swipe-to-dismiss wrapper
// ---------------------------------------------------------------------------
const DISMISS_THRESHOLD = 120; // px drag before auto-dismiss
const DISMISS_ANIM_MS = 300;

function SwipeDismissRow({
  children,
  onDismiss,
  onClick,
  className,
}: {
  children: React.ReactNode;
  onDismiss: () => void;
  onClick?: () => void;
  className?: string;
}) {
  const rowRef = useRef<HTMLLIElement>(null);
  const startX = useRef(0);
  const currentX = useRef(0);
  const dragging = useRef(false);
  const dismissed = useRef(false);

  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    if (dismissed.current) return;
    dragging.current = true;
    startX.current = e.clientX;
    currentX.current = 0;
    rowRef.current?.setPointerCapture(e.pointerId);
  }, []);

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    if (!dragging.current || dismissed.current) return;
    const dx = e.clientX - startX.current;
    currentX.current = dx;
    if (rowRef.current) {
      // Only allow leftward drag (negative dx), or slight rightward snap
      const clamped = Math.min(dx, 10);
      rowRef.current.style.transform = `translateX(${clamped}px)`;
      rowRef.current.style.opacity = String(Math.max(0, 1 - Math.abs(clamped) / 300));
    }
  }, []);

  const handlePointerEnd = useCallback(() => {
    if (!dragging.current || dismissed.current) return;
    dragging.current = false;
    const el = rowRef.current;
    if (!el) return;

    if (currentX.current < -DISMISS_THRESHOLD) {
      // Dismiss — animate out
      dismissed.current = true;
      el.style.transition = `transform ${DISMISS_ANIM_MS}ms ease-out, opacity ${DISMISS_ANIM_MS}ms ease-out`;
      el.style.transform = 'translateX(-110%)';
      el.style.opacity = '0';
      setTimeout(onDismiss, DISMISS_ANIM_MS);
    } else {
      // Snap back
      el.style.transition = 'transform 200ms ease-out, opacity 200ms ease-out';
      el.style.transform = 'translateX(0)';
      el.style.opacity = '1';
    }
  }, [onDismiss]);

  const handleClick = useCallback(() => {
    // Only fire click if we didn't drag significantly
    if (Math.abs(currentX.current) < 8 && onClick) {
      onClick();
    }
  }, [onClick]);

  return (
    <li
      ref={rowRef}
      className={className}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerEnd}
      onPointerCancel={handlePointerEnd}
      onClick={handleClick}
      style={{ touchAction: 'pan-y', willChange: 'transform' }}
    >
      {children}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Poster placeholder SVG (shared)
// ---------------------------------------------------------------------------
function PosterPlaceholder() {
  return (
    <div className="flex h-full w-full items-center justify-center text-white/20">
      <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2" y="2" width="20" height="20" rx="2" /><circle cx="8" cy="8" r="2" /><path d="m21 15-5-5L5 21" /></svg>
    </div>
  );
}

function fmtGb(bytes: number): string {
  if (!bytes) return '—';
  const gb = bytes / (1024 ** 3);
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${(bytes / (1024 ** 2)).toFixed(0)} MB`;
}

function fmtSpeed(bps: number): string {
  const mb = bps / (1024 ** 2);
  if (mb >= 1) return `${mb.toFixed(1)} MB/s`;
  return `${Math.max(0, Math.round(bps / 1024))} KB/s`;
}

function downloadStatusLabel(dl: DownloadItem): { text: string; color: string } {
  const pct = Math.round(dl.progress * 100);
  switch (dl.status) {
    case 'copying':
      return { text: `Adding to library... ${pct}%`, color: 'text-green-400' };
    case 'importing':
      return { text: 'Processing...', color: 'text-green-400' };
    case 'warning':
    case 'stalled':
      return { text: 'Stalled', color: 'text-amber-400' };
    case 'queued':
      return { text: 'Queued', color: 'text-white/50' };
    case 'paused':
      return { text: 'Paused', color: 'text-white/50' };
    case 'failed':
      return { text: 'Failed', color: 'text-red-400' };
    default:
      // Download complete but status hasn't transitioned yet — show Processing
      if (pct >= 100) return { text: 'Processing...', color: 'text-green-400' };
      return { text: `Downloading... ${pct}%`, color: 'text-blue-400' };
  }
}

function DownloadRow({ dl, onRemove }: { dl: DownloadItem; onRemove?: (id: number) => void }) {
  const poster = posterUrl(dl.posterPath, 'w185');
  const pct = Math.round(dl.progress * 100);
  const { text: statusText, color: statusColor } = downloadStatusLabel(dl);
  // Hide bar during processing state (importing or download-complete-but-not-yet-copying)
  const showBar = !(dl.status === 'importing' || (dl.progress >= 1.0 && dl.status !== 'copying'));
  const barColor = dl.status === 'copying' ? 'bg-green-500' : (dl.status === 'warning' || dl.status === 'stalled') ? 'bg-amber-500' : dl.status === 'failed' ? 'bg-red-500' : 'bg-blue-500';

  return (
    <li className="group relative flex items-center gap-5 rounded-xl px-4 py-3">
      {dl.queueId != null && onRemove && (
        <button
          onClick={(e) => { e.stopPropagation(); onRemove(dl.queueId!); }}
          className="absolute right-3 top-3 flex h-7 w-7 items-center justify-center rounded-full bg-white/10 text-white/40 opacity-0 transition-opacity hover:bg-white/20 hover:text-white group-hover:opacity-100"
          aria-label="Remove from queue"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
        </button>
      )}
      <div className="h-[100px] w-[67px] shrink-0 overflow-hidden rounded-lg bg-white/5">
        {poster ? (
          <img src={poster} alt="" className="h-full w-full object-cover" />
        ) : (
          <PosterPlaceholder />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-[18px] font-semibold leading-snug text-white">
          {dl.title}
          {dl.year > 0 && (
            <span className="ml-2 font-normal text-white/40">({dl.year})</span>
          )}
        </p>
        <p className={`mt-1.5 text-[15px] leading-snug ${statusColor}`}>
          {statusText}
        </p>
        {showBar && (
          <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-white/10">
            <div
              className={`h-full rounded-full ${barColor}${pct < 100 ? ' transition-[width] duration-500' : ''}`}
              style={{ width: `${pct}%` }}
            />
          </div>
        )}
        {dl.sizeTotal > 0 && (
          <p className="mt-1 text-[13px] text-white/40 tabular-nums">
            {fmtGb(dl.sizeTotal - dl.sizeRemaining)} / {fmtGb(dl.sizeTotal)}
            {dl.status === 'downloading' && dl.speed > 0 && <span> · ↓ {fmtSpeed(dl.speed)}</span>}
            {dl.eta && dl.status === 'downloading' && <span> · {dl.eta} left</span>}
          </p>
        )}
      </div>
    </li>
  );
}

function formatDuration(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function sendRoomCommand(roomId: string, command: string, deviceId?: string) {
  apiFetch(`/api/room/${roomId}/remote`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command, ...(deviceId ? { device_id: deviceId } : {}) }),
  }).catch(() => {});
}

function RemoteButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      className="flex h-14 w-14 items-center justify-center rounded-full bg-white/10 text-white/60 transition-colors hover:bg-white/20 hover:text-white active:bg-white/30"
      aria-label={label}
    >
      {children}
    </button>
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
  Spotify: { badge: 'bg-green-600 text-white', dot: 'bg-green-500', bar: 'bg-green-500' },
  Hulu: { badge: 'bg-green-700 text-white', dot: 'bg-green-600', bar: 'bg-green-600' },
};
const DEFAULT_APP_COLOR = { badge: 'bg-white/20 text-white', dot: 'bg-green-500', bar: 'bg-green-500' };

function RoomRow({
  room,
  expanded,
  onToggle,
}: {
  room: Room;
  expanded: boolean;
  onToggle: () => void;
}) {
  const s = room.session;
  const a = room.activity;
  const hasPlayback = !!s || !!a;
  const isTV = !!s?.grandparentTitle;
  const plexThumb = s ? (isTV ? (s.grandparentThumb || s.thumb) : s.thumb) : null;
  const poster = plexThumb
    ? apiUrl(`/api/img/plex?url=${encodeURIComponent(plexThumb)}`)
    : a?.thumb || null;
  const isPlaying = s ? s.state === 'playing' : a?.state === 'playing';
  const send = (cmd: string) => sendRoomCommand(room.roomId, cmd, a?.deviceId);

  // Progress: interpolate locally so timer ticks every second
  const serverOffset = s ? s.viewOffset : (a?.position ?? 0);
  const timeTotal = s ? s.duration : (a?.duration ?? 0);
  const { displayOffset: timeElapsed, pct } = useInterpolatedProgress(serverOffset, timeTotal, !!isPlaying);

  const appColors = a ? (APP_COLORS[a.appName] ?? DEFAULT_APP_COLOR) : null;

  return (
    <li>
      {/* Clickable room header */}
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-5 rounded-xl px-5 py-7 text-left transition-colors hover:bg-white/5 active:bg-white/8"
      >
        {(s || poster) ? (
          <div className="h-[100px] w-[67px] shrink-0 overflow-hidden rounded-lg bg-white/5">
            {poster ? (
              <img src={poster} alt="" className="h-full w-full object-cover" />
            ) : (
              <PosterPlaceholder />
            )}
          </div>
        ) : (
          <div className="flex h-[56px] w-[56px] shrink-0 items-center justify-center rounded-xl bg-white/5 text-white/20">
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="7" width="20" height="15" rx="2" ry="2" /><polyline points="17 2 12 7 7 2" /></svg>
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-medium leading-snug text-blue-400">
            {room.roomName}
            {s && <span className="text-white/30"> · </span>}
            {s && <span>Plex</span>}
            {!s && a && <span className="text-white/30"> · </span>}
            {!s && a && <span>{a.appName}</span>}
          </p>
          {s ? (
            <>
              <p className="mt-0.5 text-[18px] font-semibold leading-snug text-white">
                {isTV ? (
                  <>
                    {s.grandparentTitle}
                    <span className="ml-2 font-normal text-white/40">
                      S{s.parentIndex?.padStart(2, '0')}E{s.index?.padStart(2, '0')}
                    </span>
                  </>
                ) : (
                  <>
                    {s.title}
                    {s.year && (
                      <span className="ml-2 font-normal text-white/40">({s.year})</span>
                    )}
                  </>
                )}
              </p>
              <div className="mt-1.5 flex items-center gap-2">
                <span className={cn('inline-block h-2 w-2 rounded-full', isPlaying ? 'bg-green-500' : 'bg-amber-500')} />
                <span className={cn('text-[14px]', isPlaying ? 'text-green-400' : 'text-amber-400')}>
                  {isPlaying ? 'Playing' : 'Paused'}
                </span>
              </div>
            </>
          ) : a ? (
            <>
              {a.title && (
                <p className="mt-0.5 text-[18px] font-semibold leading-snug text-white">{a.title}</p>
              )}
              <div className="mt-1.5 flex items-center gap-2">
                <span className={cn('inline-block h-2 w-2 rounded-full', isPlaying ? appColors!.dot : 'bg-amber-500')} />
                <span className={cn('text-[14px]', isPlaying ? 'text-green-400' : 'text-amber-400')}>
                  {isPlaying ? 'Playing' : 'Paused'}
                </span>
              </div>
            </>
          ) : (
            <p className="mt-1 text-[15px] text-white/30">Nothing Playing</p>
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
                <p className="mt-1 text-[13px] text-white/40">
                  {formatDuration(timeElapsed)} / {formatDuration(timeTotal)}
                </p>
              )}
            </>
          )}
        </div>
        {/* Expand chevron */}
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

      {/* Expanded controls */}
      {expanded && (
        <div className="flex items-center justify-center gap-5 px-5 pb-5 pt-2">
          <RemoteButton label="Power" onClick={() => send('power')}>
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18.36 6.64a9 9 0 1 1-12.73 0" /><line x1="12" y1="2" x2="12" y2="12" /></svg>
          </RemoteButton>
          <RemoteButton label="Volume down" onClick={() => send('vol_down')}>
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" /><line x1="23" y1="12" x2="17" y2="12" /></svg>
          </RemoteButton>
          <RemoteButton label="Play" onClick={() => send('play')}>
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3" /></svg>
          </RemoteButton>
          <RemoteButton label="Pause" onClick={() => send('pause')}>
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="6" y="4" width="4" height="16" /><rect x="14" y="4" width="4" height="16" /></svg>
          </RemoteButton>
          <RemoteButton label="Volume up" onClick={() => send('vol_up')}>
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" /><line x1="17" y1="9" x2="17" y2="15" /><line x1="20" y1="12" x2="14" y2="12" /></svg>
          </RemoteButton>
          <RemoteButton label="Stop" onClick={() => send('stop')}>
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="4" y="4" width="16" height="16" rx="2" /></svg>
          </RemoteButton>
        </div>
      )}
    </li>
  );
}

export function TopBar() {
  const [time, setTime] = useState(new Date());
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const location = useLocation();
  const { count, items, downloads, rooms, markSeen, clearItems, removeDownload } = useNotificationsContext();
  const [expandedRoom, setExpandedRoom] = useState<string | null>(null);
  const [roomsOpen, setRoomsOpen] = useState(false);
  useVoice(); // keep context active for wake word
  const dropdownRef = useRef<HTMLDivElement>(null);
  const { openMovie, openTv } = useModalHost();
  const d = useUiDensity();
  const tk = uiDensityTokens(d);

  // Filter out individually dismissed items
  const visibleItems = items.filter(
    (item) => !dismissed.has(`${item.tmdbTitle}-${item.completedTimestamp}`),
  );

  useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  // Close dropdown on click outside
  useEffect(() => {
    if (!dropdownOpen) return;
    function handleClick(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    }
    document.addEventListener('pointerdown', handleClick);
    return () => document.removeEventListener('pointerdown', handleClick);
  }, [dropdownOpen]);

  // When all items are dismissed individually, mark-seen on the backend
  useEffect(() => {
    if (items.length > 0 && visibleItems.length === 0 && dismissed.size > 0) {
      markSeen();
      clearItems();
      setDismissed(new Set());
    }
  }, [items.length, visibleItems.length, dismissed.size, markSeen, clearItems]);

  const formattedTime = time.toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  });

  const handleBellClick = () => {
    setDropdownOpen((prev) => !prev);
  };

  const handleDismissItem = useCallback((item: NotificationItem) => {
    setDismissed((prev) => {
      const next = new Set(prev);
      next.add(`${item.tmdbTitle}-${item.completedTimestamp}`);
      return next;
    });
  }, []);

  const handleItemClick = useCallback((item: NotificationItem) => {
    if (!item.tmdbId) return;
    setDropdownOpen(false);

    // TV items: open directly with season/episode context
    if (item.mediaType === 'tv') {
      // Single episode → open in episode detail mode
      if (item.episodeNumber != null && item.seasonNumber != null) {
        openTv({
          tmdbId: item.tmdbId,
          origin: 'notification',
          episodeContext: { seasonNumber: item.seasonNumber, episodeNumber: item.episodeNumber },
        });
      } else {
        // Full season or unknown → open to season view
        openTv({ tmdbId: item.tmdbId, origin: 'notification', initialSeason: item.seasonNumber ?? undefined });
      }
      return;
    }

    // Movies: open immediately with tmdbId — modal fetches ownership itself
    openMovie({ tmdbId: item.tmdbId, origin: 'notification' });
  }, [openMovie, openTv]);

  const { ref: bellRef, focusProps: bellFocusProps } = useFocusable({
    focusKey: 'topbar-bell',
    zoneId: 'topbar-nav',
    onEnter: handleBellClick,
  });

  const visibleCount = visibleItems.length;
  const activeSessions = rooms.filter((r) => r.session != null || r.activity != null).length;
  const totalBadge = activeSessions + downloads.length + Math.min(visibleCount, count);
  const badgeText = totalBadge > 9 ? '9+' : totalBadge > 0 ? String(totalBadge) : null;
  const hasContent = downloads.length > 0 || visibleItems.length > 0;

  return (
    <header
      data-topbar
      className={cn(
        'fixed inset-x-0 top-0 z-50 flex items-center justify-between',
        'bg-gradient-to-b from-black/80 to-transparent',
        tk.spacing.headerPadX,
        'py-6',
      )}
    >
      <FocusZone zoneId="topbar-nav" orientation="horizontal">
        <nav data-topbar-nav className={cn('flex items-center', tk.spacing.navGap)}>
          <button
            type="button"
            onClick={() => window.dispatchEvent(new CustomEvent('open-search'))}
            aria-label="Open search"
            className={cn(
              'flex items-center justify-center font-medium transition-colors text-white/85 hover:text-white',
              tk.nav.itemMinH,
            )}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="34"
              height="34"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.75"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="11" cy="11" r="8" />
              <path d="M21 21l-4.35-4.35" />
            </svg>
          </button>
          {NAV_ITEMS.map((item) => {
            const isActive = location.pathname === item.to;
            return (
              <NavItem key={item.label} item={item} isActive={isActive} />
            );
          })}
        </nav>
      </FocusZone>

      <div className={cn('flex items-center', tk.spacing.actionsGap)}>
        <time className={cn(tk.typography.navText, 'font-light tabular-nums text-white/85')}>
          {formattedTime}
        </time>

        <div className="relative" ref={dropdownRef}>
          <button
            ref={bellRef as React.RefCallback<HTMLButtonElement>}
            type="button"
            onClick={handleBellClick}
            data-focus-key={bellFocusProps['data-focus-key']}
            tabIndex={bellFocusProps.tabIndex}
            className={cn(
              'flex min-h-[64px] min-w-[64px] items-center justify-center rounded-full text-white/60 transition-colors hover:bg-glass-10 hover:text-white/85',
              bellFocusProps.className,
            )}
            aria-label={totalBadge > 0 ? `${totalBadge} new notifications` : 'Notifications'}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="34"
              height="34"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
              <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
            </svg>

            {badgeText && (
              <span className="absolute -top-0.5 -right-0.5 flex h-[22px] min-w-[22px] items-center justify-center rounded-full bg-blue-500 px-1 text-[12px] font-bold text-white">
                {badgeText}
              </span>
            )}
          </button>

          {dropdownOpen && (
            <div data-testid="notifications-popover"
              className="absolute right-0 top-full mt-3 w-[520px] overflow-hidden rounded-2xl shadow-2xl backdrop-blur-xl"
              style={{ background: NAVY_GLASS_POPOVER, border: NAVY_GLASS_BORDER }}>
              {/* Rooms collapsible section */}
              {rooms.length > 0 && (
                <div className="border-b border-white/8">
                  <button
                    type="button"
                    onClick={() => setRoomsOpen((p) => !p)}
                    className="flex w-full items-center justify-between px-6 py-5 text-left transition-colors hover:bg-white/5"
                  >
                    <div className="flex items-center gap-3">
                      <span className="text-[15px] font-semibold text-white/70">Rooms</span>
                      {activeSessions > 0 && (
                        <span className="flex h-[20px] min-w-[20px] items-center justify-center rounded-full bg-green-500/20 px-1.5 text-[11px] font-bold text-green-400">
                          {activeSessions}
                        </span>
                      )}
                    </div>
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      width="16"
                      height="16"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      className={cn('text-white/30 transition-transform', roomsOpen && 'rotate-180')}
                    >
                      <polyline points="6 9 12 15 18 9" />
                    </svg>
                  </button>
                  {roomsOpen && (
                    <ul className="flex flex-col gap-1 px-2 pb-3">
                      {rooms.map((r) => (
                        <RoomRow
                          key={r.roomId}
                          room={r}
                          expanded={expandedRoom === r.roomId}
                          onToggle={() => setExpandedRoom((prev) => prev === r.roomId ? null : r.roomId)}
                        />
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {!hasContent ? (
                <p className="py-8 text-center text-[18px] text-white/40">
                  No new notifications
                </p>
              ) : (
                <div>
                  <div className="flex items-center px-6 py-3">
                    <span className="text-[15px] font-semibold text-white/70">Notifications</span>
                  </div>
                <ul className="flex max-h-[540px] flex-col gap-4 overflow-y-auto overflow-x-hidden px-6 pb-4">
                  {/* Active downloads */}
                  {downloads.map((dl, i) => (
                    <DownloadRow key={`dl-${dl.tmdbId ?? dl.title}-${i}`} dl={dl} onRemove={removeDownload} />
                  ))}

                  {/* Divider between downloads and notifications */}
                  {downloads.length > 0 && visibleItems.length > 0 && (
                    <li className="border-t border-white/8" aria-hidden />
                  )}

                  {/* Library notifications — swipeable */}
                  {visibleItems.map((item) => {
                    const poster = posterUrl(item.posterPath, 'w185');
                    return (
                      <SwipeDismissRow
                        key={`${item.tmdbTitle}-${item.completedTimestamp}`}
                        onDismiss={() => handleDismissItem(item)}
                        onClick={() => handleItemClick(item)}
                        className={cn(
                          'flex items-center gap-5 rounded-xl px-4 py-3 transition-colors select-none',
                          item.tmdbId
                            ? 'cursor-pointer hover:bg-white/8 active:bg-white/12'
                            : '',
                        )}
                      >
                        <div className="h-[100px] w-[67px] shrink-0 overflow-hidden rounded-lg bg-white/5">
                          {poster ? (
                            <img
                              src={poster}
                              alt=""
                              className="pointer-events-none h-full w-full object-cover"
                              draggable={false}
                            />
                          ) : (
                            <PosterPlaceholder />
                          )}
                        </div>

                        <div className="min-w-0 flex-1">
                          <p className="text-[18px] font-semibold leading-snug text-white">
                            {item.tmdbTitle}
                            {item.mediaType !== 'tv' && item.tmdbYear && (
                              <span className="ml-2 font-normal text-white/40">
                                ({item.tmdbYear})
                              </span>
                            )}
                          </p>
                          <p className="mt-1.5 text-[15px] leading-snug text-white/50">
                            was added to your{' '}
                            <span className="font-semibold text-white/70">
                              {item.libraryName}
                            </span>
                            {' '}library
                          </p>
                          <p className="mt-1 text-[13px] text-white/30">
                            {formatRelativeTime(item.completedTimestamp)}
                          </p>
                        </div>
                      </SwipeDismissRow>
                    );
                  })}
                </ul>

                {/* Clear All */}
                {visibleItems.length > 0 && (
                  <div className="border-t border-white/[0.06] px-6 py-3">
                    <button
                      type="button"
                      onClick={() => { markSeen(); clearItems(); setDismissed(new Set()); }}
                      className="w-full rounded-lg py-2.5 text-[14px] font-medium text-white/50 hover:text-white/80 hover:bg-white/5 transition-colors"
                    >
                      Clear All
                    </button>
                  </div>
                )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

    </header>
  );
}
