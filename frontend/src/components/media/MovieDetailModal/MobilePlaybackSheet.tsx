/**
 * MobilePlaybackSheet — bottom sheet playback flow for mobile.
 *
 * Same step logic as PlaybackModal but rendered as a slide-up sheet
 * with grab handle, swipe-to-dismiss, and large tap targets.
 *
 * Steps:
 * 1. Library Selection (if movie exists in multiple Plex libraries)
 * 2. Room Selection
 * 3. Device Selection (if room has 2+ devices)
 * 4. Resume Prompt
 * 5. Playing / Success / Error status
 */

import { useState, useEffect, useCallback, useRef } from 'react';
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
import { assetUrl } from '@/urlHelpers';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface MobilePlaybackSheetProps {
  ratingKey: number;
  title: string;
  libraries?: string[];
  libraryKeys?: Record<string, number>;
  shuffle?: boolean;
  shuffleLabel?: string;
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

const DISMISS_THRESHOLD = 120;

// ---------------------------------------------------------------------------
// Room icon SVGs
// ---------------------------------------------------------------------------

function RoomIcon({ name, size = 32 }: { name: string; size?: number }) {
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
    default:
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="2" y="7" width="20" height="15" rx="2" ry="2" />
          <polyline points="17 2 12 7 7 2" />
        </svg>
      );
  }
}

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
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
        <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
        <line x1="6" y1="6" x2="6.01" y2="6" />
        <line x1="6" y1="18" x2="6.01" y2="18" />
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="7" width="20" height="15" rx="2" ry="2" />
      <polyline points="17 2 12 7 7 2" />
    </svg>
  );
}

function roomHasAvailableDevice(room: Room): boolean {
  return room.devices.some((d) => !!d.host);
}



// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MobilePlaybackSheet({
  ratingKey: initialRatingKey,
  title,
  libraries,
  libraryKeys,
  shuffle,
  shuffleLabel,
  onClose,
}: MobilePlaybackSheetProps) {
  const [step, setStep] = useState<Step>(() =>
    libraries && libraries.length > 1 ? { type: 'library' } : { type: 'room' },
  );

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


  // Sheet animation
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });
  const [dragOffset, setDragOffset] = useState(0);
  const [dismissing, setDismissing] = useState(false);
  const [entered, setEntered] = useState(false);

  // Smooth content height transitions between steps
  const contentRef = useRef<HTMLDivElement>(null);
  const [contentHeight, setContentHeight] = useState<number | undefined>(undefined);

  useEffect(() => {
    if (!contentRef.current) return;
    const h = contentRef.current.scrollHeight;
    setContentHeight(h);
  }, [step, rooms, resumeLoading]);

  useEffect(() => {
    requestAnimationFrame(() => setEntered(true));
  }, []);

  // Fetch rooms on mount
  useEffect(() => {
    let cancelled = false;
    fetchRooms().then((data) => {
      if (!cancelled) {
        setRooms(data.rooms);
        setDefaultPlaybackTarget(data.defaultPlaybackTarget);
        setRoomsLoading(false);

        const singleRoom = data.rooms.length === 1 ? data.rooms[0] : undefined;
        if (singleRoom && step.type === 'room' && roomHasAvailableDevice(singleRoom)) {
          handleRoomSelect(singleRoom);
        }
      }
    });
    return () => { cancelled = true; };
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
  }, [onClose]);

  const handleDismiss = useCallback(() => {
    setDismissing(true);
    setTimeout(onClose, 250);
  }, [onClose]);

  // Drag-to-dismiss
  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const target = e.target as HTMLElement;
    const isHandle = target.closest('[data-sheet-handle]');
    if (isHandle && e.touches.length > 0) {
      dragRef.current = { active: true, startY: e.touches[0]!.clientY, currentY: e.touches[0]!.clientY };
    }
  }, []);

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragRef.current.active || e.touches.length === 0) return;
    const y = e.touches[0]!.clientY;
    const dy = y - dragRef.current.startY;
    if (dy > 0) {
      dragRef.current.currentY = y;
      setDragOffset(dy);
    }
  }, []);

  const onTouchEnd = useCallback(() => {
    if (!dragRef.current.active) return;
    const dy = dragRef.current.currentY - dragRef.current.startY;
    dragRef.current.active = false;
    if (dy > DISMISS_THRESHOLD) {
      handleDismiss();
    } else {
      setDragOffset(0);
    }
  }, [handleDismiss]);

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

    const availableDevices = room.devices.filter((d) => !!d.host);
    const singleDevice = availableDevices.length === 1 ? availableDevices[0] : undefined;
    if (singleDevice) {
      setSelectedDevice(singleDevice);
      await proceedAfterDeviceSelect(room, singleDevice);
    } else if (availableDevices.length > 1) {
      setStep({ type: 'device', room });
    }
  }, [activeRatingKey, shuffle]);

  // --- Device selection ---
  const handleDeviceSelect = useCallback(async (room: Room, device: Device) => {
    if (!device.host) return;
    setSelectedDevice(device);
    await proceedAfterDeviceSelect(room, device);
  }, [activeRatingKey, shuffle]);

  // --- After device is selected ---
  const proceedAfterDeviceSelect = useCallback(async (room: Room, device: Device) => {
    const services = (device.services ?? []).filter((s) => !!s.id);
    if (services.length > 1) {
      setStep({ type: 'service', room, device });
      return;
    }
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
    if (shuffle) {
      setStep({ type: 'playing', roomName: room.name });
      try {
        const pq = await createPlayQueue(activeRatingKey, true);
        if (!pq.success || !pq.play_queue_id) {
          setStep({ type: 'error', message: pq.error ?? 'Failed to create play queue' });
          return;
        }
        await initiatePlayback(room, device, 0, pq.play_queue_id, service);
      } catch {
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
        setStep({ type: 'error', message: result.message ?? 'Playback failed' });
        return;
      }

      // Optimistic success — backend confirmed command was sent
      window.dispatchEvent(new CustomEvent('playback-started', {
        detail: { roomId: room.id, ratingKey: String(activeRatingKey), title },
      }));
      setStep({ type: 'success', roomName });
      setTimeout(() => onClose(), 2000);
    } catch {
      setStep({ type: 'error', message: 'Network error' });
    }
  }, [activeRatingKey, onClose]);

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

  const sheetTranslate = dismissing ? '100%' : entered ? `${dragOffset}px` : '100%';
  const canDismiss = true;

  return (
    <div
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
        if (e.target === e.currentTarget && canDismiss) handleDismiss();
      }}
    >
      {/* Bottom sheet */}
      <div
        onTouchStart={canDismiss ? onTouchStart : undefined}
        onTouchMove={canDismiss ? onTouchMove : undefined}
        onTouchEnd={canDismiss ? onTouchEnd : undefined}
        style={{
          maxHeight: 'calc(100% - 60px)',
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
            padding: '12px 0 20px',
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

        {/* Content wrapper — animates height between steps */}
        <div style={{
          overflow: 'hidden',
          transition: contentHeight !== undefined ? 'height 0.25s cubic-bezier(0.32, 0.72, 0, 1)' : 'none',
          height: contentHeight !== undefined ? `${contentHeight}px` : 'auto',
        }}>
          <div ref={contentRef}>

        {/* === Library step === */}
        {step.type === 'library' && (
          <div style={{ padding: '8px 20px 60px' }}>
            <p style={{ fontSize: '15px', fontWeight: 500, color: 'rgba(255,255,255,0.4)', textAlign: 'center', margin: '0 0 16px' }}>
              Select Library
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {libraries!.map((lib) => (
                <button
                  key={lib}
                  type="button"
                  onClick={() => handleLibrarySelect(lib)}
                  style={{
                    width: '100%',
                    height: '64px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '16px',
                    padding: '0 24px',
                    borderRadius: '16px',
                    fontSize: '18px',
                    fontWeight: 700,
                    cursor: 'pointer',
                    border: '1px solid rgba(255,255,255,0.12)',
                    background: 'rgba(255,255,255,0.06)',
                    color: '#fff',
                    transition: 'background 0.15s',
                  }}
                >
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z" />
                  </svg>
                  <span style={{ flex: 1, textAlign: 'left' }}>{lib}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* === Room step === */}
        {step.type === 'room' && (
          <div style={{ padding: '8px 20px 60px' }}>
            <p style={{ fontSize: '15px', fontWeight: 500, color: 'rgba(255,255,255,0.4)', textAlign: 'center', margin: '0 0 16px' }}>
              Select Room
            </p>

            {roomsLoading ? (
              <div style={{ display: 'flex', justifyContent: 'center', padding: '40px 0' }}>
                <div style={{
                  width: '40px', height: '40px', borderRadius: '50%',
                  border: '3px solid rgba(255,255,255,0.15)', borderTopColor: 'rgba(255,255,255,0.7)',
                  animation: 'pbSpin 0.8s linear infinite',
                }} />
              </div>
            ) : rooms.length === 0 ? (
              <p style={{ textAlign: 'center', color: 'rgba(255,255,255,0.5)', fontSize: '15px', padding: '32px 0' }}>
                No rooms configured
              </p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
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
                        width: '100%',
                        height: '64px',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '16px',
                        padding: '0 24px',
                        borderRadius: '16px',
                        fontSize: '18px',
                        fontWeight: 700,
                        cursor: available && !resumeLoading ? 'pointer' : 'default',
                        border: '1px solid rgba(255,255,255,0.12)',
                        background: available ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.02)',
                        color: available ? '#fff' : 'rgba(255,255,255,0.3)',
                        transition: 'background 0.15s',
                        opacity: resumeLoading ? 0.5 : 1,
                      }}
                    >
                      <RoomIcon name={room.name} />
                      <span style={{ flex: 1, textAlign: 'left' }}>{room.name}</span>
                      {!available && (
                        <span style={{ fontSize: '13px', color: 'rgba(255,255,255,0.3)', fontWeight: 500 }}>
                          Offline
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            )}

            {resumeLoading && (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '12px', marginTop: '24px', color: 'rgba(255,255,255,0.6)', fontSize: '14px' }}>
                <div style={{
                  width: '20px', height: '20px', borderRadius: '50%',
                  border: '2px solid rgba(255,255,255,0.15)', borderTopColor: 'rgba(255,255,255,0.6)',
                  animation: 'pbSpin 0.8s linear infinite',
                }} />
                Checking resume point...
              </div>
            )}
          </div>
        )}

        {/* === Device step === */}
        {step.type === 'device' && (
          <div style={{ padding: '8px 20px 60px' }}>
            <p style={{ fontSize: '15px', fontWeight: 500, color: 'rgba(255,255,255,0.4)', textAlign: 'center', margin: '0 0 16px' }}>
              Select Device
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {step.room.devices.filter(d => !!d.host).map((device) => (
                <button
                  key={device.id}
                  type="button"
                  onClick={() => handleDeviceSelect(step.room, device)}
                  disabled={resumeLoading}
                  style={{
                    width: '100%',
                    height: '64px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '12px',
                    padding: '0 24px',
                    borderRadius: '16px',
                    fontSize: '17px',
                    fontWeight: 600,
                    cursor: resumeLoading ? 'default' : 'pointer',
                    border: '1px solid rgba(255,255,255,0.12)',
                    background: 'rgba(255,255,255,0.06)',
                    color: '#fff',
                    transition: 'background 0.15s',
                    opacity: resumeLoading ? 0.5 : 1,
                  }}
                >
                  <PlatformIcon platform={device.platform} />
                  {device.name}
                </button>
              ))}
            </div>

            {resumeLoading && (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '12px', marginTop: '24px', color: 'rgba(255,255,255,0.6)', fontSize: '14px' }}>
                <div style={{
                  width: '20px', height: '20px', borderRadius: '50%',
                  border: '2px solid rgba(255,255,255,0.15)', borderTopColor: 'rgba(255,255,255,0.6)',
                  animation: 'pbSpin 0.8s linear infinite',
                }} />
                Checking resume point...
              </div>
            )}
          </div>
        )}

        {/* === Service step === */}
        {step.type === 'service' && (
          <div style={{ padding: '8px 20px 60px' }}>
            <p style={{ fontSize: '15px', fontWeight: 500, color: 'rgba(255,255,255,0.4)', textAlign: 'center', margin: '0 0 16px' }}>
              Select Service
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              {step.device.services.map((svc) => (
                <button
                  key={svc.id}
                  type="button"
                  onClick={() => handleServiceSelect(step.room, step.device, svc)}
                  disabled={resumeLoading}
                  style={{
                    width: '100%',
                    height: '64px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '14px',
                    padding: '0 24px',
                    borderRadius: '16px',
                    border: '1px solid rgba(255,255,255,0.12)',
                    background: 'rgba(255,255,255,0.06)',
                    color: '#fff',
                    cursor: resumeLoading ? 'default' : 'pointer',
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

            {resumeLoading && (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '12px', marginTop: '24px', color: 'rgba(255,255,255,0.6)', fontSize: '14px' }}>
                <div style={{
                  width: '20px', height: '20px', borderRadius: '50%',
                  border: '2px solid rgba(255,255,255,0.15)', borderTopColor: 'rgba(255,255,255,0.6)',
                  animation: 'pbSpin 0.8s linear infinite',
                }} />
                Checking resume point...
              </div>
            )}
          </div>
        )}

        {/* === Resume step === */}
        {step.type === 'resume' && (
          <div style={{ padding: '8px 20px 60px' }}>
            <p style={{ fontSize: '15px', fontWeight: 500, color: 'rgba(255,255,255,0.4)', textAlign: 'center', margin: '0 0 16px' }}>
              Resume Playback?
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <button
                type="button"
                onClick={() => handleResume(step.info)}
                style={{
                  width: '100%',
                  height: '64px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '16px',
                  padding: '0 24px',
                  borderRadius: '16px',
                  fontSize: '18px',
                  fontWeight: 700,
                  cursor: 'pointer',
                  border: '1px solid rgba(16,185,129,0.3)',
                  background: 'rgba(16,185,129,0.08)',
                  color: '#fff',
                  transition: 'background 0.15s',
                }}
              >
                <svg width="24" height="24" viewBox="0 0 24 24" fill="#10b981"><polygon points="6,3 20,12 6,21" /></svg>
                <span>Resume from {step.info.time_string}</span>
              </button>
              <button
                type="button"
                onClick={handleStartOver}
                style={{
                  width: '100%',
                  height: '64px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '16px',
                  padding: '0 24px',
                  borderRadius: '16px',
                  fontSize: '18px',
                  fontWeight: 700,
                  cursor: 'pointer',
                  border: '1px solid rgba(255,255,255,0.12)',
                  background: 'rgba(255,255,255,0.06)',
                  color: '#fff',
                  transition: 'background 0.15s',
                }}
              >
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="1 4 1 10 7 10" />
                  <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
                </svg>
                <span>Play from beginning</span>
              </button>
            </div>
          </div>
        )}

        {/* === Playing step === */}
        {step.type === 'playing' && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '20px', padding: '40px 20px 48px' }}>
            <div style={{
              width: '56px', height: '56px', borderRadius: '50%',
              border: '3px solid rgba(255,255,255,0.15)', borderTopColor: 'rgba(255,255,255,0.7)',
              animation: 'pbSpin 0.8s linear infinite',
            }} />
            <p style={{ fontSize: '18px', fontWeight: 600, color: '#fff', margin: 0 }}>
              {shuffle ? (shuffleLabel ?? 'Shuffling...') : 'Starting playback...'}
            </p>
            <p style={{ fontSize: '15px', color: 'rgba(255,255,255,0.5)', margin: 0 }}>{step.roomName}</p>
          </div>
        )}

        {/* === Success step === */}
        {step.type === 'success' && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '20px', padding: '40px 20px 48px' }}>
            <div style={{
              width: '64px', height: '64px', borderRadius: '50%',
              background: 'rgba(74,222,128,0.15)', border: '2px solid rgba(74,222,128,0.4)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#4ade80" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            </div>
            <p style={{ fontSize: '18px', fontWeight: 600, color: '#4ade80', margin: 0 }}>
              Playing in {step.roomName}
            </p>
          </div>
        )}

        {/* === Error step === */}
        {step.type === 'error' && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '20px', padding: '40px 20px 48px' }}>
            <div style={{
              width: '64px', height: '64px', borderRadius: '50%',
              background: 'rgba(239,68,68,0.15)', border: '2px solid rgba(239,68,68,0.4)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#f87171" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </div>
            <p style={{ fontSize: '18px', fontWeight: 600, color: '#f87171', margin: 0 }}>{step.message}</p>
            <button
              type="button"
              onClick={() => setStep({ type: 'room' })}
              style={{
                width: '100%',
                maxWidth: '300px',
                height: '56px',
                borderRadius: '16px',
                border: '1px solid rgba(255,255,255,0.12)',
                background: 'rgba(255,255,255,0.06)',
                color: '#fff',
                fontSize: '16px',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Try Again
            </button>
          </div>
        )}

          </div>{/* end contentRef */}
        </div>{/* end height wrapper */}

        <style>{`
          @keyframes pbSpin { to { transform: rotate(360deg); } }
        `}</style>
      </div>

    </div>
  );
}
