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
  it('lists connectors through qBittorrent; no Web Push or Telegram card', async () => {
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-qbittorrent');
    expect(screen.queryByTestId('connector-webpush')).toBeNull();
    expect(screen.queryByTestId('connector-telegram')).toBeNull();
    const groups = [...document.querySelectorAll('[data-testid^="connector-"]')].map(e => e.getAttribute('data-testid'));
    expect(groups).toEqual([
      'connector-tmdb', 'connector-omdb', 'connector-tvdb', 'connector-trakt',
      'connector-homeassistant', 'connector-qbittorrent',
    ]);
    expect(screen.queryByText('Telegram')).toBeNull();
  });
});

describe('v280 Settings > Notifications Web Push subscribe UX', () => {
  it('shows Web Push with prod Title Case labels; unsupported → Unavailable not Enable Notifications', async () => {
    render(
      <LayoutModeProvider>
        <NotificationsSettings />
      </LayoutModeProvider>,
    );
    await screen.findByTestId('connector-webpush');
    expect(screen.getByTestId('notifications-section')).toBeTruthy();
    expect(screen.getByText('Web Push Notifications')).toBeTruthy();
    expect(screen.getByText('Enable Push Sending')).toBeTruthy();
    expect(screen.getByText('Contact Email')).toBeTruthy();
    const btn = screen.getByTestId('webpush-enable-notifications') as HTMLButtonElement;
    expect(btn).toBeTruthy();
    expect(btn.textContent).toBe('Unavailable');
    expect(btn.disabled).toBe(true);
    expect(screen.queryByText('Enable Notifications')).toBeNull();
    expect(screen.queryByText('Telegram')).toBeNull();
  });

  it('Enable Notifications is tappable when PushManager exists (not stuck unsupported)', async () => {
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
    expect(btn.textContent).toBe('Enable Notifications');
    expect(btn.disabled).toBe(false);
  });

  it('saves Web Push enable + Contact Email (VAPID; not an email notify channel)', async () => {
    render(
      <LayoutModeProvider>
        <NotificationsSettings />
      </LayoutModeProvider>,
    );
    await screen.findByTestId('connector-webpush');
    fireEvent.click(screen.getByTestId('webpush-enabled'));
    fireEvent.change(screen.getByTestId('webpush-email'), { target: { value: 'mailto:a@b.co' } });
    fireEvent.click(screen.getByTestId('webpush-save'));
    await waitFor(() => expect(webpush.enabled).toBe(true));
    expect(webpush.vapid_email).toBe('mailto:a@b.co');
    expect(calls.every(c => !c.url.includes('telegram'))).toBe(true);
  });
});
