import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MoviesSettings } from '@/components/movies/MoviesSettings';

const DATA = {
  monitoring: false, preferred_groups: [],
  trackers: [{ id: 2, name: 'PrivateHD', enabled: true }, { id: 1, name: 'BeyondHD', enabled: true }],
  qualities: [{ id: '2160p-remux', name: '2160p Remux' }, { id: '1080p-web', name: '1080p WEB' }],
  profiles: [{ id: 1, name: 'Any', items: [{ id: '2160p-remux', allowed: true }, { id: '1080p-web', allowed: true }],
    cutoff: '1080p-web', min_mb_per_min: 33.17, max_mb_per_min: null, default: true }],
};

afterEach(() => vi.unstubAllGlobals());

describe('v200 Settings > Movies', () => {
  it('shows monitoring, profiles, trackers and preferred groups, and saves the monitoring switch', async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const body = init?.method === 'PUT' ? { ...DATA, ...JSON.parse(String(init.body)) } : DATA;
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));
    render(<MoviesSettings />);
    await waitFor(() => expect(screen.getByTestId('movies-settings')).toBeTruthy());
    expect(screen.getAllByTestId('movie-profile-row')).toHaveLength(1);
    expect(screen.getByTestId('movie-profile-default').textContent).toBe('Default');
    expect(screen.getAllByTestId('movie-tracker').map(n => n.textContent)).toEqual([
      expect.stringContaining('PrivateHD'), expect.stringContaining('BeyondHD')]);
    expect(screen.getByTestId('tv-preferred-groups')).toBeTruthy();
    fireEvent.click(screen.getByRole('switch', { name: 'Monitoring' }));
    await waitFor(() => expect(calls.some(c => c.init?.method === 'PUT')).toBe(true));
    expect(JSON.parse(String(calls.find(c => c.init?.method === 'PUT')!.init!.body))).toEqual({ monitoring: true });
    fireEvent.click(screen.getByLabelText('Move BeyondHD up'));
    await waitFor(() => expect(calls.filter(c => c.init?.method === 'PUT')).toHaveLength(2));
    expect(JSON.parse(String(calls.filter(c => c.init?.method === 'PUT')[1]!.init!.body)).trackers.map((t: { id: number }) => t.id)).toEqual([1, 2]);
    expect(document.body.textContent || '').not.toMatch(/radarr|test instance/i);
  });
});
