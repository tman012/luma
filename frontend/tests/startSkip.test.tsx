import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

let status: Record<string, unknown> = {};
const calls: Array<[string, RequestInit | undefined]> = [];
let skipReply = { ok: true, body: { ok: true } as Record<string, unknown> };
vi.mock('@/apiClient', () => ({
  apiFetch: vi.fn(async (url: string, init?: RequestInit) => {
    calls.push([url, init]);
    if (url === '/api/start/status') return { ok: true, status: 200, json: async () => status };
    if (url === '/api/start/skip') return { ok: skipReply.ok, status: skipReply.ok ? 200 : 403, json: async () => skipReply.body };
    return { ok: false, status: 404, json: async () => ({}) };
  }),
}));
const refreshUser = vi.fn(async () => {});
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ refreshUser, authenticated: false, startInProgress: false, loading: false }),
}));
import { StartPage } from '@/pages/StartPage';

const page = () => render(<MemoryRouter><StartPage /></MemoryRouter>);

describe('Get Started: Skip', () => {
  afterEach(() => { cleanup(); calls.length = 0; skipReply = { ok: true, body: { ok: true } }; });

  it('is offered only when the server allows it (same network)', async () => {
    status = { needs_start: true, in_progress: false, step: 'welcome', skip: false };
    page();
    await screen.findByRole('button', { name: /Continue with Plex/ });
    expect(screen.queryByRole('button', { name: 'Skip' })).toBeNull();
    cleanup();
    status = { ...status, skip: true };
    page();
    const skip = await screen.findByRole('button', { name: 'Skip' });
    expect(parseInt(skip.style.minHeight, 10)).toBeGreaterThanOrEqual(44);
  });

  it('Skip asks for a username and password and creates the owner without Plex', async () => {
    status = { needs_start: true, in_progress: false, step: 'welcome', skip: true };
    page();
    fireEvent.click(await screen.findByRole('button', { name: 'Skip' }));
    expect(screen.getByRole('button', { name: 'Create account' })).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'taylor' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'correct horse' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Create account' })); });
    const post = calls.find(c => c[0] === '/api/start/skip')!;
    expect(JSON.parse(String(post[1]!.body))).toEqual({ username: 'taylor', password: 'correct horse' });
    await waitFor(() => expect(refreshUser).toHaveBeenCalled());
    expect(calls.some(c => c[0] === '/api/start/plex/pin')).toBe(false);
  });

  it('Back returns to Welcome; a refusal shows the server message', async () => {
    status = { needs_start: true, in_progress: false, step: 'welcome', skip: true };
    skipReply = { ok: false, body: { error: 'Continue with Plex to set up Luma from here' } };
    page();
    fireEvent.click(await screen.findByRole('button', { name: 'Skip' }));
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'taylor' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'correct horse' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Create account' })); });
    expect(await screen.findByText('Continue with Plex to set up Luma from here')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(await screen.findByRole('button', { name: /Continue with Plex/ })).toBeTruthy();
  });

  it('after the setup window closes, Welcome says so and offers nothing', async () => {
    status = { needs_start: true, in_progress: false, step: 'welcome', skip: true, closed: true };
    page();
    expect(await screen.findByText('Setup closed. Restart Luma to try again.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Continue with Plex/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Skip' })).toBeNull();
  });
});
