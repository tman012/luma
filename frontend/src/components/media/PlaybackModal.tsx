/**
 * PlaybackModal — multi-step playback flow.
 *
 * Steps:
 * 1. Library Selection (if movie exists in multiple Plex libraries)
 * 2. Room Selection (pick which device to play on)
 * 3. Device Selection (if room has 2+ devices)
 * 4. Resume Prompt (if a resume point exists)
 * 5. Playing Status (with polling for confirmation)
 *
 * Centered bounce dialog — not a bottom sheet.
 */

import { useRoomFirst } from '@/components/rooms/PlayIn';
import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import {
  fetchRooms,
  getResumeInfo,
  startPlayback,
  createPlayQueue,
  roomIcon,
  type Room,
  type Device,
  type Service,
  type PlaybackTarget,
  type ResumeInfo,
} from '@/services/playbackApi';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { assetUrl } from '@/urlHelpers';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface PlaybackModalProps {
  /** Plex rating key for the media item */
  ratingKey: number;
  /** Display title */
  title: string;
  /** Clearlogo URL for branding display */
  clearLogoUrl?: string | null;
  /** Backdrop URL for mobile library selector */
  backdropUrl?: string | null;
  /** Media type for display */
  mediaType?: 'movie' | 'tv';
  /** Library names this item exists in (e.g. ["Movies", "4K", "Full Rips"]) */
  libraries?: string[];
  /** Mapping from library name to its specific rating key */
  libraryKeys?: Record<string, number>;
  /** Shuffle mode — skip resume, create play queue before playback */
  shuffle?: boolean;
  /** Label shown during shuffle playback (e.g. "Shuffling Season 2...") */
  shuffleLabel?: string;
  /** Close callback */
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Step types
// ---------------------------------------------------------------------------

type Step =
  | { type: 'library' }
  | { type: 'room' }
  | { type: 'device'; room: Room }
  | { type: 'service'; room: Room; device: Device }
  | { type: 'resume'; info: ResumeInfo }
  | { type: 'playing'; roomName: string }
  | { type: 'success'; roomName: string }
  | { type: 'error'; message: string };

// ---------------------------------------------------------------------------
// Room icon SVGs (scaled up)
// ---------------------------------------------------------------------------

function RoomIcon({ name, size = 42 }: { name: string; size?: number }) {
  const iconType = roomIcon(name);

  switch (iconType) {
    case 'bed':
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M2 4v16" /><path d="M2 8h18a2 2 0 012 2v10" /><path d="M2 17h20" /><path d="M6 8v9" />
        </svg>
      );
    case 'film':
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18" />
          <line x1="7" y1="2" x2="7" y2="22" /><line x1="17" y1="2" x2="17" y2="22" />
          <line x1="2" y1="12" x2="22" y2="12" /><line x1="2" y1="7" x2="7" y2="7" />
          <line x1="2" y1="17" x2="7" y2="17" /><line x1="17" y1="7" x2="22" y2="7" />
          <line x1="17" y1="17" x2="22" y2="17" />
        </svg>
      );
    case 'laptop':
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
          <line x1="2" y1="20" x2="22" y2="20" />
        </svg>
      );
    default: // tv
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="2" y="7" width="20" height="15" rx="2" ry="2" />
          <polyline points="17 2 12 7 7 2" />
        </svg>
      );
  }
}

// ---------------------------------------------------------------------------
// Platform icon for device selection
// ---------------------------------------------------------------------------

function PlatformIcon({ platform, size = 28 }: { platform: string; size?: number }) {
  if (platform === 'chromecast_gtv') {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M2 16.1A5 5 0 0 1 5.9 20M2 12.05A9 9 0 0 1 9.95 20M2 8V6a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-6" />
        <line x1="2" y1="20" x2="2.01" y2="20" />
      </svg>
    );
  }
  if (platform === 'network') {
    // Network device icon (e.g. Kodi box, generic streamer)
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
        <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
        <line x1="6" y1="6" x2="6.01" y2="6" />
        <line x1="6" y1="18" x2="6.01" y2="18" />
      </svg>
    );
  }
  // Default: Android TV / Shield icon
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="7" width="20" height="15" rx="2" ry="2" />
      <polyline points="17 2 12 7 7 2" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Check if a room has any device with a host configured */
function roomHasAvailableDevice(room: Room): boolean {
  return room.devices.some((d) => !!d.host);
}

/** Format platform name for display */
function platformLabel(platform: string): string {
  switch (platform) {
    case 'android_tv': return 'Android TV';
    case 'chromecast_gtv': return 'Chromecast';
    case 'network': return 'Network';
    default: return platform;
  }
}


// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function PlaybackModal({
  ratingKey: initialRatingKey,
  title,
  clearLogoUrl,
  backdropUrl,
  libraries,
  libraryKeys,
  shuffle,
  shuffleLabel,
  onClose,
}: PlaybackModalProps) {
  const { mode } = useLayoutMode();
  const isMobile = mode === 'compact';
  const [step, setStep] = useState<Step>(() => {
    // If multiple libraries, start with library selection
    if (libraries && libraries.length > 1) {
      return { type: 'library' };
    }
    return { type: 'room' };
  });

  // The active rating key (may change after library selection)
  const [activeRatingKey, setActiveRatingKey] = useState(initialRatingKey);
  const [rooms, setRooms] = useState<Room[]>([]);
  const [defaultPlaybackTarget, setDefaultPlaybackTarget] = useState<PlaybackTarget>({ roomId: '', deviceId: '', serviceId: '' });
  const [roomsLoading, setRoomsLoading] = useState(true);
  const [selectedRoom, setSelectedRoom] = useState<Room | null>(null);
  const [selectedDevice, setSelectedDevice] = useState<Device | null>(null);
  const [selectedService, setSelectedService] = useState<Service | null>(null);
  const [resumeLoading, setResumeLoading] = useState(false);

  // Refs to avoid stale closures in resume/start-over handlers
  const selectedRoomRef = useRef<Room | null>(null);
  const selectedDeviceRef = useRef<Device | null>(null);
  const selectedServiceRef = useRef<Service | null>(null);
  useEffect(() => { selectedRoomRef.current = selectedRoom; }, [selectedRoom]);
  useEffect(() => { selectedDeviceRef.current = selectedDevice; }, [selectedDevice]);
  useEffect(() => { selectedServiceRef.current = selectedService; }, [selectedService]);

  // Fetch rooms on mount
  useEffect(() => {
    let cancelled = false;
    fetchRooms().then((data) => {
      if (!cancelled) {
        setRooms(data.rooms);
        setDefaultPlaybackTarget(data.defaultPlaybackTarget);
        setRoomsLoading(false);

        // Only auto-select if there is exactly one room with one device
        // v225 room first (Option A): one usable room plays at once with the 'Playing in' toast; several open
        // 'Play in' / 'Shuffle in' with the default room preselected; none shows prod's room step.
        if (step.type === 'room') {
          roomFirst.start(data.rooms.filter(roomHasAvailableDevice), data.defaultPlaybackTarget?.roomId);
        }
      }
    });
    return () => { cancelled = true; };
  }, []);


  // --- Library selection ---
  const handleLibrarySelect = useCallback((libraryName: string) => {
    const key = libraryKeys?.[libraryName] ?? initialRatingKey;
    setActiveRatingKey(key);
    setStep({ type: 'room' });
  }, [libraryKeys, initialRatingKey]);

  // --- Room selection ---
  const handleRoomSelect = useCallback(async (room: Room) => {
    if (!roomHasAvailableDevice(room)) return;
    setSelectedRoom(room);

    // Find available devices in this room
    const availableDevices = room.devices.filter((d) => !!d.host);

    const singleDevice = availableDevices.length === 1 ? availableDevices[0] : undefined;
    if (singleDevice) {
      // Single device — auto-select it, skip device step
      setSelectedDevice(singleDevice);
      await proceedAfterDeviceSelect(room, singleDevice);
    } else if (availableDevices.length > 1) {
      // Multiple devices — show device selection
      setStep({ type: 'device', room });
    }
  }, [activeRatingKey, shuffle]);
  const roomSelectRef = useRef(handleRoomSelect);
  roomSelectRef.current = handleRoomSelect;
  const roomFirst = useRoomFirst<Room>({ action: shuffle ? 'Shuffle' : 'Play', select: r => { void roomSelectRef.current(r); }, onCancel: () => onClose() });

  // --- Device selection ---
  const handleDeviceSelect = useCallback(async (room: Room, device: Device) => {
    if (!device.host) return;
    setSelectedDevice(device);
    await proceedAfterDeviceSelect(room, device);
  }, [activeRatingKey, shuffle]);

  // --- After device is selected (shared by auto-select and manual select) ---
  const proceedAfterDeviceSelect = useCallback(async (room: Room, device: Device) => {
    // If device has multiple services, show service selection
    const services = (device.services ?? []).filter((s) => !!s.id);
    if (services.length > 1) {
      setStep({ type: 'service', room, device });
      return;
    }
    // Single or no service — auto-select and continue
    const svc = services[0] ?? null;
    setSelectedService(svc);
    await proceedAfterServiceSelect(room, device, svc);
  }, [activeRatingKey, shuffle]);

  // --- Service selection ---
  const handleServiceSelect = useCallback(async (room: Room, device: Device, service: Service) => {
    setSelectedService(service);
    await proceedAfterServiceSelect(room, device, service);
  }, [activeRatingKey, shuffle]);

  // --- After service is selected ---
  const proceedAfterServiceSelect = useCallback(async (room: Room, device: Device, service: Service | null) => {
    // Shuffle mode: skip resume check, create play queue, start immediately
    if (shuffle) {
      setStep({ type: 'playing', roomName: room.name });
      try {
        const pq = await createPlayQueue(activeRatingKey, true);
        if (!pq.success || !pq.play_queue_id) {
          roomFirst.failed();
          setStep({ type: 'error', message: pq.error ?? 'Failed to create play queue' });
          return;
        }
        await initiatePlayback(room, device, 0, pq.play_queue_id, service);
      } catch {
        roomFirst.failed();
        setStep({ type: 'error', message: 'Network error creating play queue' });
      }
      return;
    }

    setResumeLoading(true);
    try {
      const info = await getResumeInfo(activeRatingKey);
      if (info.has_resume_point) {
        setStep({ type: 'resume', info });
      } else {
        // No resume point — start immediately
        await initiatePlayback(room, device, 0, undefined, service);
      }
    } catch {
      await initiatePlayback(room, device, 0, undefined, service);
    } finally {
      setResumeLoading(false);
    }
  }, [activeRatingKey, shuffle]);

  // --- Start playback ---
  const initiatePlayback = useCallback(async (room: Room, device: Device, offset: number, playQueueId?: number, service?: Service | null) => {
    const roomName = room.name;
    setStep({ type: 'playing', roomName });

    const serviceId = service?.id ?? device.services?.[0]?.id;

    try {
      const result = await startPlayback(
        activeRatingKey,
        device.host,
        room.id,
        offset,
        playQueueId,
        device.id,
        serviceId,
      );
      if (!result.success) {
        roomFirst.failed();   // v231: the toast goes back to the room still playing
        setStep({ type: 'error', message: result.message ?? 'Playback failed' });
        return;
      }

      // Optimistic success — backend confirmed command was sent
      window.dispatchEvent(new CustomEvent('playback-started', {
        detail: { roomId: room.id, ratingKey: String(activeRatingKey), title },
      }));
      setStep({ type: 'success', roomName });
      roomFirst.closeAfter(2000);   // v225: prod's 2s close, after the Playing in toast / picker
    } catch {
      roomFirst.failed();
      setStep({ type: 'error', message: 'Network error' });
    }
  }, [activeRatingKey, onClose]);

  // --- Resume handlers ---
  const handleResume = useCallback((info: ResumeInfo) => {
    const room = selectedRoomRef.current;
    const device = selectedDeviceRef.current;
    const service = selectedServiceRef.current;
    if (!room || !device) return;
    initiatePlayback(room, device, info.offset_ms, undefined, service);
  }, [initiatePlayback]);

  const handleStartOver = useCallback(() => {
    const room = selectedRoomRef.current;
    const device = selectedDeviceRef.current;
    const service = selectedServiceRef.current;
    if (!room || !device) return;
    initiatePlayback(room, device, 0, undefined, service);
  }, [initiatePlayback]);

  // --- Render step content ---
  function renderContent() {
    switch (step.type) {
      case 'library':
        return (
          <div style={{
            padding: isMobile ? '40px 24px 48px' : '24px 32px 36px',
            position: 'relative',
            zIndex: 1,
            minHeight: isMobile ? '280px' : undefined,
            display: isMobile ? 'flex' : undefined,
            flexDirection: isMobile ? 'column' : undefined,
            justifyContent: isMobile ? 'center' : undefined,
          }}>
            {/* Clearlogo or title — hidden on mobile */}
            {!isMobile && (
              <div style={{
                display: 'flex',
                justifyContent: 'center',
                margin: '0 0 28px',
              }}>
                {clearLogoUrl ? (
                  <img
                    src={clearLogoUrl}
                    alt={title}
                    style={{
                      maxHeight: '60px',
                      maxWidth: '70%',
                      objectFit: 'contain',
                    }}
                  />
                ) : (
                  <p style={{
                    fontSize: '22px',
                    fontWeight: 600,
                    color: '#fff',
                    margin: 0,
                    textAlign: 'center',
                  }}>
                    {title}
                  </p>
                )}
              </div>
            )}
            {isMobile ? (
              /* Mobile: stacked pills — matches room selector pattern */
              <div style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '14px',
                position: 'relative',
                zIndex: 1,
              }}>
                {libraries!.map((lib) => (
                  <button
                    key={lib}
                    type="button"
                    onClick={() => handleLibrarySelect(lib)}
                    style={{
                      padding: '18px 22px',
                      borderRadius: '50px',
                      border: '1px solid rgba(255,255,255,0.12)',
                      background: 'rgba(255,255,255,0.06)',
                      color: '#fff',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '14px',
                      transition: 'background 0.15s',
                    }}
                  >
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18" />
                      <line x1="7" y1="2" x2="7" y2="22" /><line x1="17" y1="2" x2="17" y2="22" />
                      <line x1="2" y1="12" x2="22" y2="12" /><line x1="2" y1="7" x2="7" y2="7" />
                      <line x1="2" y1="17" x2="7" y2="17" /><line x1="17" y1="7" x2="22" y2="7" />
                      <line x1="17" y1="17" x2="22" y2="17" />
                    </svg>
                    <span style={{ fontSize: '17px', fontWeight: 600, flex: 1, textAlign: 'left' }}>
                      {lib}
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              /* Desktop/tablet: 2-column grid */
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(2, 1fr)',
                gap: '20px',
                maxWidth: '500px',
                margin: '0 auto',
                position: 'relative',
                zIndex: 1,
              }}>
                {libraries!.map((lib) => (
                  <button
                    key={lib}
                    type="button"
                    onClick={() => handleLibrarySelect(lib)}
                    style={{
                      padding: '35px 20px',
                      borderRadius: '20px',
                      border: '2px solid rgba(255,255,255,0.25)',
                      background: 'rgba(0,0,0,0.4)',
                      color: '#fff',
                      fontSize: '18px',
                      fontWeight: 600,
                      cursor: 'pointer',
                      textAlign: 'center',
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '10px',
                      transition: 'all 0.3s ease',
                    }}
                  >
                    <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18" />
                      <line x1="7" y1="2" x2="7" y2="22" /><line x1="17" y1="2" x2="17" y2="22" />
                      <line x1="2" y1="12" x2="22" y2="12" /><line x1="2" y1="7" x2="7" y2="7" />
                      <line x1="2" y1="17" x2="7" y2="17" /><line x1="17" y1="7" x2="22" y2="7" />
                      <line x1="17" y1="17" x2="22" y2="17" />
                    </svg>
                    {lib}
                  </button>
                ))}
              </div>
            )}

            {/* Subtext below libraries */}
            {libraries!.length > 0 && (
              <p style={{
                textAlign: 'center',
                fontSize: isMobile ? '15px' : '16px',
                fontWeight: 400,
                letterSpacing: '0.5px',
                color: 'rgba(255,255,255,0.5)',
                margin: '20px 0 0',
              }}>
                Select library
              </p>
            )}
          </div>
        );

      case 'room':
        return (
          <div style={{ padding: isMobile ? '16px 24px 28px' : '24px 40px 36px' }}>
            {/* Clearlogo or title — hidden on mobile */}
            {!isMobile && (
              <div style={{
                display: 'flex',
                justifyContent: 'center',
                margin: '0 0 28px',
              }}>
                {clearLogoUrl ? (
                  <img
                    src={clearLogoUrl}
                    alt={title}
                    style={{
                      maxHeight: '60px',
                      maxWidth: '70%',
                      objectFit: 'contain',
                    }}
                  />
                ) : (
                  <p style={{
                    fontSize: '22px',
                    fontWeight: 600,
                    color: '#fff',
                    margin: 0,
                    textAlign: 'center',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}>
                    {title}
                  </p>
                )}
              </div>
            )}

            {roomsLoading ? (
              <div style={{ display: 'flex', justifyContent: 'center', padding: '40px 0' }}>
                <div style={{
                  width: '40px',
                  height: '40px',
                  borderRadius: '50%',
                  border: '3px solid rgba(255,255,255,0.15)',
                  borderTopColor: 'rgba(255,255,255,0.7)',
                  animation: 'playbackSpin 0.8s linear infinite',
                }} />
              </div>
            ) : rooms.length === 0 ? (
              <p style={{
                textAlign: 'center',
                color: 'rgba(255,255,255,0.5)',
                fontSize: '18px',
                padding: '32px 0',
              }}>
                No rooms configured. Add rooms in Settings.
              </p>
            ) : isMobile ? (
              /* Mobile: stacked pills with optional "Play on this device" */
              <>
                <div style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '14px',
                }}>
                  {[...rooms].sort((a, b) => {
                    if (a.id === defaultPlaybackTarget.roomId) return -1;
                    if (b.id === defaultPlaybackTarget.roomId) return 1;
                    return 0;
                  }).map((room) => {
                    const available = roomHasAvailableDevice(room);
                    return (
                      <button
                        key={room.id}
                        type="button"
                        onClick={() => available && handleRoomSelect(room)}
                        disabled={!available || resumeLoading}
                        style={{
                          padding: '18px 22px',
                          borderRadius: '50px',
                          border: '1px solid rgba(255,255,255,0.12)',
                          background: available ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.02)',
                          color: available ? '#fff' : 'rgba(255,255,255,0.3)',
                          cursor: available && !resumeLoading ? 'pointer' : 'default',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '14px',
                          transition: 'background 0.15s',
                          opacity: resumeLoading ? 0.5 : 1,
                        }}
                      >
                        <RoomIcon name={room.name} />
                        <span style={{ fontSize: '17px', fontWeight: 600, flex: 1, textAlign: 'left' }}>
                          {room.name}
                        </span>
                        {!available && (
                          <span style={{
                            fontSize: '13px',
                            color: 'rgba(255,255,255,0.3)',
                            fontWeight: 500,
                          }}>
                            Offline
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </>
            ) : (
              /* Desktop/tablet: 2-column grid — matches legacy web style */
              <>
              <div style={{
                display: 'grid',
                gridTemplateColumns: rooms.length === 1 ? '1fr' : 'repeat(2, 1fr)',
                gap: '20px',
                maxWidth: '500px',
                margin: '0 auto',
              }}>
                {[...rooms].sort((a, b) => {
                  if (a.id === defaultPlaybackTarget.roomId) return -1;
                  if (b.id === defaultPlaybackTarget.roomId) return 1;
                  return 0;
                }).map((room) => {
                  const available = roomHasAvailableDevice(room);
                  return (
                    <button
                      key={room.id}
                      type="button"
                      onClick={() => available && handleRoomSelect(room)}
                      disabled={!available || resumeLoading}
                      style={{
                        padding: '25px 20px',
                        borderRadius: '20px',
                        border: `2px solid ${available ? 'rgba(255,255,255,0.25)' : 'rgba(255,255,255,0.1)'}`,
                        background: available ? 'rgba(0,0,0,0.4)' : 'rgba(0,0,0,0.2)',
                        color: available ? '#3b82f6' : 'rgba(255,255,255,0.3)',
                        cursor: available && !resumeLoading ? 'pointer' : 'default',
                        textAlign: 'center',
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        gap: '12px',
                        transition: 'all 0.3s ease',
                        opacity: resumeLoading ? 0.5 : 1,
                      }}
                    >
                      <RoomIcon name={room.name} size={42} />
                      <span style={{ fontSize: '18px', fontWeight: 600, color: '#fff' }}>
                        {room.name}
                      </span>
                      <span style={{
                        fontSize: '13px',
                        color: available ? '#10b981' : 'rgba(255,255,255,0.3)',
                        fontWeight: 500,
                      }}>
                        {available ? 'Available' : 'Not Configured'}
                      </span>
                    </button>
                  );
                })}
              </div>
              </>
            )}

            {/* Subtext below rooms */}
            {rooms.length > 0 && !roomsLoading && !resumeLoading && (
              <p style={{
                textAlign: 'center',
                fontSize: isMobile ? '15px' : '16px',
                fontWeight: 400,
                letterSpacing: '0.5px',
                color: 'rgba(255,255,255,0.5)',
                margin: '20px 0 0',
              }}>
                Select a room for playback
              </p>
            )}

            {resumeLoading && (
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '12px',
                marginTop: '24px',
                color: 'rgba(255,255,255,0.6)',
                fontSize: '16px',
              }}>
                <div style={{
                  width: '20px',
                  height: '20px',
                  borderRadius: '50%',
                  border: '2px solid rgba(255,255,255,0.15)',
                  borderTopColor: 'rgba(255,255,255,0.6)',
                  animation: 'playbackSpin 0.8s linear infinite',
                }} />
                Checking resume point...
              </div>
            )}
          </div>
        );

      case 'device':
        return (
          <div style={{ padding: isMobile ? '16px 24px 28px' : '24px 40px 36px' }}>
            {/* Clearlogo or title — hidden on mobile */}
            {!isMobile && (
              <div style={{
                display: 'flex',
                justifyContent: 'center',
                margin: '0 0 28px',
              }}>
                {clearLogoUrl ? (
                  <img
                    src={clearLogoUrl}
                    alt={title}
                    style={{
                      maxHeight: '60px',
                      maxWidth: '70%',
                      objectFit: 'contain',
                    }}
                  />
                ) : (
                  <p style={{
                    fontSize: '22px',
                    fontWeight: 600,
                    color: '#fff',
                    margin: 0,
                    textAlign: 'center',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}>
                    {title}
                  </p>
                )}
              </div>
            )}

            {isMobile ? (
              /* Mobile: stacked pills */
              <div style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '14px',
              }}>
                {step.room.devices.map((device) => {
                  const available = !!device.host;
                  return (
                    <button
                      key={device.id}
                      type="button"
                      onClick={() => available && handleDeviceSelect(step.room, device)}
                      disabled={!available || resumeLoading}
                      style={{
                        padding: '18px 22px',
                        borderRadius: '50px',
                        border: '1px solid rgba(255,255,255,0.12)',
                        background: available ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.02)',
                        color: available ? '#fff' : 'rgba(255,255,255,0.3)',
                        cursor: available && !resumeLoading ? 'pointer' : 'default',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '14px',
                        transition: 'background 0.15s',
                        opacity: resumeLoading ? 0.5 : 1,
                      }}
                    >
                      <PlatformIcon platform={device.platform} />
                      <div style={{ flex: 1, textAlign: 'left' }}>
                        <span style={{ fontSize: '17px', fontWeight: 600, display: 'block' }}>
                          {device.name}
                        </span>
                      </div>
                      {!available && (
                        <span style={{
                          fontSize: '13px',
                          color: 'rgba(255,255,255,0.3)',
                          fontWeight: 500,
                        }}>
                          Offline
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            ) : (
              /* Desktop/tablet: 2-column grid */
              <div style={{
                display: 'grid',
                gridTemplateColumns: step.room.devices.length === 1 ? '1fr' : 'repeat(2, 1fr)',
                gap: '20px',
                maxWidth: '500px',
                margin: '0 auto',
              }}>
                {step.room.devices.map((device) => {
                  const available = !!device.host;
                  return (
                    <button
                      key={device.id}
                      type="button"
                      onClick={() => available && handleDeviceSelect(step.room, device)}
                      disabled={!available || resumeLoading}
                      style={{
                        padding: '25px 20px',
                        borderRadius: '20px',
                        border: `2px solid ${available ? 'rgba(255,255,255,0.25)' : 'rgba(255,255,255,0.1)'}`,
                        background: available ? 'rgba(0,0,0,0.4)' : 'rgba(0,0,0,0.2)',
                        color: available ? '#3b82f6' : 'rgba(255,255,255,0.3)',
                        cursor: available && !resumeLoading ? 'pointer' : 'default',
                        textAlign: 'center',
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        gap: '12px',
                        transition: 'all 0.3s ease',
                        opacity: resumeLoading ? 0.5 : 1,
                      }}
                    >
                      <PlatformIcon platform={device.platform} size={42} />
                      <span style={{ fontSize: '18px', fontWeight: 600, color: '#fff' }}>
                        {device.name}
                      </span>
                      <span style={{
                        fontSize: '12px',
                        padding: '3px 10px',
                        borderRadius: '20px',
                        background: 'rgba(255,255,255,0.08)',
                        color: 'rgba(255,255,255,0.5)',
                        fontWeight: 500,
                      }}>
                        {platformLabel(device.platform)}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}

            {/* Subtext below devices */}
            <p style={{
              textAlign: 'center',
              fontSize: isMobile ? '15px' : '16px',
              fontWeight: 400,
              letterSpacing: '0.5px',
              color: 'rgba(255,255,255,0.5)',
              margin: '20px 0 0',
            }}>
              Select a device in {step.room.name}
            </p>

            {resumeLoading && (
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '12px',
                marginTop: '24px',
                color: 'rgba(255,255,255,0.6)',
                fontSize: '16px',
              }}>
                <div style={{
                  width: '20px',
                  height: '20px',
                  borderRadius: '50%',
                  border: '2px solid rgba(255,255,255,0.15)',
                  borderTopColor: 'rgba(255,255,255,0.6)',
                  animation: 'playbackSpin 0.8s linear infinite',
                }} />
                Checking resume point...
              </div>
            )}
          </div>
        );

      case 'service':
        return (
          <div style={{ padding: isMobile ? '16px 24px 28px' : '24px 40px 36px' }}>
            {!isMobile && (
              <div style={{
                display: 'flex',
                justifyContent: 'center',
                margin: '0 0 28px',
              }}>
                {clearLogoUrl ? (
                  <img
                    src={clearLogoUrl}
                    alt={title}
                    style={{
                      maxHeight: '60px',
                      maxWidth: '70%',
                      objectFit: 'contain',
                    }}
                  />
                ) : (
                  <p style={{
                    fontSize: '22px',
                    fontWeight: 600,
                    color: '#fff',
                    margin: 0,
                    textAlign: 'center',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}>
                    {title}
                  </p>
                )}
              </div>
            )}

            {isMobile ? (
              <div style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '14px',
              }}>
                {step.device.services.map((svc) => (
                  <button
                    key={svc.id}
                    type="button"
                    onClick={() => handleServiceSelect(step.room, step.device, svc)}
                    disabled={resumeLoading}
                    style={{
                      padding: '18px 22px',
                      borderRadius: '50px',
                      border: '1px solid rgba(255,255,255,0.12)',
                      background: 'rgba(255,255,255,0.06)',
                      color: '#fff',
                      cursor: resumeLoading ? 'default' : 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '14px',
                      transition: 'background 0.15s',
                      opacity: resumeLoading ? 0.5 : 1,
                    }}
                  >
                    <img
                      src={svc.type === 'plex' ? assetUrl('/static/images/plex_white.png') : assetUrl('/static/images/kodi_white.png')}
                      alt={svc.type === 'plex' ? 'Plex' : 'Kodi'}
                      style={{ height: svc.type === 'kodi' ? '80px' : '32px', objectFit: 'contain' }}
                    />
                  </button>
                ))}
              </div>
            ) : (
              <div style={{
                display: 'grid',
                gridTemplateColumns: step.device.services.length === 1 ? '1fr' : 'repeat(2, 1fr)',
                gap: '20px',
                maxWidth: '500px',
                margin: '0 auto',
              }}>
                {step.device.services.map((svc) => (
                  <button
                    key={svc.id}
                    type="button"
                    onClick={() => handleServiceSelect(step.room, step.device, svc)}
                    disabled={resumeLoading}
                    style={{
                      padding: '30px 20px',
                      borderRadius: '20px',
                      border: '2px solid rgba(255,255,255,0.25)',
                      background: 'rgba(0,0,0,0.4)',
                      cursor: resumeLoading ? 'default' : 'pointer',
                      textAlign: 'center',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      transition: 'all 0.3s ease',
                      opacity: resumeLoading ? 0.5 : 1,
                    }}
                  >
                    <img
                      src={svc.type === 'plex' ? assetUrl('/static/images/plex_white.png') : assetUrl('/static/images/kodi_white.png')}
                      alt={svc.type === 'plex' ? 'Plex' : 'Kodi'}
                      style={{ height: svc.type === 'kodi' ? '100px' : '48px', objectFit: 'contain' }}
                    />
                  </button>
                ))}
              </div>
            )}

            <p style={{
              textAlign: 'center',
              fontSize: isMobile ? '15px' : '16px',
              fontWeight: 400,
              letterSpacing: '0.5px',
              color: 'rgba(255,255,255,0.5)',
              margin: '20px 0 0',
            }}>
              Select a playback service
            </p>

            {resumeLoading && (
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '12px',
                marginTop: '24px',
                color: 'rgba(255,255,255,0.6)',
                fontSize: '16px',
              }}>
                <div style={{
                  width: '20px',
                  height: '20px',
                  borderRadius: '50%',
                  border: '2px solid rgba(255,255,255,0.15)',
                  borderTopColor: 'rgba(255,255,255,0.6)',
                  animation: 'playbackSpin 0.8s linear infinite',
                }} />
                Checking resume point...
              </div>
            )}
          </div>
        );

      case 'resume':
        return (
          <div style={{ padding: '24px 40px 36px', textAlign: 'center' }}>
            {/* Clearlogo or title */}
            <div style={{
              display: 'flex',
              justifyContent: 'center',
              margin: '0 0 28px',
            }}>
              {clearLogoUrl ? (
                <img
                  src={clearLogoUrl}
                  alt={title}
                  style={{
                    maxHeight: '60px',
                    maxWidth: '70%',
                    objectFit: 'contain',
                  }}
                />
              ) : (
                <p style={{
                  fontSize: '22px',
                  fontWeight: 600,
                  color: '#fff',
                  margin: 0,
                }}>
                  {title}
                </p>
              )}
            </div>

            <p style={{
              fontSize: '20px',
              fontWeight: 500,
              color: '#fff',
              margin: '0 0 24px',
            }}>
              Would you like to resume playback?
            </p>

            <div style={{
              display: 'flex',
              flexDirection: 'column',
              gap: '20px',
              maxWidth: '420px',
              margin: '0 auto',
            }}>
              <button
                type="button"
                onClick={() => handleResume(step.info)}
                style={{
                  padding: '22px 30px',
                  borderRadius: '20px',
                  border: '2px solid #10b981',
                  background: 'rgba(0,0,0,0.4)',
                  color: '#fff',
                  fontSize: '18px',
                  fontWeight: 600,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '15px',
                  transition: 'all 0.3s ease',
                }}
              >
                <svg width="24" height="24" viewBox="0 0 24 24" fill="#10b981">
                  <polygon points="6,3 20,12 6,21" />
                </svg>
                Resume from {step.info.time_string}
              </button>

              <button
                type="button"
                onClick={handleStartOver}
                style={{
                  padding: '22px 30px',
                  borderRadius: '20px',
                  border: '2px solid rgba(255,255,255,0.25)',
                  background: 'rgba(0,0,0,0.4)',
                  color: '#fff',
                  fontSize: '18px',
                  fontWeight: 600,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '15px',
                  transition: 'all 0.3s ease',
                }}
              >
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="1 4 1 10 7 10" />
                  <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
                </svg>
                Play from beginning
              </button>
            </div>
          </div>
        );

      case 'playing':
        return (
          <div style={{
            padding: '48px 32px',
            textAlign: 'center',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: '20px',
          }}>
            <div style={{
              width: '56px',
              height: '56px',
              borderRadius: '50%',
              border: '3px solid rgba(255,255,255,0.15)',
              borderTopColor: 'rgba(255,255,255,0.7)',
              animation: 'playbackSpin 0.8s linear infinite',
            }} />
            <p style={{
              fontSize: '20px',
              fontWeight: 600,
              color: '#fff',
              margin: 0,
            }}>
              {shuffle ? (shuffleLabel ?? 'Shuffling...') : 'Starting playback...'}
            </p>
            <p style={{
              fontSize: '17px',
              color: 'rgba(255,255,255,0.5)',
              margin: 0,
            }}>
              {step.roomName}
            </p>
          </div>
        );

      case 'success':
        return (
          <div style={{
            padding: '48px 32px',
            textAlign: 'center',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: '20px',
          }}>
            <div style={{
              width: '64px',
              height: '64px',
              borderRadius: '50%',
              background: 'rgba(74,222,128,0.15)',
              border: '2px solid rgba(74,222,128,0.4)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}>
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#4ade80" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            </div>
            <p style={{
              fontSize: '20px',
              fontWeight: 600,
              color: '#4ade80',
              margin: 0,
            }}>
              Playing in {step.roomName}
            </p>
          </div>
        );

      case 'error':
        return (
          <div style={{
            padding: '48px 32px',
            textAlign: 'center',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: '20px',
          }}>
            <div style={{
              width: '64px',
              height: '64px',
              borderRadius: '50%',
              background: 'rgba(239,68,68,0.15)',
              border: '2px solid rgba(239,68,68,0.4)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}>
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#f87171" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </div>
            <p style={{
              fontSize: '20px',
              fontWeight: 600,
              color: '#f87171',
              margin: 0,
            }}>
              {step.message}
            </p>
            <button
              type="button"
              onClick={() => setStep({ type: 'room' })}
              style={{
                marginTop: '4px',
                padding: '16px 36px',
                borderRadius: '16px',
                border: '2px solid rgba(255,255,255,0.25)',
                background: 'rgba(0,0,0,0.4)',
                color: '#fff',
                fontSize: '17px',
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'all 0.3s ease',
              }}
            >
              Try Again
            </button>
          </div>
        );
    }
  }

  const showClose = true;

  return createPortal(
    <div
      data-modal="PlaybackModal"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10002,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      {/* Backdrop */}
      <div
        onClick={showClose ? onClose : undefined}
        style={{
          position: 'absolute',
          inset: 0,
          background: 'rgba(0,0,0,0.6)',
          backdropFilter: 'blur(8px)',
          WebkitBackdropFilter: 'blur(8px)',
          animation: 'playbackFadeIn 0.3s ease both',
        }}
      />

      {/* Centered dialog */}
      <div
        style={{
          position: 'relative',
          width: '92%',
          maxWidth: '560px',
          maxHeight: '85vh',
          borderRadius: '24px',
          background: '#2a2a2a',
          border: '1px solid rgba(255,255,255,0.12)',
          overflow: 'hidden',
          boxShadow: '0 32px 80px rgba(0,0,0,0.6)',
          animation: 'playbackBounceIn 0.5s cubic-bezier(0.34, 1.56, 0.64, 1) both',
        }}
      >
        {/* Mobile library backdrop — covers entire dialog */}
        {isMobile && step.type === 'library' && backdropUrl && (
          <>
            <img
              src={backdropUrl}
              alt=""
              style={{
                position: 'absolute',
                inset: 0,
                width: '100%',
                height: '100%',
                objectFit: 'cover',
                zIndex: 0,
              }}
            />
            <div style={{
              position: 'absolute',
              inset: 0,
              background: 'linear-gradient(to bottom, rgba(42,42,42,0.5), rgba(42,42,42,0.92))',
              zIndex: 0,
            }} />
          </>
        )}

        {/* Step content */}
        {renderContent()}
      </div>

      {/* Keyframes */}
      <style>{`
        @keyframes playbackBounceIn {
          0%   { transform: scale(0.85); opacity: 0; }
          50%  { opacity: 1; }
          70%  { transform: scale(1.03); }
          85%  { transform: scale(0.98); }
          100% { transform: scale(1); }
        }
        @keyframes playbackFadeIn {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
        @keyframes playbackSpin {
          to { transform: rotate(360deg); }
        }
      `}</style>
      {roomFirst.ui}
    </div>,
    document.body,
  );
}
