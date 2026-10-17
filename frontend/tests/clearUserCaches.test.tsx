import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';
import * as prefetch from '@/services/modalPrefetch';

let me: { id: number; name: string } | null;
let post: ReturnType<typeof vi.fn>;
let api: ReturnType<typeof useAuth> | null = null;

function Probe() { api = useAuth(); return null; }

const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });

describe('sign-out and account switch clear the caches', () => {
  beforeEach(() => {
    me = { id: 1, name: 'a' };
    post = vi.fn();
    Object.defineProperty(navigator, 'serviceWorker', { value: { controller: { postMessage: post } }, configurable: true });
    vi.stubGlobal('fetch', vi.fn(async (u: string) => {
      const url = String(u);
      if (url.includes('/api/auth/me')) return me ? json({ authenticated: true, user: { ...me, is_admin: false } }) : json({ authenticated: false }, 401);
      if (url.includes('/api/auth/login')) return json({ success: true, user: { id: 2, name: 'b', is_admin: false } });
      return json({ success: true });
    }));
  });
  afterEach(() => { sessionStorage.clear(); cleanup(); vi.unstubAllGlobals(); Object.defineProperty(navigator, 'serviceWorker', { value: undefined, configurable: true }); });

  async function mount() {
    render(<AuthProvider><Probe /></AuthProvider>);
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }

  it('sign-out sends CLEAR_ALL_CACHES and resets the in-memory prefetch', async () => {
    const reset = vi.spyOn(prefetch, '_resetModalPrefetch');
    await mount();
    expect(post).not.toHaveBeenCalled();
    await act(async () => { await api!.logout(); });
    expect(post).toHaveBeenCalledWith('CLEAR_ALL_CACHES');
    expect(reset).toHaveBeenCalled();
  });

  it('signing in as another account clears; the same account does not', async () => {
    await mount();
    await act(async () => { await api!.refreshUser(); });
    expect(post).not.toHaveBeenCalled();
    me = { id: 2, name: 'b' };
    await act(async () => { await api!.login('b', '1234'); });
    expect(post).toHaveBeenCalledWith('CLEAR_ALL_CACHES');
  });

  it('an account switch after a reload still clears (last account kept in sessionStorage)', async () => {
    sessionStorage.setItem('luma:cachesFor', '1');
    me = { id: 2, name: 'b' };
    await mount();
    expect(post).toHaveBeenCalledWith('CLEAR_ALL_CACHES');
    expect(sessionStorage.getItem('luma:cachesFor')).toBe('2');
  });

  it('a reload as the same account does not clear; sign-out forgets the account', async () => {
    sessionStorage.setItem('luma:cachesFor', '1');
    await mount();
    expect(post).not.toHaveBeenCalled();
    await act(async () => { await api!.logout(); });
    expect(sessionStorage.getItem('luma:cachesFor')).toBeNull();
  });
});
