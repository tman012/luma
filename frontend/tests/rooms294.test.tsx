/** v294: Open Remote works idle — never silent no-op; Harmony opens with empty deviceId. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { RoomsSheet } from '@/components/rooms/RoomsSheet';
import type { Room } from '@/hooks/useNotifications';

const FR: Room = {
  roomId: '134ccd87',
  roomName: 'Family Room',
  deviceCount: 1,
  session: null,
  activity: null,
};

const LR: Room = {
  roomId: '1aac6f6b',
  roomName: 'Living Room',
  deviceCount: 0,
  session: null,
  activity: null,
};

const shield = {
  id: 'dev-shield',
  name: 'SHIELD',
  platform: 'android_tv' as const,
  host: '192.168.1.126',
  connectPort: 5555,
  paired: true,
  services: [] as { id: string; type: 'plex' | 'kodi' }[],
};

function stubFetch(settingsRooms: unknown[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.includes('/api/settings/rooms')) {
        return new Response(JSON.stringify({
          rooms: settingsRooms,
          defaultPlaybackTarget: { roomId: '', deviceId: '', serviceId: '' },
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      // FullScreenRemote ensure / remote
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }),
  );
}

vi.mock('@/contexts/NotificationsContext', () => ({
  useNotificationsContext: () => ({
    rooms: [FR, LR],
    roomsLoading: false,
    unmatchedSessions: [],
    count: 0,
    items: [],
    downloads: [],
    markSeen: async () => {},
    clearItems: () => {},
    removeDownload: async () => {},
    refreshQueue: async () => {},
  }),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('v294 Open Remote idle', () => {
  it('source: Open Remote uses hasHub / empty deviceId / exact toast copy', () => {
    const src = readFileSync('src/components/rooms/RoomsSheet.tsx', 'utf8');
    expect(src).toContain('Add a device in Settings.');
    expect(src).toContain('hasHub');
    expect(src).toContain('onNeedDevice');
    expect(src).toContain("autoDeviceId || roomDevices?.[0]?.id || ''");
    expect(src).toContain('if (devId || hasHub)');
    // no silent-only guard left as the sole path
    expect(src).not.toMatch(/if \(devId\) onOpenRemote\(devId\);/);
  });

  it('opens FullScreenRemote when deviceMap empty but Harmony hub set', async () => {
    stubFetch([
      {
        id: '134ccd87',
        name: 'Family Room',
        useHarmonyHub: true,
        harmonyHubIp: '192.168.1.19',
        harmonyVolumeDevice: 'Denon AV Receiver',
        devices: [],
      },
    ]);
    render(<RoomsSheet open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText('Family Room')).toBeTruthy());
    fireEvent.click(screen.getByText('Family Room'));
    fireEvent.click(screen.getByText('Open Remote'));
    await waitFor(() => expect(screen.getByLabelText('Volume up')).toBeTruthy());
    expect(screen.queryByTestId('rooms-device-notice')).toBeNull();
  });

  it('opens with roomDevices[0] when idle (no activity) and devices present', async () => {
    stubFetch([
      {
        id: '134ccd87',
        name: 'Family Room',
        useHarmonyHub: true,
        harmonyHubIp: '192.168.1.19',
        harmonyVolumeDevice: 'Denon',
        devices: [shield],
      },
    ]);
    render(<RoomsSheet open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText('Family Room')).toBeTruthy());
    fireEvent.click(screen.getByText('Family Room'));
    fireEvent.click(screen.getByText('Open Remote'));
    await waitFor(() => expect(screen.getByLabelText('Volume up')).toBeTruthy());
  });

  it('toasts when zero devices and no Hub — never silent no-op', async () => {
    stubFetch([
      {
        id: '1aac6f6b',
        name: 'Living Room',
        useHarmonyHub: false,
        harmonyHubIp: '',
        harmonyVolumeDevice: '',
        devices: [],
      },
    ]);
    // Only Living in context for this case — remount mock rooms via override is heavy;
    // click Living Room card from the shared mock list and rely on its hub/device maps.
    render(<RoomsSheet open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText('Living Room')).toBeTruthy());
    fireEvent.click(screen.getByText('Living Room'));
    fireEvent.click(screen.getByText('Open Remote'));
    await waitFor(() => {
      expect(screen.getByTestId('rooms-device-notice').textContent).toBe('Add a device in Settings.');
    });
    expect(screen.queryByLabelText('Volume up')).toBeNull();
  });
});
