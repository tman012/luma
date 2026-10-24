import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConnectorsSettings } from '@/components/home/ConnectorsSettings';

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
    return res({ error: 'Not found' }, 404);
  });
}

beforeEach(() => {
  webpush = { enabled: false, vapid_email: '' };
  calls = [];
  stubMatchMedia();
  vi.stubGlobal('fetch', server());
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('v273 Web Push Connectors (no Telegram)', () => {
  it('shows Web Push after qBittorrent with prod Title Case labels; no Telegram card', async () => {
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-webpush');
    expect(screen.queryByTestId('connector-telegram')).toBeNull();
    const groups = [...document.querySelectorAll('[data-testid^="connector-"]')].map(e => e.getAttribute('data-testid'));
    expect(groups).toEqual([
      'connector-tmdb', 'connector-omdb', 'connector-tvdb', 'connector-trakt',
      'connector-homeassistant', 'connector-qbittorrent',
      'connector-webpush',
    ]);
    expect(screen.getByText('Web Push Notifications')).toBeTruthy();
    expect(screen.getByText('Enable Push Sending')).toBeTruthy();
    expect(screen.getByText('Contact Email')).toBeTruthy();
    expect(screen.queryByText('Bot Token')).toBeNull();
    expect(screen.queryByText('Chat ID')).toBeNull();
    expect(screen.queryByText('Telegram')).toBeNull();
  });

  it('saves Web Push enable + Contact Email (VAPID; not an email notify channel)', async () => {
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-webpush');
    fireEvent.click(screen.getByTestId('webpush-enabled'));
    fireEvent.change(screen.getByTestId('webpush-email'), { target: { value: 'mailto:a@b.co' } });
    fireEvent.click(screen.getByTestId('webpush-save'));
    await waitFor(() => expect(webpush.enabled).toBe(true));
    expect(webpush.vapid_email).toBe('mailto:a@b.co');
    expect(calls.every(c => !c.url.includes('telegram'))).toBe(true);
  });
});
