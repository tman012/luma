/**
 * RoomAccordion — nested Room > Device > Service accordion with full CRUD.
 * Uses dedicated REST endpoints from settingsApi.
 */

import { useState, useEffect, useCallback } from 'react';
import { cn } from '@/utils/cn';
import { useSettings } from '../useSettings';
import {
  fetchRooms,
  createRoom,
  updateRoom,
  deleteRoom,
  createDevice,
  updateDevice,
  deleteDevice,
  createService,
  updateService,
  deleteService,
  setDefaultPlaybackTarget,
  testDevice,
  pairDevice,
  type RoomData,
  type DeviceData,
  type ServiceData,
} from '@/services/settingsApi';

// ── Chevron icon ─────────────────────────────────────────────────

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn('transition-transform duration-200', open && 'rotate-90')}
    >
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

// ── Platform labels ──────────────────────────────────────────────

const PLATFORMS: { value: string; label: string }[] = [
  { value: 'android_tv', label: 'Shield / Android TV (ADB)' },
  { value: 'chromecast_gtv', label: 'Chromecast w/ Google TV' },
  { value: 'network', label: 'Network (Kodi/Other)' },
];

function platformLabel(p: string) {
  return PLATFORMS.find((x) => x.value === p)?.label ?? p;
}

// ── Inline form input ────────────────────────────────────────────

function InlineInput({
  label,
  value,
  onChange,
  type = 'text',
  placeholder,
  hint,
}: {
  label: string;
  value: string | number;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
  hint?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-[13px] font-medium text-white/60">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2.5 text-[14px] text-white placeholder:text-white/25 outline-none focus:border-white/20"
      />
      {hint && <span className="text-[11px] text-white/30">{hint}</span>}
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────

export function RoomAccordion() {
  const { showToast } = useSettings();

  const [rooms, setRooms] = useState<RoomData[]>([]);
  const [defaultTarget, setDefaultTarget] = useState({ roomId: '', deviceId: '', serviceId: '' });
  const [loading, setLoading] = useState(true);

  // Expand states
  const [expandedRooms, setExpandedRooms] = useState<Set<string>>(new Set());
  const [expandedDevices, setExpandedDevices] = useState<Set<string>>(new Set());

  // Form states
  const [roomForm, setRoomForm] = useState<Partial<RoomData> | null>(null);
  const [roomEditId, setRoomEditId] = useState<string | null>(null);
  const [deviceForm, setDeviceForm] = useState<Partial<DeviceData> | null>(null);
  const [deviceEditId, setDeviceEditId] = useState<string | null>(null);
  const [deviceRoomId, setDeviceRoomId] = useState('');
  const [serviceForm, setServiceForm] = useState<Partial<ServiceData> | null>(null);
  const [serviceEditId, setServiceEditId] = useState<string | null>(null);
  const [serviceRoomId, setServiceRoomId] = useState('');
  const [serviceDeviceId, setServiceDeviceId] = useState('');

  // Test / pair status
  const [testStatus, setTestStatus] = useState<Record<string, { ok: boolean; msg: string }>>({});
  const [pairForm, setPairForm] = useState<{
    roomId: string;
    deviceId: string;
    pairPort: string;
    pairCode: string;
  } | null>(null);

  const loadRooms = useCallback(async () => {
    try {
      const data = await fetchRooms();
      setRooms(data.rooms);
      setDefaultTarget(data.defaultPlaybackTarget);
    } catch (e) {
      showToast(`Failed to load rooms: ${(e as Error).message}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadRooms();
  }, [loadRooms]);

  // ── Room CRUD ────────────────────────────────────────────────

  const openRoomForm = (room?: RoomData) => {
    if (room) {
      setRoomForm({ name: room.name, useHarmonyHub: room.useHarmonyHub, harmonyHubIp: room.harmonyHubIp, harmonyVolumeDevice: room.harmonyVolumeDevice });
      setRoomEditId(room.id);
    } else {
      setRoomForm({ name: '', useHarmonyHub: false, harmonyHubIp: '', harmonyVolumeDevice: '' });
      setRoomEditId(null);
    }
  };

  const saveRoomForm = async () => {
    if (!roomForm?.name?.trim()) {
      showToast('Room name is required', 'error');
      return;
    }
    try {
      if (roomEditId) {
        await updateRoom(roomEditId, roomForm as Partial<RoomData>);
        showToast('Room updated', 'success');
        setRoomForm(null);
        setRoomEditId(null);
        await loadRooms();
      } else {
        const { room } = await createRoom(roomForm as Partial<RoomData>);
        showToast('Room created — add a device next', 'success');
        setRoomForm(null);
        setRoomEditId(null);
        await loadRooms();
        // Expand the new room and open the Add Device form immediately
        setExpandedRooms((prev) => new Set(prev).add(room.id));
        openDeviceForm(room.id);
      }
    } catch (e) {
      showToast((e as Error).message, 'error');
    }
  };

  const removeRoom = async (id: string) => {
    try {
      await deleteRoom(id);
      showToast('Room deleted', 'success');
      await loadRooms();
    } catch (e) {
      showToast((e as Error).message, 'error');
    }
  };

  // ── Device CRUD ──────────────────────────────────────────────

  const openDeviceForm = (roomId: string, device?: DeviceData) => {
    setDeviceRoomId(roomId);
    if (device) {
      setDeviceForm({
        name: device.name,
        platform: device.platform,
        host: device.host,
        connectPort: device.connectPort,
        pairPort: device.pairPort,
        harmonyActivity: device.harmonyActivity ?? '',
        harmonyNavigationDevice: device.harmonyNavigationDevice ?? '',
        macAddress: device.macAddress ?? '',
        powerMethod: device.powerMethod ?? 'adb',
        haPowerEntity: device.haPowerEntity ?? '',
      });
      setDeviceEditId(device.id);
    } else {
      setDeviceForm({
        name: '',
        platform: 'chromecast_gtv',
        host: '',
        // Google TV wireless debugging shows its own port on-screen — do not assume 5555
        connectPort: undefined,
        pairPort: null,
        powerMethod: 'adb',
        macAddress: '',
      });
      setDeviceEditId(null);
    }
  };

  const saveDeviceForm = async () => {
    if (!deviceForm?.name?.trim()) {
      showToast('Device name is required', 'error');
      return;
    }
    if (!deviceEditId && !deviceForm.host?.trim() && deviceForm.platform !== 'network') {
      showToast('Host / IP is required for ADB devices', 'error');
      return;
    }
    if (
      !deviceEditId
      && deviceForm.platform === 'chromecast_gtv'
      && (deviceForm.connectPort == null || Number.isNaN(Number(deviceForm.connectPort)))
    ) {
      showToast('Enter the Wireless debugging port shown on the TV (not 5555)', 'error');
      return;
    }
    try {
      if (deviceEditId) {
        await updateDevice(deviceRoomId, deviceEditId, deviceForm);
        showToast('Device updated', 'success');
        setDeviceForm(null);
        setDeviceEditId(null);
        await loadRooms();
      } else {
        const roomId = deviceRoomId;
        const { device } = await createDevice(roomId, deviceForm);
        showToast('Device added — add a playback service next', 'success');
        setDeviceForm(null);
        setDeviceEditId(null);
        await loadRooms();
        setExpandedDevices((prev) => new Set(prev).add(device.id));
        // Default service: Plex for ADB TVs, Kodi for network boxes
        const defaultType = deviceForm.platform === 'network' ? 'kodi' : 'plex';
        openServiceForm(roomId, device.id);
        setServiceForm(
          defaultType === 'plex'
            ? { type: 'plex' }
            : { type: 'kodi', kodi_host: '', kodi_port: 8080, kodi_username: '', kodi_password: '' },
        );
      }
    } catch (e) {
      showToast((e as Error).message, 'error');
    }
  };

  const removeDevice = async (roomId: string, deviceId: string) => {
    try {
      await deleteDevice(roomId, deviceId);
      showToast('Device deleted', 'success');
      await loadRooms();
    } catch (e) {
      showToast((e as Error).message, 'error');
    }
  };

  const handleTestDevice = async (roomId: string, deviceId: string) => {
    setTestStatus((prev) => ({ ...prev, [deviceId]: { ok: false, msg: 'Testing...' } }));
    try {
      const r = await testDevice(roomId, deviceId);
      const ok = Boolean((r as { success?: boolean; ok?: boolean }).success ?? (r as { ok?: boolean }).ok);
      const msg = r.message || (ok ? 'Connected' : 'Connection failed');
      setTestStatus((prev) => ({ ...prev, [deviceId]: { ok, msg } }));
      // If GTV needs pairing, open the pair form automatically
      const device = rooms.find((x) => x.id === roomId)?.devices.find((d) => d.id === deviceId);
      if (!ok && device?.platform === 'chromecast_gtv') {
        setPairForm({
          roomId,
          deviceId,
          pairPort: device.pairPort != null ? String(device.pairPort) : '',
          pairCode: '',
        });
      }
    } catch (e) {
      setTestStatus((prev) => ({ ...prev, [deviceId]: { ok: false, msg: (e as Error).message } }));
    }
  };

  const handlePairDevice = async () => {
    if (!pairForm) return;
    const port = Number(pairForm.pairPort);
    const code = pairForm.pairCode.trim();
    if (!port || Number.isNaN(port)) {
      showToast('Enter the pairing port from the TV Pair screen', 'error');
      return;
    }
    if (!code) {
      showToast('Enter the 6-digit pairing code from the TV', 'error');
      return;
    }
    const { roomId, deviceId } = pairForm;
    setTestStatus((prev) => ({ ...prev, [deviceId]: { ok: false, msg: 'Pairing…' } }));
    try {
      const r = await pairDevice(roomId, deviceId, port, code);
      const ok = Boolean((r as { success?: boolean; ok?: boolean }).ok ?? (r as { success?: boolean }).success);
      const msg = r.message || (ok ? 'Paired and connected' : 'Pairing failed');
      setTestStatus((prev) => ({ ...prev, [deviceId]: { ok, msg } }));
      if (ok) {
        showToast('Paired and connected via ADB', 'success');
        setPairForm(null);
        // Persist pairPort on the device
        await updateDevice(roomId, deviceId, { pairPort: port, paired: true });
        await loadRooms();
      } else {
        showToast(msg, 'error');
      }
    } catch (e) {
      const msg = (e as Error).message;
      setTestStatus((prev) => ({ ...prev, [deviceId]: { ok: false, msg } }));
      showToast(msg, 'error');
    }
  };

  // ── Service CRUD ─────────────────────────────────────────────

  const openServiceForm = (roomId: string, deviceId: string, service?: ServiceData) => {
    setServiceRoomId(roomId);
    setServiceDeviceId(deviceId);
    if (service) {
      setServiceForm({ ...service });
      setServiceEditId(service.id);
    } else {
      setServiceForm({ type: 'plex' });
      setServiceEditId(null);
    }
  };

  const saveServiceForm = async () => {
    try {
      if (serviceEditId) {
        await updateService(serviceRoomId, serviceDeviceId, serviceEditId, serviceForm as Partial<ServiceData>);
        showToast('Service updated', 'success');
      } else {
        await createService(serviceRoomId, serviceDeviceId, serviceForm as Partial<ServiceData>);
        showToast('Service added', 'success');
      }
      setServiceForm(null);
      setServiceEditId(null);
      await loadRooms();
    } catch (e) {
      showToast((e as Error).message, 'error');
    }
  };

  const removeService = async (roomId: string, deviceId: string, serviceId: string) => {
    try {
      await deleteService(roomId, deviceId, serviceId);
      showToast('Service deleted', 'success');
      await loadRooms();
    } catch (e) {
      showToast((e as Error).message, 'error');
    }
  };

  // ── Default target ───────────────────────────────────────────

  const setDefault = async (roomId: string, deviceId: string, serviceId: string) => {
    try {
      await setDefaultPlaybackTarget({ roomId, deviceId, serviceId });
      setDefaultTarget({ roomId, deviceId, serviceId });
      showToast('Default playback target set', 'success');
    } catch (e) {
      showToast((e as Error).message, 'error');
    }
  };

  // ── Toggle expand ────────────────────────────────────────────

  const toggleRoom = (id: string) => {
    setExpandedRooms((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleDevice = (id: string) => {
    setExpandedDevices((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  // ── Render ───────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-6 overflow-visible">
        <div className="flex items-center gap-3 animate-pulse">
          <div className="h-[42px] w-[42px] rounded-[10px] bg-white/5" />
          <div className="h-5 w-40 rounded bg-white/5" />
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-6">
      {/* Header */}
      <div className="mb-5 flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="flex h-[42px] w-[42px] items-center justify-center rounded-[10px] bg-white/5 text-white/50 shrink-0">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="2" y="7" width="20" height="15" rx="2" ry="2" /><polyline points="17 2 12 7 7 2" />
            </svg>
          </div>
          <div>
            <h3 className="text-[15px] font-semibold text-white/90">Playback Rooms</h3>
            <p className="mt-0.5 text-[12px] text-white/35">Multi-room playback and ADB control</p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => openRoomForm()}
          className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500 px-3 py-2 text-[12px] font-semibold text-white hover:bg-emerald-600 min-h-[36px]"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          Add Room
        </button>
      </div>

      {/* Room list */}
      <div className="flex flex-col gap-2">
        {rooms.length === 0 && !roomForm && (
          <p className="py-6 text-center text-[13px] text-white/25">No rooms configured</p>
        )}

        {rooms.map((room) => {
          const isOpen = expandedRooms.has(room.id);
          return (
            <div key={room.id} className="rounded-xl border border-white/[0.06] bg-white/[0.02] overflow-hidden">
              {/* Room header */}
              <div className="flex items-center gap-3 px-4 py-3">
                <button type="button" onClick={() => toggleRoom(room.id)} className="text-white/30 hover:text-white/60 p-1">
                  <ChevronIcon open={isOpen} />
                </button>
                <div className="flex-1 min-w-0">
                  <span className="text-[14px] font-semibold text-white/85">{room.name}</span>
                  <span className="ml-2 text-[11px] text-white/30">{room.devices.length} device{room.devices.length !== 1 ? 's' : ''}</span>
                </div>
                <div className="flex gap-1.5">
                  <button type="button" onClick={() => openRoomForm(room)} className="rounded-lg p-2 text-white/25 hover:text-white/60 hover:bg-white/5" aria-label="Edit room">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" /></svg>
                  </button>
                  <button type="button" onClick={() => removeRoom(room.id)} className="rounded-lg p-2 text-white/25 hover:text-red-400 hover:bg-red-500/10" aria-label="Delete room">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                  </button>
                </div>
              </div>

              {/* Room body */}
              {isOpen && (
                <div className="border-t border-white/[0.04] px-5 py-4 space-y-3">
                  {/* Devices */}
                  {room.devices.map((device) => {
                    const devOpen = expandedDevices.has(device.id);
                    const ts = testStatus[device.id];
                    const isDefault =
                      defaultTarget.roomId === room.id &&
                      defaultTarget.deviceId === device.id;

                    return (
                      <div key={device.id} className="rounded-lg border border-white/[0.05] bg-white/[0.02]">
                        <div className="flex items-center gap-3 px-3 py-2.5">
                          <button type="button" onClick={() => toggleDevice(device.id)} className="text-white/30 hover:text-white/60 p-1">
                            <ChevronIcon open={devOpen} />
                          </button>
                          <div className="flex-1 min-w-0">
                            <span className="text-[13px] font-medium text-white/80">{device.name}</span>
                            <span className="ml-2 text-[11px] text-white/25">{platformLabel(device.platform)}</span>
                            {isDefault && (
                              <span className="ml-2 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase bg-emerald-500/15 text-emerald-400">
                                Default
                              </span>
                            )}
                          </div>
                          <div className="flex gap-1">
                            {device.platform !== 'network' && (
                              <button type="button" onClick={() => handleTestDevice(room.id, device.id)} className="rounded-lg px-2 py-1.5 text-[11px] text-indigo-400 bg-indigo-500/10 hover:bg-indigo-500/20">
                                Test
                              </button>
                            )}
                            {device.platform === 'chromecast_gtv' && (
                              <button
                                type="button"
                                onClick={() =>
                                  setPairForm({
                                    roomId: room.id,
                                    deviceId: device.id,
                                    pairPort: device.pairPort != null ? String(device.pairPort) : '',
                                    pairCode: '',
                                  })
                                }
                                className="rounded-lg px-2 py-1.5 text-[11px] text-amber-300 bg-amber-500/10 hover:bg-amber-500/20"
                              >
                                Pair
                              </button>
                            )}
                            {!isDefault && (
                              <button
                                type="button"
                                onClick={() => setDefault(room.id, device.id, device.services[0]?.id ?? '')}
                                className="rounded-lg px-2 py-1.5 text-[11px] text-white/40 hover:text-emerald-400 hover:bg-emerald-500/10"
                              >
                                Set Default
                              </button>
                            )}
                            <button type="button" onClick={() => openDeviceForm(room.id, device)} className="rounded-lg p-1.5 text-white/25 hover:text-white/60 hover:bg-white/5" aria-label="Edit device">
                              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" /></svg>
                            </button>
                            <button type="button" onClick={() => removeDevice(room.id, device.id)} className="rounded-lg p-1.5 text-white/25 hover:text-red-400 hover:bg-red-500/10" aria-label="Delete device">
                              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                            </button>
                          </div>
                        </div>

                        {/* Test status */}
                        {ts && (
                          <div className={cn('px-4 py-2 text-[11px]', ts.ok ? 'text-green-400' : 'text-red-400')}>
                            {ts.msg}
                          </div>
                        )}

                        {/* In-app wireless debugging pair (Google TV) */}
                        {pairForm && pairForm.deviceId === device.id && (
                          <div
                            ref={(el) => { el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }}
                            className="mx-3 mb-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 space-y-2"
                          >
                            <p className="text-[12px] font-semibold text-amber-200/90">Wireless debugging pair</p>
                            <p className="text-[11px] text-white/45">
                              On the TV: Developer options → Wireless debugging → Pair device with pairing code.
                              Enter the <span className="text-white/70">pairing port</span> and <span className="text-white/70">6-digit code</span> shown there.
                              Connect port stays <span className="text-white/70">{device.connectPort || '—'}</span>.
                            </p>
                            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                              <InlineInput
                                label="Pairing port"
                                value={pairForm.pairPort}
                                onChange={(v) => setPairForm((p) => (p ? { ...p, pairPort: v } : p))}
                                type="number"
                                placeholder="e.g. 37123"
                              />
                              <InlineInput
                                label="Pairing code"
                                value={pairForm.pairCode}
                                onChange={(v) => setPairForm((p) => (p ? { ...p, pairCode: v } : p))}
                                placeholder="6-digit code"
                              />
                            </div>
                            <div className="flex gap-2 pt-1">
                              <button
                                type="button"
                                onClick={handlePairDevice}
                                className="rounded-lg bg-amber-500 px-3 py-1.5 text-[12px] font-semibold text-black hover:bg-amber-400"
                              >
                                Pair &amp; Connect
                              </button>
                              <button
                                type="button"
                                onClick={() => setPairForm(null)}
                                className="rounded-lg border border-white/10 bg-white/[0.06] px-3 py-1.5 text-[12px] text-white/60 hover:bg-white/10"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        )}

                        {/* Device body: services */}
                        {devOpen && (
                          <div className="border-t border-white/[0.04] px-4 py-3">
                            <div className="mb-2 flex items-center justify-between">
                              <span className="text-[11px] font-semibold uppercase text-white/25 tracking-wider">Services</span>
                              <button
                                type="button"
                                onClick={() => openServiceForm(room.id, device.id)}
                                className="rounded-lg px-2 py-1 text-[11px] text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/20"
                              >
                                + Add
                              </button>
                            </div>
                            {device.services.length === 0 && (
                              <p className="py-2 text-[12px] text-white/20">No services</p>
                            )}
                            {device.services.map((svc) => (
                              <div key={svc.id} className="flex items-center gap-3 py-1.5">
                                <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase bg-indigo-500/15 text-indigo-400">
                                  {svc.type}
                                </span>
                                {svc.type === 'kodi' && svc.kodi_host && (
                                  <span className="text-[12px] text-white/40">{svc.kodi_host}:{svc.kodi_port}</span>
                                )}
                                <div className="flex-1" />
                                <button type="button" onClick={() => openServiceForm(room.id, device.id, svc)} className="rounded-lg p-1.5 text-white/25 hover:text-white/60 hover:bg-white/5">
                                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" /></svg>
                                </button>
                                <button type="button" onClick={() => removeService(room.id, device.id, svc.id)} className="rounded-lg p-1.5 text-white/25 hover:text-red-400 hover:bg-red-500/10">
                                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                                </button>
                              </div>
                            ))}

                            {/* Inline service form */}
                            {serviceForm && serviceRoomId === room.id && serviceDeviceId === device.id && (
                              <div className="mt-2 rounded-lg border border-dashed border-white/10 bg-white/[0.02] p-4">
                                <p className="mb-3 text-[13px] font-semibold text-white/50">{serviceEditId ? 'Edit Service' : 'Add Service'}</p>
                                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                                  <div className="flex flex-col gap-1.5">
                                    <label className="text-[13px] font-medium text-white/60">Type</label>
                                    <select
                                      value={serviceForm.type ?? 'kodi'}
                                      onChange={(e) => setServiceForm((p) => ({ ...p, type: e.target.value as 'kodi' | 'plex' }))}
                                      className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2.5 text-[14px] text-white outline-none"
                                    >
                                      <option value="kodi">Kodi</option>
                                      <option value="plex">Plex</option>
                                    </select>
                                  </div>
                                  {serviceForm.type === 'kodi' && (
                                    <>
                                      <InlineInput label="Kodi Host" value={serviceForm.kodi_host ?? ''} onChange={(v) => setServiceForm((p) => ({ ...p, kodi_host: v }))} placeholder="192.168.1.10" hint="Leave blank to use device IP" />
                                      <InlineInput label="Kodi Port" value={String(serviceForm.kodi_port ?? 8080)} onChange={(v) => setServiceForm((p) => ({ ...p, kodi_port: Number(v) }))} type="number" placeholder="8080" />
                                      <InlineInput label="Username" value={serviceForm.kodi_username ?? ''} onChange={(v) => setServiceForm((p) => ({ ...p, kodi_username: v }))} placeholder="Optional" />
                                      <InlineInput label="Password" value={serviceForm.kodi_password ?? ''} onChange={(v) => setServiceForm((p) => ({ ...p, kodi_password: v }))} placeholder="Optional" />
                                    </>
                                  )}
                                </div>
                                <div className="mt-3 flex gap-2">
                                  <button type="button" onClick={saveServiceForm} className="rounded-lg bg-emerald-500 px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-emerald-600">Save</button>
                                  <button type="button" onClick={() => { setServiceForm(null); setServiceEditId(null); }} className="rounded-lg border border-white/10 bg-white/[0.06] px-3 py-1.5 text-[12px] text-white/60 hover:bg-white/10">Cancel</button>
                                </div>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}

                  {room.devices.length === 0 && !deviceForm && (
                    <p className="rounded-lg border border-dashed border-white/8 bg-white/[0.015] px-3 py-3 text-center text-[12px] text-white/35">
                      No devices yet. Add a TV, Shield, or network player to this room.
                    </p>
                  )}

                  {/* Add device button */}
                  <button
                    type="button"
                    onClick={() => openDeviceForm(room.id)}
                    className="w-full rounded-lg border border-dashed border-emerald-500/25 py-2.5 text-[12px] font-medium text-emerald-400/80 hover:text-emerald-300 hover:border-emerald-500/40 hover:bg-emerald-500/5 transition-colors"
                  >
                    + Add Device
                  </button>

                  {/* Inline device form */}
                  {deviceForm && deviceRoomId === room.id && (
                    <div
                      ref={(el) => { el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }}
                      className="rounded-lg border border-dashed border-emerald-500/30 bg-white/[0.02] p-4"
                    >
                      <p className="mb-1 text-[13px] font-semibold text-white/50">{deviceEditId ? 'Edit Device' : 'Add Device'}</p>
                      {!deviceEditId && deviceForm.platform === 'chromecast_gtv' && (
                        <p className="mb-3 text-[11px] text-amber-300/80">
                          Google TV uses a dynamic Wireless debugging port from the TV screen — do not use 5555 unless the TV shows it.
                        </p>
                      )}
                      {!(!deviceEditId && deviceForm.platform === 'chromecast_gtv') && (
                        <div className="mb-3" />
                      )}
                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                        <InlineInput label="Name" value={deviceForm.name ?? ''} onChange={(v) => setDeviceForm((p) => ({ ...p, name: v }))} placeholder="e.g. Shield TV Pro" />
                        <div className="flex flex-col gap-1.5">
                          <label className="text-[13px] font-medium text-white/60">Platform</label>
                          <select
                            value={deviceForm.platform ?? 'android_tv'}
                            onChange={(e) => setDeviceForm((p) => ({ ...p, platform: e.target.value as DeviceData['platform'] }))}
                            className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2.5 text-[14px] text-white outline-none"
                          >
                            {PLATFORMS.map((p) => (
                              <option key={p.value} value={p.value}>{p.label}</option>
                            ))}
                          </select>
                        </div>
                        <InlineInput
                          label="Host / IP"
                          value={deviceForm.host ?? ''}
                          onChange={(v) => setDeviceForm((p) => ({ ...p, host: v }))}
                          placeholder="192.168.1.10"
                          hint={
                            deviceForm.platform === 'chromecast_gtv'
                              ? 'Settings → System → Developer options → Wireless debugging'
                              : deviceForm.platform === 'android_tv'
                                ? 'Shield / Android TV ADB IP (often :5555)'
                                : 'Network player IP (no ADB)'
                          }
                        />
                        {deviceForm.platform !== 'network' && (
                          <InlineInput
                            label={deviceForm.platform === 'chromecast_gtv' ? 'Wireless debugging port' : 'Connect Port'}
                            value={deviceForm.connectPort != null && !Number.isNaN(Number(deviceForm.connectPort)) ? String(deviceForm.connectPort) : ''}
                            onChange={(v) => setDeviceForm((p) => ({ ...p, connectPort: v === '' ? undefined : Number(v) }))}
                            type="number"
                            placeholder={deviceForm.platform === 'chromecast_gtv' ? 'e.g. 37411 (from TV screen)' : '5555'}
                            hint={
                              deviceForm.platform === 'chromecast_gtv'
                                ? 'Not 5555 — copy the IP & port shown under Wireless debugging on the TV'
                                : 'Shields usually use 5555'
                            }
                          />
                        )}
                        {deviceForm.platform === 'chromecast_gtv' && (
                          <InlineInput
                            label="Pairing port (optional)"
                            value={deviceForm.pairPort != null ? String(deviceForm.pairPort) : ''}
                            onChange={(v) => setDeviceForm((p) => ({ ...p, pairPort: v === '' ? null : Number(v) }))}
                            type="number"
                            placeholder="Shown when you tap Pair on the TV"
                            hint="Only needed once: Wireless debugging → Pair device with pairing code"
                          />
                        )}
                        <InlineInput label="MAC Address" value={deviceForm.macAddress ?? ''} onChange={(v) => setDeviceForm((p) => ({ ...p, macAddress: v }))} placeholder="AA:BB:CC:DD:EE:FF" hint="For Wake-on-LAN" />
                        {deviceForm.platform !== 'network' && (
                          <div className="flex flex-col gap-1.5">
                            <label className="text-[13px] font-medium text-white/60">Power Method</label>
                            <select
                              value={deviceForm.powerMethod ?? 'adb'}
                              onChange={(e) => setDeviceForm((p) => ({ ...p, powerMethod: e.target.value as 'adb' | 'homeassistant' }))}
                              className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2.5 text-[14px] text-white outline-none"
                            >
                              <option value="adb">ADB</option>
                              <option value="homeassistant">Home Assistant</option>
                            </select>
                            <span className="text-[11px] text-white/30">How to wake / power-toggle the device</span>
                          </div>
                        )}
                        {deviceForm.platform !== 'network' && deviceForm.powerMethod === 'homeassistant' && (
                          <InlineInput label="HA Power Entity" value={deviceForm.haPowerEntity ?? ''} onChange={(v) => setDeviceForm((p) => ({ ...p, haPowerEntity: v }))} placeholder="scene.living_room_tv_power" hint="Home Assistant entity to fire for power" />
                        )}
                        {room.useHarmonyHub && (
                          <InlineInput label="Harmony Activity" value={deviceForm.harmonyActivity ?? ''} onChange={(v) => setDeviceForm((p) => ({ ...p, harmonyActivity: v }))} placeholder="Watch Shield" hint="Harmony activity name for power on" />
                        )}
                        {room.useHarmonyHub && (
                          <InlineInput label="Harmony Nav Device" value={deviceForm.harmonyNavigationDevice ?? ''} onChange={(v) => setDeviceForm((p) => ({ ...p, harmonyNavigationDevice: v }))} placeholder="NVIDIA Shield" hint="Harmony device name for d-pad/nav" />
                        )}
                      </div>
                      <div className="mt-3 flex gap-2">
                        <button type="button" onClick={saveDeviceForm} className="rounded-lg bg-emerald-500 px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-emerald-600">Save</button>
                        <button type="button" onClick={() => { setDeviceForm(null); setDeviceEditId(null); }} className="rounded-lg border border-white/10 bg-white/[0.06] px-3 py-1.5 text-[12px] text-white/60 hover:bg-white/10">Cancel</button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Room form */}
      {roomForm && (
        <div
          ref={(el) => { el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }}
          className="mt-3 rounded-xl border border-dashed border-emerald-500/30 bg-white/[0.02] p-5"
        >
          <p className="mb-1 text-[14px] font-semibold text-white/60">
            {roomEditId ? 'Edit Room' : 'Add Room'}
          </p>
          {!roomEditId && (
            <p className="mb-4 text-[12px] text-white/35">
              Rooms are just locations. You&apos;ll add the TV/Shield as a device next — nothing is auto-created.
            </p>
          )}
          {roomEditId && <div className="mb-4" />}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <InlineInput
              label="Room Name"
              value={roomForm.name ?? ''}
              onChange={(v) => setRoomForm((p) => ({ ...p, name: v }))}
              placeholder="e.g. Family Room"
            />
          </div>
          <div className="mt-4 flex items-center gap-3">
            <button
              type="button"
              role="switch"
              aria-checked={roomForm.useHarmonyHub ?? false}
              onClick={() => setRoomForm((p) => ({ ...p, useHarmonyHub: !p?.useHarmonyHub }))}
              className={cn(
                'relative h-[26px] w-[46px] shrink-0 rounded-full transition-colors',
                roomForm.useHarmonyHub ? 'bg-emerald-500' : 'bg-white/15',
              )}
            >
              <span className={cn('absolute top-[3px] left-[3px] h-[20px] w-[20px] rounded-full bg-white shadow-sm transition-transform', roomForm.useHarmonyHub && 'translate-x-[20px]')} />
            </button>
            <span className="text-[13px] text-white/60">Uses Harmony Hub</span>
          </div>
          {roomForm.useHarmonyHub && (
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <InlineInput label="Harmony Hub IP" value={roomForm.harmonyHubIp ?? ''} onChange={(v) => setRoomForm((p) => ({ ...p, harmonyHubIp: v }))} placeholder="192.168.1.50" />
              <InlineInput label="Volume Device" value={roomForm.harmonyVolumeDevice ?? ''} onChange={(v) => setRoomForm((p) => ({ ...p, harmonyVolumeDevice: v }))} placeholder="Denon AV Receiver" />
            </div>
          )}
          <div className="mt-4 flex gap-2.5">
            <button type="button" onClick={saveRoomForm} className="rounded-lg bg-emerald-500 px-3.5 py-2 text-[12px] font-semibold text-white hover:bg-emerald-600 min-h-[36px]">
              {roomEditId ? 'Update Room' : 'Save Room'}
            </button>
            <button type="button" onClick={() => { setRoomForm(null); setRoomEditId(null); }} className="rounded-lg border border-white/10 bg-white/[0.06] px-3.5 py-2 text-[12px] text-white/60 hover:bg-white/10 min-h-[36px]">
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
