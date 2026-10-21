/** v225 playback wired to Rooms (Option A): prod's PlaybackModal / MobilePlaybackSheet start room first.
 * Every request is mocked; play/playqueue calls are recorded, never sent anywhere. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { PlaybackModal } from '@/components/media/PlaybackModal';
import { MobilePlaybackSheet } from '@/components/media/MovieDetailModal/MobilePlaybackSheet';

const dev = (id: string, host = '192.168.1.40') => ({ id, name: id, platform: 'android_tv', host, connectPort: 5555, services: [{ id: `${id}-p`, type: 'plex' }] });
const LR = { id: 'lr', name: 'Living Room', devices: [dev('shield')] };
const BR = { id: 'br', name: 'Bedroom', devices: [dev('atv', '192.168.1.41')] };
const EMPTY = { id: 'of', name: 'Office', devices: [dev('x', '')] };
const ROOMS_VIEW = { rooms: [
  { id: 'lr', name: 'Living Room', default: false, harmony_hub: '', player: { name: 'SHIELD Android TV', product: 'Plex for Android (TV)', online: true } },
  { id: 'br', name: 'Bedroom', default: true, harmony_hub: '', player: { name: 'Apple TV 4K', product: 'Plex for Apple TV', online: false } }], default_room_id: 'br' };

function stub(rooms: unknown[], defaultRoom = '') {
  const calls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (u: RequestInfo | URL, init?: RequestInit) => {
    const url = String(u); calls.push(`${init?.method || 'GET'} ${url}`);
    if (url.includes('/api/settings/rooms')) return new Response(JSON.stringify({ rooms, defaultPlaybackTarget: { roomId: defaultRoom, deviceId: '', serviceId: '' } }));
    if (url.endsWith('/api/rooms')) return new Response(JSON.stringify(ROOMS_VIEW));
    if (url.endsWith('/stop')) return new Response(JSON.stringify({ stopped: true }));
    if (url.includes('/api/player/play')) return new Response(JSON.stringify({ success: true }));
    return new Response(JSON.stringify({}), { status: 200 });
  }));
  return calls;
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const after = (calls: string[]) => calls.filter(c => !c.includes('/rooms'));

const plays = (calls: string[]) => calls.filter(c => c.includes('/api/player/play') || c.endsWith('/stop'));

describe('v225 room-first playback', () => {
  it('Change to another room: plays there, then (only after it started) stops the first room', async () => {
    let body: Record<string, unknown>[] = [];
    const calls = stub([LR, BR], 'lr');
    const f = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    const inner = f.getMockImplementation()!;
    f.mockImplementation(async (u: RequestInfo | URL, init?: RequestInit) => {
      if (String(u).includes('/api/player/play')) body = [...body, JSON.parse(String(init!.body))];
      return inner(u, init);
    });
    const onClose = vi.fn();
    render(<PlaybackModal ratingKey={11} title="Dune" onClose={onClose} />);
    await waitFor(() => expect(screen.getByTestId('play-in')).toBeTruthy());
    fireEvent.click(screen.getByTestId('play-in-go'));                                  // Living Room (default)
    await waitFor(() => expect(plays(calls)).toEqual(['POST /api/player/play']));
    expect(body[0]!.room_id).toBe('lr');
    fireEvent.click(await screen.findByTestId('playing-toast-change'));
    fireEvent.click(screen.getAllByTestId('play-in-room')[1]!);
    fireEvent.click(screen.getByTestId('play-in-go'));                                  // Bedroom
    await waitFor(() => expect(plays(calls)).toEqual(['POST /api/player/play', 'POST /api/player/play', 'POST /api/rooms/lr/stop']));
    expect(body[1]!.room_id).toBe('br');
    expect(onClose).not.toHaveBeenCalled();                                              // the toast keeps the popup until it ends
  });
  it('Change back to the same room stops nothing; a failed play in the new room stops nothing', async () => {
    const calls = stub([LR, BR], 'lr');
    render(<PlaybackModal ratingKey={11} title="Dune" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('play-in')).toBeTruthy());
    fireEvent.click(screen.getByTestId('play-in-go'));
    await waitFor(() => expect(plays(calls)).toHaveLength(1));
    fireEvent.click(await screen.findByTestId('playing-toast-change'));
    fireEvent.click(screen.getByTestId('play-in-go'));                                  // same room
    await waitFor(() => expect(plays(calls)).toHaveLength(2));
    await act(async () => { await new Promise(r => setTimeout(r, 50)); });
    expect(calls.some(c => c.endsWith('/stop'))).toBe(false);
    const f = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    const inner = f.getMockImplementation()!;
    f.mockImplementation(async (u: RequestInfo | URL, init?: RequestInit) =>
      String(u).includes('/api/player/play') ? new Response(JSON.stringify({ success: false, message: 'Offline' })) : inner(u, init));
    fireEvent.click(await screen.findByTestId('playing-toast-change'));
    fireEvent.click(screen.getAllByTestId('play-in-room')[1]!);
    fireEvent.click(screen.getByTestId('play-in-go'));
    await act(async () => { await new Promise(r => setTimeout(r, 80)); });
    expect(calls.some(c => c.endsWith('/stop'))).toBe(false);
  });
  it('one room: no picker, plays there at once with the Playing in toast', async () => {
    const calls = stub([LR]);
    render(<PlaybackModal ratingKey={11} title="Dune: Part Two" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('playing-toast')).toBeTruthy());
    expect(screen.getByTestId('playing-toast').textContent).toContain('Playing in Living Room');
    expect(screen.queryByTestId('play-in')).toBeNull();
    await waitFor(() => expect(after(calls).length).toBeGreaterThan(0));     // prod's flow continued (resume info), mocked
  });
  it('one usable room among rooms without a player host also plays at once', async () => {
    stub([LR, EMPTY]);
    render(<PlaybackModal ratingKey={11} title="Dune" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('playing-toast').textContent).toContain('Living Room'));
  });
  it('several rooms: Play in with the default preselected; nothing plays until a pick', async () => {
    const calls = stub([LR, BR], 'br');
    render(<PlaybackModal ratingKey={11} title="Dune" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'Play in' })).toBeTruthy());
    await waitFor(() => expect(screen.getAllByTestId('play-in-room')[1]!.textContent).toContain('Offline'));
    expect(screen.getAllByTestId('play-in-room').map(r => r.getAttribute('aria-checked'))).toEqual(['false', 'true']);
    expect(after(calls)).toEqual([]);
    fireEvent.click(screen.getAllByTestId('play-in-room')[0]!);
    fireEvent.click(screen.getByTestId('play-in-go'));
    await waitFor(() => expect(screen.getByTestId('playing-toast').textContent).toContain('Playing in Living Room'));
    await waitFor(() => expect(after(calls).length).toBeGreaterThan(0));
  });
  it('Change on the toast opens the picker again; Cancel there keeps the popup', async () => {
    const onClose = vi.fn();
    stub([LR, BR], 'br');
    render(<PlaybackModal ratingKey={11} title="Dune" onClose={onClose} />);
    await waitFor(() => expect(screen.getByTestId('play-in')).toBeTruthy());
    fireEvent.click(screen.getByTestId('play-in-go'));
    await waitFor(() => expect(screen.getByTestId('playing-toast')).toBeTruthy());
    fireEvent.click(screen.getByTestId('playing-toast-change'));
    expect(screen.getByRole('dialog', { name: 'Play in' })).toBeTruthy();
    fireEvent.click(screen.getByTestId('play-in-cancel'));
    expect(onClose).not.toHaveBeenCalled();
  });
  it('Cancel on the first picker closes playback', async () => {
    const onClose = vi.fn();
    stub([LR, BR]);
    render(<PlaybackModal ratingKey={11} title="Dune" onClose={onClose} />);
    await waitFor(() => expect(screen.getByTestId('play-in')).toBeTruthy());
    fireEvent.click(screen.getByTestId('play-in-cancel'));
    expect(onClose).toHaveBeenCalled();
  });
  it('no usable room: prod\'s own room step, no picker or toast', async () => {
    const calls = stub([EMPTY]);
    render(<PlaybackModal ratingKey={11} title="Dune" onClose={() => {}} />);
    await act(async () => { await new Promise(r => setTimeout(r, 50)); });
    expect(screen.queryByTestId('play-in')).toBeNull();
    expect(screen.queryByTestId('playing-toast')).toBeNull();
    expect(after(calls)).toEqual([]);
  });
  it('phone TV Shuffle: Shuffle in picker', async () => {
    stub([LR, BR], 'lr');
    render(<MobilePlaybackSheet ratingKey={5} title="Severance" shuffle shuffleLabel="Shuffling Severance..." onClose={() => {}} />);
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'Shuffle in' })).toBeTruthy());
    expect(screen.getByTestId('play-in-go').textContent).toBe('Shuffle');
    expect(screen.getAllByTestId('play-in-room')[0]!.getAttribute('aria-checked')).toBe('true');
  });
  it('phone movie Play with one room: toast', async () => {
    stub([BR]);
    render(<MobilePlaybackSheet ratingKey={5} title="Dune" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('playing-toast').textContent).toContain('Playing in Bedroom'));
  });
});
