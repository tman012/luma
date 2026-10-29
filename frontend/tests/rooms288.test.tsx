/** v288: Rooms sheet shows Volume device + Harmony nav device when wake=Harmony. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RoomsSettings } from '@/components/rooms/RoomsSettings';
import type { Room } from '@/components/rooms/roomsApi';

type Call = { url: string; method: string; body: unknown };

const P = (o: Partial<NonNullable<Room['player']>> = {}) => ({
  id: 'd1', name: 'SHIELD Android TV', product: 'Plex for Android (TV)', platform: 'android_tv',
  machine_id: 'mid-shield', host: '192.168.1.40', port: 5555, mac: '', harmony_activity: '',
  harmony_nav_device: '', paired: true, online: true, wake: 'adb' as const, ...o,
});
const LR: Room = { id: 'lr', name: 'Living Room', default: true, harmony_hub: '', harmony_volume_device: '', player: P() };

function stub(rooms: Room[], calls: Call[]) {
  let view = { rooms, default_room_id: rooms.find(r => r.default)?.id || '' };
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url: String(url), method, body });
    if (String(url).endsWith('/players')) return new Response(JSON.stringify({ players: [] }), { status: 200 });
    if (method === 'PUT') {
      const id = String(url).split('/').pop()!;
      const patch = body as Record<string, unknown>;
      view = {
        ...view,
        rooms: view.rooms.map(r => {
          if (r.id !== id) return r;
          const player = r.player ? {
            ...r.player,
            ...(patch.wake != null ? { wake: patch.wake as Room['player'] extends infer P ? P extends { wake: infer W } ? W : never : never } : {}),
            ...(patch.harmony_activity != null ? { harmony_activity: String(patch.harmony_activity) } : {}),
            ...(patch.harmony_nav_device != null ? { harmony_nav_device: String(patch.harmony_nav_device) } : {}),
          } : r.player;
          return {
            ...r,
            ...(patch.harmony_hub != null ? { harmony_hub: String(patch.harmony_hub) } : {}),
            ...(patch.harmony_volume_device != null ? { harmony_volume_device: String(patch.harmony_volume_device) } : {}),
            player,
          };
        }),
      };
      return new Response(JSON.stringify(view), { status: 200 });
    }
    if (method === 'GET' && String(url).includes('/options/harmony')) {
      return new Response(JSON.stringify({ ok: true, activities: ['Watch TV', 'SHIELD TV'], devices: ['AV Receiver', 'NVIDIA Shield'] }), { status: 200 });
    }
    if (method === 'GET') return new Response(JSON.stringify(view), { status: 200 });
    return new Response(JSON.stringify({ errors: { _: 'no' } }), { status: 400 });
  }));
}

const writes = (calls: Call[]) => calls.filter(c => c.method !== 'GET');

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('v288 Rooms sheet Harmony devices', () => {
  it('premium pass: Harmony shows Hub + Activity/Volume pickers (and Navigation under Advanced) fed by the hub, and saves them', async () => {
    const calls: Call[] = [];
    stub([{ ...LR, player: P() }], calls);
    render(<RoomsSettings wide={false} />);
    await waitFor(() => expect(screen.getAllByTestId('room-row')).toHaveLength(1));
    fireEvent.click(screen.getAllByTestId('room-row')[0]!);
    fireEvent.click(screen.getByTestId('room-wake-harmony'));
    expect(screen.getByTestId('room-hub')).toBeTruthy();
    expect(screen.getByTestId('room-activity')).toBeTruthy();
    expect(screen.getByTestId('room-volume-device')).toBeTruthy();
    expect(screen.getByTestId('room-activity').tagName).toBe('SELECT');      // pickers, not raw text
    expect(screen.getByTestId('room-volume-device').tagName).toBe('SELECT');
    expect(screen.getByLabelText('Volume')).toBeTruthy();
    fireEvent.click(screen.getByTestId('room-advanced-toggle'));
    expect(screen.getByLabelText('Navigation')).toBeTruthy();
    fireEvent.change(screen.getByTestId('room-hub'), { target: { value: '192.168.1.19' } });
    await waitFor(() => expect(screen.getByTestId('room-activity').querySelectorAll('option').length).toBeGreaterThan(2), { timeout: 2000 });
    expect(calls.some(c => c.url === '/api/rooms/options/harmony?hub=192.168.1.19')).toBe(true);
    fireEvent.change(screen.getByTestId('room-activity'), { target: { value: 'SHIELD TV' } });
    fireEvent.change(screen.getByTestId('room-volume-device'), { target: { value: 'AV Receiver' } });
    fireEvent.change(screen.getByTestId('room-nav-device'), { target: { value: 'NVIDIA Shield' } });
    fireEvent.click(screen.getByTestId('room-save'));
    await waitFor(() => expect(writes(calls).some(c => c.method === 'PUT')).toBe(true));
    const put = writes(calls).find(c => c.method === 'PUT')!;
    expect(put.body).toMatchObject({
      wake: 'harmony',
      harmony_hub: '192.168.1.19',
      harmony_activity: 'SHIELD TV',
      harmony_volume_device: 'AV Receiver',
      harmony_nav_device: 'NVIDIA Shield',
    });
  });

  it('hides Volume/Nav fields when wake is not Harmony', async () => {
    stub([LR], []);
    render(<RoomsSettings wide={false} />);
    await waitFor(() => expect(screen.getAllByTestId('room-row')).toHaveLength(1));
    fireEvent.click(screen.getAllByTestId('room-row')[0]!);
    expect(screen.queryByTestId('room-volume-device')).toBeNull();
    expect(screen.queryByTestId('room-nav-device')).toBeNull();
    fireEvent.click(screen.getByTestId('room-wake-none'));
    expect(screen.queryByTestId('room-volume-device')).toBeNull();
  });
});
