/** v293: volume rocker — one pointerdown = one fire (no touch+mouse double). */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
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
  vi.useRealTimers();
});

function volBodies(fetchMock: ReturnType<typeof vi.fn>): string[] {
  return fetchMock.mock.calls
    .filter((c) => String(c[0]).includes('/api/room/fr/remote'))
    .map((c) => {
      const init = c[1] as RequestInit | undefined;
      return String(init?.body || '');
    });
}

describe('v293 Harmony volume half-step (pointer dedupe)', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })),
    );
  });

  it('useHoldRepeat is Pointer Events only (no dual touch+mouse)', () => {
    const src = readFileSync('src/components/rooms/FullScreenRemote.tsx', 'utf8');
    const hook = src.slice(
      src.indexOf('function useHoldRepeat'),
      src.indexOf('// Shared inline styles'),
    );
    // Hook body must bind pointer, not the iOS Safari dual path.
    expect(hook).toContain('onPointerDown');
    expect(hook).toContain('activePointerRef');
    expect(hook).not.toMatch(/onTouchStart\s*:/);
    expect(hook).not.toMatch(/onMouseDown\s*:/);
  });

  it('single pointerdown on Volume up → one vol_up POST', async () => {
    const fetchMock = vi.mocked(fetch);
    const { getByLabelText } = render(
      <FullScreenRemote room={room} deviceId="d1" onClose={() => {}} />,
    );
    const btn = getByLabelText('Volume up');
    fireEvent.pointerDown(btn, { pointerId: 1, pointerType: 'touch', button: 0 });
    fireEvent.pointerUp(btn, { pointerId: 1, pointerType: 'touch', button: 0 });
    await waitFor(() => {
      const bodies = volBodies(fetchMock);
      expect(bodies.filter((b) => b.includes('vol_up'))).toHaveLength(1);
    });
  });

  it('iOS Safari path: pointerdown then ghost mouseDown/touchStart → still one fire', async () => {
    const fetchMock = vi.mocked(fetch);
    const { getByLabelText } = render(
      <FullScreenRemote room={room} deviceId="d1" onClose={() => {}} />,
    );
    const btn = getByLabelText('Volume up');
    // Real phone: pointerdown (touch) then compatibility mousedown / touch leftovers.
    fireEvent.pointerDown(btn, { pointerId: 7, pointerType: 'touch', button: 0 });
    fireEvent.touchStart(btn, { touches: [{ identifier: 7, clientX: 0, clientY: 0 }] });
    fireEvent.mouseDown(btn, { button: 0 });
    fireEvent.pointerUp(btn, { pointerId: 7, pointerType: 'touch', button: 0 });
    fireEvent.mouseUp(btn, { button: 0 });
    await waitFor(() => {
      const bodies = volBodies(fetchMock);
      expect(bodies.filter((b) => b.includes('vol_up'))).toHaveLength(1);
    });
  });

  it('second pointerdown while held is ignored (one active pointerId)', async () => {
    const fetchMock = vi.mocked(fetch);
    const { getByLabelText } = render(
      <FullScreenRemote room={room} deviceId="d1" onClose={() => {}} />,
    );
    const btn = getByLabelText('Volume down');
    fireEvent.pointerDown(btn, { pointerId: 1, pointerType: 'touch', button: 0 });
    fireEvent.pointerDown(btn, { pointerId: 2, pointerType: 'touch', button: 0 });
    fireEvent.pointerUp(btn, { pointerId: 1, pointerType: 'touch', button: 0 });
    await waitFor(() => {
      const bodies = volBodies(fetchMock);
      expect(bodies.filter((b) => b.includes('vol_down'))).toHaveLength(1);
    });
  });

  it('hold-repeat still fires again after 400ms delay', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.mocked(fetch);
    const { getByLabelText } = render(
      <FullScreenRemote room={room} deviceId="d1" onClose={() => {}} />,
    );
    const btn = getByLabelText('Volume up');
    fireEvent.pointerDown(btn, { pointerId: 1, pointerType: 'mouse', button: 0 });
    // Immediate first fire
    expect(volBodies(fetchMock).filter((b) => b.includes('vol_up'))).toHaveLength(1);
    // Before hold delay — still one
    await vi.advanceTimersByTimeAsync(399);
    expect(volBodies(fetchMock).filter((b) => b.includes('vol_up'))).toHaveLength(1);
    // Delay elapses → interval starts and fires once immediately? setInterval waits 200ms
    // after the timeout callback only schedules the interval; first interval tick at +200.
    await vi.advanceTimersByTimeAsync(1); // 400 total — timeout fires, interval created
    expect(volBodies(fetchMock).filter((b) => b.includes('vol_up'))).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(200);
    expect(volBodies(fetchMock).filter((b) => b.includes('vol_up'))).toHaveLength(2);
    fireEvent.pointerUp(btn, { pointerId: 1, pointerType: 'mouse', button: 0 });
  });

  it('desktop mouse single click → one vol_up', async () => {
    const fetchMock = vi.mocked(fetch);
    const { getByLabelText } = render(
      <FullScreenRemote room={room} deviceId="d1" onClose={() => {}} />,
    );
    const btn = getByLabelText('Volume up');
    fireEvent.pointerDown(btn, { pointerId: 1, pointerType: 'mouse', button: 0 });
    fireEvent.pointerUp(btn, { pointerId: 1, pointerType: 'mouse', button: 0 });
    await waitFor(() => {
      expect(volBodies(fetchMock).filter((b) => b.includes('vol_up'))).toHaveLength(1);
    });
  });
});

describe('v293 Tester hard gate (document)', () => {
  it('SUMMARY / tip documents: single tap → one access-log POST / one vol IR', () => {
    // Gate text lives in tip SUMMARY; code contract is one fire per pointerdown above.
    const src = readFileSync('src/components/rooms/FullScreenRemote.tsx', 'utf8');
    expect(src).toContain('One pointerId → one start()');
  });
});
