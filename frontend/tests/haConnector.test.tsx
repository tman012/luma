import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConnectorsSettings } from '@/components/home/ConnectorsSettings';

const TOKEN = 'ha-test-token-abcdefghij';
const res = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;
type View = { url: string; access_token: { set: boolean; last4: string } };
let ha: View;
let calls: { url: string; method: string; body: Record<string, unknown> | null }[] = [];
let testAnswer: unknown = { ok: true, message: 'Connected' };
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
    if (url.endsWith('/api/settings/connectors/homeassistant/test')) return res(testAnswer);
    if (url.endsWith('/api/settings/connectors/homeassistant') && method === 'GET') return res(ha);
    if (url.endsWith('/api/settings/connectors/homeassistant') && method === 'PUT') {
      if (saveAnswer) return saveAnswer(body!);
      if (!body!.url) {
        ha = { url: '', access_token: { set: false, last4: '' } };
        return res(ha);
      }
      const tok = typeof body!.access_token === 'string' ? String(body!.access_token) : '';
      ha = {
        url: String(body!.url),
        access_token: {
          set: ha.access_token.set || !!tok,
          last4: tok ? tok.slice(-4) : ha.access_token.last4,
        },
      };
      return res(ha);
    }
    if (url.endsWith('/api/settings/connectors/qbittorrent')) {
      return res({ url: '', username: '', category: '', password: { set: false } });
    }
    if (url.endsWith('/api/settings/connectors')) {
      return res({ tmdb: { set: false, last4: '' }, trakt: { set: false, last4: '' }, omdb: { set: false, last4: '' }, tvdb: { set: false, last4: '' } });
    }
    if (url.includes('/api/settings/connectors/webpush')) {
      return res({ enabled: false, vapid_email: '' });
    }
    if (url.includes('/api/settings/connectors/telegram')) {
      return res({ bot_token: { set: false, last4: '' }, chat_id: '' });
    }
    return res({ error: 'Not found' }, 404);
  });
}

const field = (id: string) => screen.getByTestId(`ha-${id}`) as HTMLInputElement;
const type = (id: string, v: string) => fireEvent.change(field(id), { target: { value: v } });

beforeEach(() => {
  ha = { url: 'http://ha.example:8123', access_token: { set: true, last4: TOKEN.slice(-4) } };
  calls = []; testAnswer = { ok: true, message: 'Connected' }; saveAnswer = null; wide = true;
  stubMatchMedia();
  vi.stubGlobal('fetch', server());
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('Settings > Connectors > Home Assistant', () => {
  it('sits after Trakt and before qBittorrent; token shows only as dots', async () => {
    render(<ConnectorsSettings />);
    const card = await screen.findByTestId('connector-homeassistant');
    const groups = [...document.querySelectorAll('[data-testid^="connector-"]')].map(e => e.getAttribute('data-testid'));
    expect(groups).toEqual([
      'connector-tmdb', 'connector-omdb', 'connector-tvdb', 'connector-trakt',
      'connector-homeassistant', 'connector-qbittorrent',
      'connector-webpush', 'connector-telegram',
    ]);
    expect(field('url').value).toBe('http://ha.example:8123');
    expect(field('access-token').value).toBe('');
    expect(field('access-token').type).toBe('password');
    expect(field('access-token').placeholder).toBe(`••••${TOKEN.slice(-4)}`);
    expect((screen.getByTestId('ha-save') as HTMLButtonElement).disabled).toBe(true);
    expect(card.textContent).toBe('Home AssistantURLAccess TokenShowTestSaveRemove');
  });

  it('a fresh install starts empty with the prod URL placeholder', async () => {
    ha = { url: '', access_token: { set: false, last4: '' } };
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-homeassistant');
    expect(field('url').value).toBe('');
    expect(field('url').placeholder).toBe('http://homeassistant.local:8123');
    expect(field('access-token').value).toBe('');
    expect(field('access-token').placeholder).toBe('');
  });

  it('Test sends the form and shows Connected', async () => {
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-homeassistant');
    type('access-token', TOKEN);
    fireEvent.click(screen.getByTestId('ha-test'));
    await waitFor(() => expect(screen.getByTestId('ha-result').textContent).toBe('Connected'));
    const t = [...calls].reverse().find(c => c.url.endsWith('/homeassistant/test'));
    expect(t?.body).toEqual({ url: 'http://ha.example:8123', access_token: TOKEN });
  });

  it('Save writes URL + token; Remove clears both', async () => {
    ha = { url: '', access_token: { set: false, last4: '' } };
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-homeassistant');
    type('url', 'http://ha.example:8123');
    type('access-token', TOKEN);
    fireEvent.click(screen.getByTestId('ha-save'));
    await waitFor(() => expect(ha.url).toBe('http://ha.example:8123'));
    expect(ha.access_token).toEqual({ set: true, last4: TOKEN.slice(-4) });
    fireEvent.click(screen.getByTestId('ha-remove'));
    fireEvent.click(screen.getByTestId('ha-remove-confirm'));
    await waitFor(() => expect(ha.url).toBe(''));
    expect(ha.access_token.set).toBe(false);
  });

  it('never puts the token in any test assertion failure path text', async () => {
    testAnswer = { ok: false, error: 'Invalid token' };
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-homeassistant');
    type('access-token', TOKEN);
    fireEvent.click(screen.getByTestId('ha-test'));
    await waitFor(() => expect(screen.getByTestId('ha-result').textContent).toBe('Invalid token'));
    expect(document.body.textContent).not.toContain(TOKEN);
  });
});
