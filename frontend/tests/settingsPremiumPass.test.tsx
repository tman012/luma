/** Settings premium pass (Designer round 2): Connectors lists only the 7 real connectors with
 * same-origin vendor logos and Connected / Not set up; Plex lives here (no Plex section). */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync, existsSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConnectorsSettings, CONNECTORS } from '@/components/home/ConnectorsSettings';
import { SETTINGS_SECTIONS } from '@/pages/LibrariesPage';

const res = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;

beforeEach(() => {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('min-width: 1024px'), media: q,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    onchange: null, dispatchEvent: () => false,
  }));
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = String(input);
    if (url.endsWith('/api/settings/plex/pin')) return res({ status: 'none' });
    if (url.endsWith('/api/settings/plex')) return res({ connected: true, server: 'Bosley Plex' });
    if (url.endsWith('/api/settings/connectors/qbittorrent')) return res({ url: 'http://192.168.1.72:8080', username: 'a', category: 'luma-test', password: { set: true } });
    if (url.endsWith('/api/settings/connectors/homeassistant')) return res({ url: 'http://192.168.1.20:8123', access_token: { set: false, last4: '' } });
    if (url.endsWith('/api/settings/connectors')) return res({ tmdb: { set: true, last4: 'abcd' }, omdb: { set: true, last4: 'om99' }, tvdb: { set: false, last4: '' }, trakt: { set: false, last4: '' } });
    return res({ error: 'Not found' }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('Settings premium pass: Connectors', () => {
  it('no Plex section in Settings; Connectors is an owner section', () => {
    expect(SETTINGS_SECTIONS.map(s => s.slug)).not.toContain('plex');
    expect(SETTINGS_SECTIONS.map(s => s.slug)).toContain('connectors');
  });

  it('exactly the real connectors, grouped, each with a logo tile and a status', async () => {
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-row-plex');
    const groups = screen.getAllByTestId('connector-group').map(g => ({
      title: g.querySelector('h2')!.textContent,
      rows: [...g.querySelectorAll('[data-testid^="connector-row-"]')].map(r => r.getAttribute('data-testid')!.slice(14)),
    }));
    expect(screen.queryByText('Set up')).toBeNull();   // v319 Taylor: connected rows say Connected
    expect(groups).toEqual([
      { title: 'Media Server', rows: ['plex'] },
      { title: 'Download Client', rows: ['qbittorrent'] },
      { title: 'Smart Home', rows: ['homeassistant'] },
      { title: 'Metadata', rows: ['tmdb', 'omdb', 'tvdb', 'trakt'] },
    ]);
    for (const id of ['sonarr', 'radarr', 'prowlarr', 'harmony', 'webpush', 'telegram']) expect(screen.queryByTestId(`connector-row-${id}`)).toBeNull();
    await waitFor(() => expect(screen.getByTestId('connector-status-plex').textContent).toBe('Connected'));
    expect(within(screen.getByTestId('connector-row-plex')).getByText('Bosley Plex')).toBeTruthy();
    expect(screen.getByTestId('connector-status-qbittorrent').textContent).toBe('Connected');
    expect(within(screen.getByTestId('connector-row-qbittorrent')).getByText('192.168.1.72')).toBeTruthy();
    expect(screen.getByTestId('connector-status-homeassistant').textContent).toBe('Not set up');   // no token saved
    expect(['tmdb', 'omdb', 'tvdb', 'trakt'].map(id => screen.getByTestId(`connector-status-${id}`).textContent))
      .toEqual(['Connected', 'Connected', 'Not set up', 'Not set up']);
  });

  it('logos are same-origin static assets that exist (no remote loads); OMDb is a wordmark tile', async () => {
    render(<ConnectorsSettings />);
    await screen.findByTestId('connector-row-plex');
    for (const c of CONNECTORS) {
      const tile = screen.getByTestId(`connector-logo-${c.id}`);
      const img = tile.querySelector('img');
      if (c.id === 'omdb') { expect(img).toBeNull(); expect(tile.textContent).toBe('OMDb'); continue; }
      const src = img!.getAttribute('src')!;
      expect(src).toMatch(/^\/static\/images\/connectors\/[a-z]+\.svg$/);
      expect(existsSync(`../${src.slice(1)}`)).toBe(true);
      expect(readFileSync(`../${src.slice(1)}`, 'utf8')).not.toMatch(/https?:\/\/(?!www\.w3\.org)/);   // no remote refs inside the SVG
    }
  });

  it('a row opens its 560px modal with the status chip and the form; Done closes it', async () => {
    render(<ConnectorsSettings />);
    fireEvent.click(await screen.findByTestId('connector-row-plex'));
    const modal = await screen.findByTestId('connector-modal-plex');
    expect(modal.style.width).toBe('560px');
    await waitFor(() => expect(screen.getByTestId('connector-modal-status').textContent).toBe('Connected'));
    expect(await within(modal).findByTestId('plex-server-name')).toBeTruthy();
    fireEvent.click(screen.getByTestId('connector-modal-done'));
    expect(screen.queryByTestId('connector-modal-plex')).toBeNull();
  });

  it('opens straight to Plex when asked (the /settings/plex return path)', async () => {
    const closed = vi.fn();
    render(<ConnectorsSettings initial="plex" onInitialClosed={closed} />);
    expect(await screen.findByTestId('connector-modal-plex')).toBeTruthy();
    fireEvent.click(screen.getByTestId('connector-modal-close'));
    expect(closed).toHaveBeenCalledTimes(1);
  });
});

describe('Settings premium pass: no Contact Email anywhere in Settings', () => {
  it('the old full-app Services tab has no Contact email field and does not save vapid_email', () => {
    const src = readFileSync('src/pages/settings/ServicesTab.tsx', 'utf8');
    expect(src).not.toMatch(/Contact email/i);
    expect(src).not.toContain('vapid_email');
  });
});
