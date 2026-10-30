/** Connectors copy (Taylor 11:00 PM): connected rows say Connected (green dot kept); disconnected or
 * errored rows keep Not set up; headers are Media Server / Download Client / Smart Home / Metadata. */
import { cleanup, render, screen, waitFor, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConnectorsSettings } from '@/components/home/ConnectorsSettings';

const res = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;
const stub = (over: Record<string, () => Response>) => vi.stubGlobal('fetch', vi.fn(async (input: string) => {
  const url = String(input);
  for (const k of Object.keys(over)) if (url.endsWith(k)) return over[k]();
  return res({ error: 'Not found' }, 404);
}));

beforeEach(() => {
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('min-width: 1024px'), media: q,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const connectors = () => res({ tmdb: { set: true, last4: 'abcd' }, omdb: { set: false, last4: '' }, tvdb: { set: false, last4: '' }, trakt: { set: false, last4: '' } });

describe('Connectors copy v319', () => {
  it('headers renamed; Media Server Title Case, Metadata unchanged', async () => {
    stub({ '/api/settings/connectors': connectors });
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-row-plex');
    expect(screen.getAllByTestId('connector-group').map(g => g.querySelector('h2')!.textContent))
      .toEqual(['Media Server', 'Download Client', 'Smart Home', 'Metadata']);
    expect(screen.queryByText('Downloads')).toBeNull();
  });

  it('connected says Connected with the green dot; disconnected and errored never say Connected', async () => {
    stub({
      '/api/settings/plex': () => res({ connected: false, server: '' }), // disconnected
      '/api/settings/connectors/qbittorrent': () => res({ url: 'http://192.168.1.72:8080', password: { set: true } }), // connected
      '/api/settings/connectors/homeassistant': () => res({ error: 'boom' }, 500), // errored
      '/api/settings/connectors': connectors,
    });
    render(<ConnectorsSettings />);
    await waitFor(() => expect(screen.getByTestId('connector-status-qbittorrent').textContent).toBe('Connected'));
    const dot = screen.getByTestId('connector-status-qbittorrent').querySelector('span')!.getAttribute('style') || '';
    expect(dot).toMatch(/22c55e|34, 197, 94|#4ade80|green/i);
    expect(screen.getByTestId('connector-status-tmdb').textContent).toBe('Connected');
    expect(screen.getByTestId('connector-status-plex').textContent).toBe('Not set up');
    expect(screen.getByTestId('connector-status-homeassistant').textContent).toBe('Not set up');
    expect(screen.getByTestId('connector-status-omdb').textContent).toBe('Not set up');
    expect(document.body.textContent).not.toContain('Set up');
    fireEvent.click(screen.getByTestId('connector-row-qbittorrent'));
    await waitFor(() => expect(screen.getByTestId('connector-modal-status').textContent).toBe('Connected'));
  });
});
