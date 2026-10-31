/** Rooms sheet bounded wait: if rooms or devices never resolve, the sheet reveals what it has at ROOMS_WAIT_MS. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ implicit: false, authenticated: true }) }));
import { HomePills } from '@/components/home/HomePills';
import { ROOMS_WAIT_MS, _clearRoomsCache } from '@/components/rooms/RoomsSheet';

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); _clearRoomsCache(); });

function openRooms(hang: (url: string) => boolean) {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn((url: string) => hang(String(url))
    ? new Promise<Response>(() => {})
    : Promise.resolve(new Response(JSON.stringify({ rooms: [], unmatched: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))));
  render(<MemoryRouter><HomePills /></MemoryRouter>);
  fireEvent.click(screen.getByTestId('pill-rooms'));
}

describe('Rooms sheet bounded wait', () => {
  it.each([
    ['rooms (sessions) hang', (u: string) => u.includes('/api/plex/sessions/active')],
    ['devices hang', (u: string) => u.includes('/api/settings/rooms')],
    ['both hang', (u: string) => u.includes('/api/plex/sessions/active') || u.includes('/api/settings/rooms')],
  ])('%s: blank until %s, then the same reveal with the empty state', async (_n, hang) => {
    openRooms(hang);
    await act(async () => { await vi.advanceTimersByTimeAsync(ROOMS_WAIT_MS - 100); });
    const box = screen.getByTestId('rooms-reveal');
    expect(box.dataset.reveal).toBe('hidden');
    expect(box.textContent).toBe('');
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    expect(box.dataset.reveal).not.toBe('hidden');
    expect(box.textContent).toContain('No rooms configured');
    expect(document.querySelector('.animate-pulse')).toBeNull();
  });

  it('nothing hangs: reveals before the bound', async () => {
    openRooms(() => false);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByTestId('rooms-reveal').dataset.reveal).not.toBe('hidden');
  });

  it('the bound is 4s', () => { expect(ROOMS_WAIT_MS).toBe(4000); });
});
