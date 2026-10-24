/** v265 Rooms: prod's Home Rooms button + RoomsSheet, and Settings > Rooms Pair (prod's ADB flow). All network mocked. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { readFileSync } from 'node:fs';

vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ implicit: false, authenticated: true }) }));
import { HomePills } from '@/components/home/HomePills';
import { RoomsSettings } from '@/components/rooms/RoomsSettings';
import type { Room } from '@/components/rooms/roomsApi';

const P = { id: 'd', name: 'Living Room TV', product: 'Plex for Android (TV)', platform: 'android_tv', machine_id: 'm', host: '192.168.1.41',
  port: 5555, mac: '', harmony_activity: '', paired: false, online: false, wake: 'adb' as const };
const LR: Room = { id: 'lr', name: 'Living Room', default: true, harmony_hub: '', player: P };
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function stub(pair: { ok: boolean; state: string }) {
  const calls: { url: string; method: string }[] = [];
  let paired = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || 'GET';
    calls.push({ url: String(url), method });
    if (String(url).includes('/api/plex/sessions/active')) return json({ rooms: [{ roomId: 'lr', roomName: 'Living Room', deviceCount: 1, session: null, activity: null }], unmatched: [] });
    if (String(url).endsWith('/connect')) { paired = pair.ok; return json({ ...pair, hint: pair.state }); }
    if (String(url).includes('/api/rooms')) return json({ rooms: [{ ...LR, player: { ...P, paired } }], default_room_id: 'lr' });
    return json({});
  }));
  return calls;
}

describe('v265 Pair', () => {
  it('runs the ADB connect and shows Paired and connected', async () => {
    const calls = stub({ ok: true, state: 'connected' });
    render(<RoomsSettings wide={false} />);
    fireEvent.click(await screen.findByTestId('room-row'));
    expect(screen.getByTestId('room-paired').textContent).toBe('Not paired');
    fireEvent.click(screen.getByTestId('room-pair'));
    expect(screen.getByTestId('room-paired').textContent).toBe('Pairing…');
    await waitFor(() => expect(screen.getByTestId('room-paired').textContent).toBe('Paired and connected'));
    expect(calls.filter(c => c.method === 'POST').map(c => c.url)).toEqual(['/api/rooms/lr/connect']);
  });
  it('shows Pairing failed when the TV is not allowed in time', async () => {
    stub({ ok: false, state: 'unauthorized' });
    render(<RoomsSettings wide={false} />);
    fireEvent.click(await screen.findByTestId('room-row'));
    fireEvent.click(screen.getByTestId('room-pair'));
    await waitFor(() => expect(screen.getByTestId('room-paired').textContent).toBe('Pairing failed'));
  });
});

describe('v265 Rooms button and sheet (prod 1b4ea74)', () => {
  it('Home rail has Rooms; it opens the Rooms sheet, which polls sessions only while open', async () => {
    const calls = stub({ ok: true, state: 'connected' });
    render(<MemoryRouter><HomePills /></MemoryRouter>);
    expect(calls.some(c => c.url.includes('/sessions/active'))).toBe(false);
    fireEvent.click(screen.getByTestId('pill-rooms'));
    await waitFor(() => expect(document.body.textContent).toContain('Nothing Playing'));
    expect(document.body.textContent).toContain('Living Room');
    expect(calls.filter(c => c.method === 'POST')).toEqual([]);
  });
  it("sheet and remote text match prod's Title Case", () => {
    const sheet = readFileSync('src/components/rooms/RoomsSheet.tsx', 'utf8');
    const remote = readFileSync('src/components/rooms/FullScreenRemote.tsx', 'utf8');
    expect(sheet).toContain('Nothing Playing'); expect(sheet).toContain('Friends & Family');
    expect(remote).toContain('aria-label="OK / Select"'); expect(remote).toContain('label="Play / Pause"');
  });
});
