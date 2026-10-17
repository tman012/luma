import { act, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider, useAuth, noteSignedInUser, LAST_USER_KEY } from '@/contexts/AuthContext';

const posted: unknown[] = [];
const deleted: string[] = [];
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });

beforeEach(() => {
  posted.length = 0; deleted.length = 0; localStorage.clear();
  Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: { controller: { postMessage: (m: unknown) => posted.push(m) } } });
  vi.stubGlobal('caches', { keys: async () => ['media-kiosk-v126', 'media-kiosk-immutable'], delete: async (n: string) => { deleted.push(n); return true; } });
});
afterEach(() => { vi.unstubAllGlobals(); });

describe('shared device: caches are cleared on sign-out and account switch', () => {
  it('sign-out tells the service worker to clear all caches and clears Cache Storage', async () => {
    vi.stubGlobal('fetch', vi.fn(async (u: string) => (String(u).includes('/api/auth/me')
      ? json({ authenticated: true, user: { id: 1, name: 'a' } }) : json({ success: true }))));
    let api: ReturnType<typeof useAuth> | null = null;
    const Grab = () => { api = useAuth(); return null; };
    render(<AuthProvider><Grab /></AuthProvider>);
    await waitFor(() => expect(api!.authenticated).toBe(true));
    posted.length = 0; deleted.length = 0;
    await act(async () => { await api!.logout(); });
    expect(posted).toContain('CLEAR_ALL_CACHES');
    await waitFor(() => expect(deleted).toEqual(['media-kiosk-v126', 'media-kiosk-immutable']));
  });

  it('a different account signing in clears what the previous one left; the same account does not', async () => {
    noteSignedInUser(1);
    expect(posted).toEqual([]);           // first account on this device
    expect(localStorage.getItem(LAST_USER_KEY)).toBe('1');
    noteSignedInUser(1);
    expect(posted).toEqual([]);           // same account again: keep its caches (speed)
    noteSignedInUser(2);
    expect(posted).toEqual(['CLEAR_ALL_CACHES']);
    expect(localStorage.getItem(LAST_USER_KEY)).toBe('2');
  });
});
