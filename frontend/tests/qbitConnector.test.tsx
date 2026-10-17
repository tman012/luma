import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConnectorsSettings } from '@/components/home/ConnectorsSettings';

const PW = 'pw-for-tests-only';
const res = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;
type View = { url: string; username: string; category: string; password: { set: boolean } };
let qbit: View;
let calls: { url: string; method: string; body: Record<string, unknown> | null }[] = [];
let testAnswer: unknown = { ok: true, message: 'Connected', version: 'v4.6.5' };
let saveAnswer: ((b: Record<string, unknown>) => Response) | null = null;
let wide = true;

function stubMatchMedia() {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('min-width: 1024px') ? wide : false, media: q,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    onchange: null, dispatchEvent: () => false,
  }));
}

function server() {
  return vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method || 'GET').toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null;
    calls.push({ url, method, body });
    if (url.endsWith('/api/settings/connectors/qbittorrent/test')) return res(testAnswer);
    if (url.endsWith('/api/settings/connectors/qbittorrent') && method === 'GET') return res(qbit);
    if (url.endsWith('/api/settings/connectors/qbittorrent') && method === 'PUT') {
      if (saveAnswer) return saveAnswer(body!);
      qbit = { url: String(body!.url), username: String(body!.username), category: String(body!.category),
        password: { set: qbit.password.set || typeof body!.password === 'string' } };
      return res(qbit);
    }
    if (url.endsWith('/api/settings/connectors')) return res({ tmdb: { set: false, last4: '' }, trakt: { set: false, last4: '' } });
    return res({ error: 'Not found' }, 404);
  });
}

const field = (id: string) => screen.getByTestId(`qbit-${id}`) as HTMLInputElement;
const type = (id: string, v: string) => fireEvent.change(field(id), { target: { value: v } });
const last = (pred: (c: (typeof calls)[number]) => boolean) => [...calls].reverse().find(pred);

beforeEach(() => {
  qbit = { url: 'http://qbit.local:8080', username: 'admin', category: 'luma-test', password: { set: true } };
  calls = []; testAnswer = { ok: true, message: 'Connected', version: 'v4.6.5' }; saveAnswer = null; wide = true;
  stubMatchMedia();
  vi.stubGlobal('fetch', server());
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('Settings > Connectors > qBittorrent', () => {
  it('sits after TMDB and Trakt, pre-filled from the server; the password shows only as dots', async () => {
    render(<ConnectorsSettings />);
    const card = await screen.findByTestId('connector-qbittorrent');
    const groups = [...document.querySelectorAll('[data-testid^="connector-"]')].map(e => e.getAttribute('data-testid'));
    expect(groups).toEqual(['connector-tmdb', 'connector-trakt', 'connector-qbittorrent']);
    expect(field('url').value).toBe('http://qbit.local:8080');
    expect(field('username').value).toBe('admin');
    expect(field('category').value).toBe('luma-test');
    expect(field('password').value).toBe('');
    expect(field('password').type).toBe('password');
    expect(field('password').placeholder).toBe('••••••••');
    expect((screen.getByTestId('qbit-save') as HTMLButtonElement).disabled).toBe(true);
    // Labels and buttons only: no helper or explanatory text.
    expect(card.textContent).toBe('qBittorrentURLUsernamePasswordShowCategoryTestSave');
  });

  it('a fresh install starts empty', async () => {
    qbit = { url: '', username: '', category: '', password: { set: false } };
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-qbittorrent');
    for (const id of ['url', 'username', 'password', 'category']) expect(field(id).value).toBe('');
    expect(field('password').placeholder).toBe('');
  });

  it('Test sends the form without the stored password and shows Connected with the version', async () => {
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-qbittorrent');
    fireEvent.click(screen.getByTestId('qbit-test'));
    await waitFor(() => expect(screen.getByTestId('qbit-result').textContent).toBe('Connected (v4.6.5)'));
    expect(screen.getByTestId('qbit-result').style.color).toBe('rgb(34, 197, 94)');
    expect(last(c => c.url.endsWith('/test'))!.body).toEqual({ url: 'http://qbit.local:8080', username: 'admin', category: 'luma-test' });
  });

  it('a failed Test shows the specific error until the next edit', async () => {
    testAnswer = { ok: false, error: 'Wrong username or password' };
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-qbittorrent');
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(screen.getByTestId('qbit-test'));
    await waitFor(() => expect(screen.getByTestId('qbit-result').textContent).toBe('Wrong username or password'));
    expect(screen.getByTestId('qbit-result').style.color).toBe('rgb(248, 113, 113)');
    await act(async () => { vi.advanceTimersByTime(10000); });
    expect(screen.getByTestId('qbit-result').textContent).toBe('Wrong username or password');
    type('password', 'x');
    expect(screen.getByTestId('qbit-result').textContent).toBe('');
  });

  it('Save writes URL, username, category and a typed password, then shows Saved', async () => {
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-qbittorrent');
    type('category', 'movies');
    type('password', PW);
    expect((screen.getByTestId('qbit-save') as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByTestId('qbit-save'));
    await screen.findByText('Saved');
    expect(last(c => c.method === 'PUT')!.body).toEqual({ url: 'http://qbit.local:8080', username: 'admin', category: 'movies', password: PW });
    expect(field('password').value).toBe('');
    expect(field('category').value).toBe('movies');
  });

  it('Save without retyping the password leaves it out of the body', async () => {
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-qbittorrent');
    type('username', 'taylor');
    fireEvent.click(screen.getByTestId('qbit-save'));
    await screen.findByText('Saved');
    expect(last(c => c.method === 'PUT')!.body).toEqual({ url: 'http://qbit.local:8080', username: 'taylor', category: 'luma-test' });
  });

  it('a new URL drops the dots and shows the server field error', async () => {
    saveAnswer = () => res({ error: 'Invalid', fields: { password: 'Required' } }, 400);
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-qbittorrent');
    type('url', 'http://other.local:8080');
    expect(field('password').placeholder).toBe('');
    fireEvent.click(screen.getByTestId('qbit-save'));
    expect((await screen.findByTestId('qbit-password-error')).textContent).toBe('Required');
    expect(field('password').getAttribute('aria-invalid')).toBe('true');
    type('password', PW);
    expect(screen.queryByTestId('qbit-password-error')).toBeNull();
  });

  it('Show reveals only what was typed', async () => {
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-qbittorrent');
    fireEvent.click(screen.getByRole('button', { name: 'Show password' }));
    expect(field('password').type).toBe('text');
    expect(field('password').value).toBe('');
  });

  it('phone: 44px inputs and buttons, 16px text', async () => {
    wide = false; stubMatchMedia();
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-qbittorrent');
    for (const id of ['url', 'username', 'password', 'category']) {
      expect(field(id).style.height).toBe('44px');
      expect(field(id).style.fontSize).toBe('16px');
    }
    expect(screen.getByTestId('qbit-save').style.minHeight).toBe('44px');
    expect(screen.getByTestId('qbit-test').style.minHeight).toBe('44px');
    expect(screen.getByTestId('qbit-save').style.background).toBe('rgb(59, 130, 246)');
  });
});
