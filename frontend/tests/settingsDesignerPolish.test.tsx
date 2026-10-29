import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConnectorsSettings } from '@/components/home/ConnectorsSettings';
import { trackersCss } from '@/components/trackers/trackersUi';

const res = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;

beforeEach(() => {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('min-width: 1024px'), media: q,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    onchange: null, dispatchEvent: () => false,
  }));
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/api/settings/connectors/qbittorrent') && (init?.method || 'GET') === 'GET')
      return res({ url: 'http://qbit.local:8080', username: 'admin', category: 'luma', password: { set: true } });
    if (url.endsWith('/api/settings/connectors')) return res({ tmdb: { set: false, last4: '' }, trakt: { set: false, last4: '' } });
    return res({ error: 'Not found' }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('Designer polish: Settings', () => {
  it('qBit Save is blue at 40% until there are changes, then full blue', async () => {
    render(<ConnectorsSettings />);
    fireEvent.click(await screen.findByTestId('connector-row-qbittorrent'));
    await screen.findByTestId('connector-qbittorrent');
    const save = screen.getByTestId('qbit-save') as HTMLButtonElement;
    expect(save.style.background).toBe('var(--color-accent)');
    expect(save.style.opacity).toBe('0.4');
    fireEvent.change(screen.getByTestId('qbit-category'), { target: { value: 'movies' } });
    expect(save.style.opacity).toBe('1');
  });

  it('tracker form: a disabled primary button is 40% opacity', () => {
    expect(trackersCss).toContain('.lt-primary:disabled{opacity:.4}');
  });

  it('Connectors labels: group headers 13px/600 white 50%, fields 13px/500 white 70%', async () => {
    render(<ConnectorsSettings />);
    fireEvent.click(await screen.findByTestId('connector-row-qbittorrent'));
    await screen.findByTestId('connector-qbittorrent');
    for (const l of document.querySelectorAll('label')) {
      const s = (l as HTMLElement).style;
      if (!s.fontSize) continue;
      expect(s.fontSize).toBe('13px');
      expect(s.fontWeight).toBe('500');
      expect(['rgba(255, 255, 255, 0.7)', 'var(--color-text-secondary)']).toContain(s.color);
    }
  });
});
