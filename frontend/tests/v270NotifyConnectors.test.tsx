import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConnectorsSettings } from '@/components/home/ConnectorsSettings';

const res = (body: unknown, status = 200) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;

let webpush = { enabled: false, vapid_email: '' };
let telegram = { bot_token: { set: false, last4: '' }, chat_id: '' };
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
    if (url.endsWith('/telegram/test') && method === 'POST') return res({ ok: true, message: 'Connected' });
    if (url.endsWith('/telegram') && method === 'GET') return res(telegram);
    if (url.endsWith('/telegram') && method === 'PUT') {
      const tok = String(body!.bot_token || '');
      if (!tok) {
        telegram = { bot_token: { set: false, last4: '' }, chat_id: '' };
        return res(telegram);
      }
      telegram = {
        bot_token: { set: true, last4: tok.slice(-4) },
        chat_id: String(body!.chat_id || ''),
      };
      return res(telegram);
    }
    return res({ error: 'Not found' }, 404);
  });
}

beforeEach(() => {
  webpush = { enabled: false, vapid_email: '' };
  telegram = { bot_token: { set: false, last4: '' }, chat_id: '' };
  calls = [];
  stubMatchMedia();
  vi.stubGlobal('fetch', server());
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('v270 Notify Connectors', () => {
  it('shows Web Push and Telegram after qBittorrent with prod Title Case labels', async () => {
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-webpush');
    await screen.findByTestId('connector-telegram');
    const groups = [...document.querySelectorAll('[data-testid^="connector-"]')].map(e => e.getAttribute('data-testid'));
    expect(groups).toEqual([
      'connector-tmdb', 'connector-omdb', 'connector-tvdb', 'connector-trakt',
      'connector-homeassistant', 'connector-qbittorrent',
      'connector-webpush', 'connector-telegram',
    ]);
    expect(screen.getByText('Web Push Notifications')).toBeTruthy();
    expect(screen.getByText('Enable Push Sending')).toBeTruthy();
    expect(screen.getByText('Contact Email')).toBeTruthy();
    expect(screen.getByText('Bot Token')).toBeTruthy();
    expect(screen.getByText('Chat ID')).toBeTruthy();
  });

  it('saves Web Push enable + email', async () => {
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-webpush');
    fireEvent.click(screen.getByTestId('webpush-enabled'));
    fireEvent.change(screen.getByTestId('webpush-email'), { target: { value: 'mailto:a@b.co' } });
    fireEvent.click(screen.getByTestId('webpush-save'));
    await waitFor(() => expect(webpush.enabled).toBe(true));
    expect(webpush.vapid_email).toBe('mailto:a@b.co');
  });

  it('Telegram token field is password and never shows a typed value after save', async () => {
    render(<ConnectorsSettings />);
    const input = await screen.findByTestId('telegram-bot-token') as HTMLInputElement;
    expect(input.type).toBe('password');
    fireEvent.change(input, { target: { value: '123456789:AAHsecret' } });
    fireEvent.change(screen.getByTestId('telegram-chat-id'), { target: { value: '42' } });
    fireEvent.click(screen.getByTestId('telegram-save'));
    await waitFor(() => expect(telegram.bot_token.set).toBe(true));
    expect((screen.getByTestId('telegram-bot-token') as HTMLInputElement).value).toBe('');
    expect((screen.getByTestId('telegram-bot-token') as HTMLInputElement).placeholder).toBe('••••cret');
  });
});
