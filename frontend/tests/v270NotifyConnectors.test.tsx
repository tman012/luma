import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConnectorsSettings } from '@/components/home/ConnectorsSettings';
import { NotificationsSettings } from '@/components/home/NotificationsSettings';
import { LayoutModeProvider } from '@/contexts/LayoutModeContext';

const res = (body: unknown, status = 200) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;

let webpush = { enabled: false, vapid_email: '' };
let calls: { url: string; method: string; body: Record<string, unknown> | null }[] = [];

function stubMatchMedia() {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: true, media: q,
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
    if (url.endsWith('/api/settings/connectors')) {
      return res({ tmdb: { set: false, last4: '' }, trakt: { set: false, last4: '' }, omdb: { set: false, last4: '' }, tvdb: { set: false, last4: '' } });
    }
    if (url.includes('/homeassistant')) return res({ url: '', access_token: { set: false, last4: '' } });
    if (url.includes('/qbittorrent')) return res({ url: '', username: '', category: '', password: { set: false } });
    if (url.endsWith('/webpush/test') && method === 'POST') return res({ ok: false, error: 'No subscribers' });
    if (url.endsWith('/webpush') && method === 'GET') return res(webpush);
    if (url.endsWith('/webpush') && method === 'PUT') {
      webpush = { enabled: !!body!.enabled, vapid_email: String(body!.vapid_email || '') };
      return res(webpush);
    }
    if (url.includes('/telegram')) return res({ error: 'Not found' }, 404);
    if (url.endsWith('/api/push/vapid-key')) return res({ publicKey: 'BAAAA' });
    return res({ error: 'Not found' }, 404);
  });
}

beforeEach(() => {
  webpush = { enabled: false, vapid_email: '' };
  calls = [];
  stubMatchMedia();
  vi.stubGlobal('fetch', server());
  // jsdom has no PushManager by default — button shows Unavailable (disabled).
  Object.defineProperty(window, 'PushManager', { value: undefined, configurable: true });
  Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('v273 Connectors without Web Push / Telegram', () => {
  it('premium pass: lists exactly the 7 real connectors; no Web Push or Telegram', async () => {
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-row-qbittorrent');
    expect(screen.queryByTestId('connector-webpush')).toBeNull();
    expect(screen.queryByTestId('connector-telegram')).toBeNull();
    const rows = [...document.querySelectorAll('[data-testid^="connector-row-"]')].map(e => e.getAttribute('data-testid'));
    expect(rows).toEqual(['plex', 'qbittorrent', 'homeassistant', 'tmdb', 'omdb', 'tvdb', 'trakt'].map(id => `connector-row-${id}`));
    expect(screen.queryByText('Telegram')).toBeNull();
    expect(screen.queryByText('Web Push')).toBeNull();
  });
});

describe('v280 Settings > Notifications Web Push subscribe UX', () => {
  it('premium pass: Push notifications toggle + This browser; no Contact Email; unsupported → Unavailable', async () => {
    render(
      <LayoutModeProvider>
        <NotificationsSettings />
      </LayoutModeProvider>,
    );
    await screen.findByTestId('connector-webpush');
    expect(screen.getByTestId('notifications-section')).toBeTruthy();
    expect(screen.getByText('Push notifications')).toBeTruthy();
    expect(screen.getByText('This browser')).toBeTruthy();
    expect(screen.queryByText('Contact Email')).toBeNull();
    expect(screen.queryByTestId('webpush-email')).toBeNull();
    expect(document.querySelector('input[type="email"]')).toBeNull();
    expect(screen.getByTestId('webpush-browser-blocked').textContent).toBe('Unavailable');
    expect(screen.queryByTestId('webpush-enable-notifications')).toBeNull();
    expect(screen.getByTestId('webpush-test').textContent).toBe('Send test');
    expect(screen.queryByText('Telegram')).toBeNull();
  });

  it('Turn on is tappable when PushManager exists (not stuck unsupported)', async () => {
    // Minimal PushManager stub — capability sync must leave state at prompt.
    Object.defineProperty(window, 'PushManager', { value: function PushManager() {}, configurable: true });
    Object.defineProperty(navigator, 'serviceWorker', {
      value: { ready: new Promise(() => {}) },
      configurable: true,
    });
    render(
      <LayoutModeProvider>
        <NotificationsSettings />
      </LayoutModeProvider>,
    );
    await screen.findByTestId('connector-webpush');
    const btn = screen.getByTestId('webpush-enable-notifications') as HTMLButtonElement;
    expect(btn.textContent).toBe('Turn on');
    expect(btn.disabled).toBe(false);
  });

  it('the Push notifications toggle saves at once and never sends vapid_email', async () => {
    render(
      <LayoutModeProvider>
        <NotificationsSettings />
      </LayoutModeProvider>,
    );
    await screen.findByTestId('connector-webpush');
    const sw = screen.getByTestId('webpush-enabled');
    expect(sw.getAttribute('aria-checked')).toBe('false');
    fireEvent.click(sw);
    await waitFor(() => expect(webpush.enabled).toBe(true));
    const put = calls.find(c => c.method === 'PUT')!;
    expect(put.body).toEqual({ enabled: true });
    expect(calls.every(c => !c.body || !('vapid_email' in (c.body as object)))).toBe(true);
    await waitFor(() => expect(screen.getByTestId('webpush-enabled').getAttribute('aria-checked')).toBe('true'));
    expect(calls.every(c => !c.url.includes('telegram'))).toBe(true);
  });
});
