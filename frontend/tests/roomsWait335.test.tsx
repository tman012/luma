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

import { RoomsSheet } from '@/components/rooms/RoomsSheet';
import { RoomsSessionsProvider } from '@/contexts/NotificationsContext';

describe('Rooms sheet reopen', () => {
  const Sheet = ({ open }: { open: boolean }) => (
    <MemoryRouter><RoomsSessionsProvider active={open}><RoomsSheet open={open} onClose={() => {}} /></RoomsSessionsProvider></MemoryRouter>
  );
  it('reopened mid-load: no stale content, a fresh bounded reveal with its fade', async () => {
    vi.useFakeTimers();
    const fades: unknown[] = [];
    (Element.prototype as unknown as { animate: unknown }).animate = function (...a: unknown[]) { fades.push(a); return { onfinish: null, cancel() {} } as unknown as Animation; };
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})));        // everything hangs
    const r = render(<Sheet open />);
    await act(async () => { await vi.advanceTimersByTimeAsync(ROOMS_WAIT_MS + 100); }); // first open waits out
    expect(screen.getByTestId('rooms-reveal').textContent).toContain('No rooms configured');
    r.rerender(<Sheet open={false} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    const before = fades.length;
    r.rerender(<Sheet open />);                                                     // reopen, still loading
    const box = screen.getByTestId('rooms-reveal');
    expect(box.dataset.reveal).toBe('hidden');                                     // frame 0: not the old content
    expect(box.textContent).toBe('');
    await act(async () => { await vi.advanceTimersByTimeAsync(ROOMS_WAIT_MS - 200); });
    expect(screen.getByTestId('rooms-reveal').dataset.reveal).toBe('hidden');
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    expect(screen.getByTestId('rooms-reveal').dataset.reveal).not.toBe('hidden');
    expect(fades.length).toBe(before + 1);                                         // the reopen gets its own fade
  });

  it('reopened after data loaded: cached content on frame 0, no fade', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ rooms: [], unmatched: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
    const r = render(<Sheet open />);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    r.rerender(<Sheet open={false} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    r.rerender(<Sheet open />);
    const box = screen.getByTestId('rooms-reveal');
    expect(box.dataset.reveal).toBe('shown');
    expect(box.dataset.revealCached).toBe('true');
  });
});
