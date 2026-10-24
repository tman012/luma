import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

const auth = { implicit: false };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));

import { HomePills, HOME_PILLS } from '@/components/home/HomePills';
import { HomeRails } from '@/components/home/Rails';
import type { Card, Rail } from '@/components/home/homeApi';

const GIB = 1024 ** 3;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); auth.implicit = false; });

function stub(over: Record<string, unknown> = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (String(url).includes('/api/storage/downloads/cleanup')) return json({ error: 'Sign in', sign_in: true }, 401);
    if (String(url).includes('/api/downloads/queue')) return json({ downloads: [] });
    if (String(url).includes('/api/downloads/history')) return json({ history: [] });
    if (String(url).includes('/api/storage/downloads')) {
      return json({ path: '/downloads', available: true, total_bytes: 4000 * GIB, used_bytes: 3588 * GIB, free_bytes: 412 * GIB,
        cleanup_target_bytes: 100 * GIB, min_age_hours: 48, ...over });
    }
    return json({});
  }));
  return calls;
}
const card = (key: string): Card => ({ key, title: key, type: 'movie' } as Card);
const rail = (id: string): Rail => ({ id, title: id, items: [card(`${id}-1`)] } as Rail);

describe('v260 phone Home button row (prod 1b4ea74 NavPills)', () => {
  it('has the Storage pill first, then only the buttons whose screens test has, in prod order', async () => {
    stub();
    render(<MemoryRouter><HomePills /></MemoryRouter>);
    const row = screen.getByTestId('home-pills');
    await waitFor(() => within(row).getByText('412 GB free'));
    const labels = within(row).getAllByRole('button').map(b => b.textContent);
    expect(labels).toEqual(['412 GB free', 'Activity', 'TV Calendar', 'Rooms']);
    expect(HOME_PILLS.map(p => p.id)).toEqual(['activity', 'tv-calendar', 'rooms']);
    for (const gone of ['Latest', 'Luma AI', 'Library', 'Watchlist', 'Collections', 'Upgrades']) {
      expect(within(row).queryByText(gone)).toBeNull();
    }
  });

  it("uses prod's pill look", () => {
    stub();
    render(<MemoryRouter><HomePills /></MemoryRouter>);
    const b = screen.getByTestId('pill-tv-calendar');
    expect(b.style.borderRadius).toBe('999px');
    expect(b.style.padding).toBe('10px 18px');
    expect(b.style.fontSize).toBe('14px');
    expect(screen.getByTestId('home-pills').style.gap).toBe('10px');
  });

  it('goes after Continue watching, else after the first rail', () => {
    stub();
    const { rerender } = render(<MemoryRouter><HomeRails cw={[card('cw')]} rails={[rail('a'), rail('b')]} afterFirst={<div data-testid="row" />} /></MemoryRouter>);
    const order = () => Array.from(screen.getByTestId('home-rails').children).map(e => e.getAttribute('data-testid') || e.querySelector('[data-testid]')?.getAttribute('data-testid'));
    expect(order().indexOf('row')).toBe(1);
    rerender(<MemoryRouter><HomeRails cw={[]} rails={[rail('a'), rail('b')]} afterFirst={<div data-testid="row" />} /></MemoryRouter>);
    expect(order()).toEqual(['rail-a', 'row', 'rail-b']);
  });

  it('the Storage sheet has only the title, usage, amount and buttons, and the implicit owner is asked to sign in', async () => {
    stub({ min_age_hours: 72 });
    render(<MemoryRouter><HomePills /></MemoryRouter>);
    await waitFor(() => screen.getByText('412 GB free'));
    fireEvent.click(screen.getByText('412 GB free'));
    fireEvent.click(await screen.findByText('Free up 100 GB'));
    expect(await screen.findByText('Delete 100 GB')).toBeTruthy();
    const sheet = screen.getByText('Downloads storage').parentElement!;
    expect(sheet.textContent).not.toMatch(/oldest|hours|undone|skipped|permanently|\/downloads/i);
    expect(Array.from(sheet.querySelectorAll('p')).map(p => p.textContent)).toEqual([]);
    cleanup();
    auth.implicit = true;
    stub();
    render(<MemoryRouter><HomePills /></MemoryRouter>);
    await waitFor(() => screen.getByText('412 GB free'));
    fireEvent.click(screen.getByText('412 GB free'));
    expect(await screen.findByTestId('storage-sign-in')).toBeTruthy();
    expect(screen.queryByText('Free up 100 GB')).toBeNull();
  });
});
