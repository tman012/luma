/** v291: FullScreenRemote warms Harmony hub on mount when room has hub IP. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/react';
import { FullScreenRemote } from '@/components/rooms/FullScreenRemote';
import type { Room } from '@/hooks/useNotifications';
import { readFileSync } from 'node:fs';

const room: Room = {
  roomId: 'fr',
  roomName: 'Family Room',
  deviceCount: 1,
  session: null,
  activity: null,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('v291 FullScreenRemote Harmony ensure', () => {
  it('POST /harmony/ensure on mount when harmonyHubIp is set', async () => {
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method || 'GET'} ${String(url)}`);
      return new Response(JSON.stringify({ ok: true, warmed: true }), { status: 200 });
    }));
    render(
      <FullScreenRemote
        room={room}
        deviceId="d1"
        harmonyHubIp="192.168.1.19"
        onClose={() => {}}
      />,
    );
    await waitFor(() => {
      expect(calls.some((c) => c.includes('/api/room/fr/harmony/ensure') && c.startsWith('POST'))).toBe(true);
    });
  });

  it('skips ensure when no hub IP', async () => {
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method || 'GET'} ${String(url)}`);
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }));
    render(
      <FullScreenRemote
        room={room}
        deviceId="d1"
        onClose={() => {}}
      />,
    );
    await new Promise((r) => setTimeout(r, 50));
    expect(calls.some((c) => c.includes('harmony/ensure'))).toBe(false);
  });

  it('RoomsSheet passes hubMap harmonyHubIp into FullScreenRemote', () => {
    const src = readFileSync('src/components/rooms/RoomsSheet.tsx', 'utf8');
    expect(src).toContain('harmonyHubIp={hubMap[remoteTarget.roomId]}');
    expect(src).toContain('hubs[r.id] = (r.harmonyHubIp || \'\').trim()');
  });
});
