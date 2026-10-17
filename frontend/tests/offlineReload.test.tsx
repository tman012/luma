// Tester ab1c841 #4: reloading offline while signed in shows an offline notice, not the Sign in form.
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider, LAST_USER_KEY, useAuth } from '@/contexts/AuthContext';

function Probe() {
  const { authenticated, loading } = useAuth();
  return <p data-testid="probe">{loading ? 'loading' : authenticated ? 'signed in' : 'sign in form'}</p>;
}
const me = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
const renderApp = () => render(<AuthProvider><Probe /></AuthProvider>);

beforeEach(() => { localStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

describe('offline reload', () => {
  it('signed in before, network down: the offline notice, not the Sign in form', async () => {
    localStorage.setItem(LAST_USER_KEY, '1');
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    renderApp();
    expect((await screen.findByTestId('offline-notice')).textContent).toBe("Can't reach Luma");
    expect(screen.queryByTestId('probe')).toBeNull();
    expect(screen.getByTestId('offline-retry').textContent).toBe('Try again');
  });

  it('back online (the online event or Try again) restores the session', async () => {
    localStorage.setItem(LAST_USER_KEY, '1');
    let up = false;
    vi.stubGlobal('fetch', vi.fn(async () => {
      if (!up) throw new TypeError('Failed to fetch');
      return me({ authenticated: true, user: { id: 1, name: 'o', is_admin: true } });
    }));
    renderApp();
    await screen.findByTestId('offline-notice');
    fireEvent.click(screen.getByTestId('offline-retry'));
    await waitFor(() => expect(screen.getByTestId('offline-notice')).toBeTruthy());
    up = true;
    act(() => { window.dispatchEvent(new Event('online')); });
    expect((await screen.findByTestId('probe')).textContent).toBe('signed in');
    expect(screen.queryByTestId('offline-notice')).toBeNull();
  });

  it('the browser reports offline: the notice even without a remembered user', async () => {
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    renderApp();
    await screen.findByTestId('offline-notice');
    vi.restoreAllMocks();
  });

  it('never signed in on this device and online: the Sign in form as before', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    renderApp();
    expect((await screen.findByTestId('probe')).textContent).toBe('sign in form');
  });

  it('a real signed-out answer from Luma is the Sign in form, never the notice', async () => {
    localStorage.setItem(LAST_USER_KEY, '1');
    vi.stubGlobal('fetch', vi.fn(async () => me({ authenticated: false })));
    renderApp();
    expect((await screen.findByTestId('probe')).textContent).toBe('sign in form');
    expect(screen.queryByTestId('offline-notice')).toBeNull();
  });
});
