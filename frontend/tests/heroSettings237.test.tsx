import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { HeroSettingsRows } from '@/components/home/HeroSettings';

const json = (body: unknown, ok = true) => ({ ok, status: ok ? 200 : 500, json: async () => body }) as Response;
const BASE = { source: 'newest', section_id: '1', limit: 10, libraries: [{ id: '1', title: 'Movies' }],
  excluded_genres: [] as string[], genres: ['Comedy', 'Documentary', 'Horror'] };

function api(fail = false) {
  let cur = { ...BASE };
  return vi.fn(async (_u: string, init?: RequestInit) => {
    if (init?.method === 'PUT') {
      if (fail) return json({}, false);
      cur = { ...cur, ...JSON.parse(String(init.body)) }; return json(cur);
    }
    return json(cur);
  });
}

describe('v237 Settings > Home screen > Hero source and count', () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('shows Recently added (current hero) and Most popular, default 10, no helper text', async () => {
    vi.stubGlobal('fetch', api());
    render(<HeroSettingsRows />);
    const sel = await screen.findByTestId('hero-source') as HTMLSelectElement;
    expect(sel.value).toBe('newest');
    expect([...sel.options].map(o => o.textContent)).toEqual(['Recently added', 'Most popular']);
    const n = screen.getByTestId('hero-count') as HTMLInputElement;
    expect(n.value).toBe('10'); expect(n.min).toBe('5'); expect(n.max).toBe('30');
    expect(n.className).not.toContain('font-mono'); expect(n.style.fontFamily).not.toBe('');   // v238: UI font
    expect(screen.getByTestId('hero-settings').textContent).toBe('HeroRecently addedMost popularNumber of moviesExcluded genresNone');
  });

  it('saves the source, and clamps the count to 5-30 on blur', async () => {
    const f = api(); vi.stubGlobal('fetch', f);
    render(<HeroSettingsRows />);
    fireEvent.change(await screen.findByTestId('hero-source'), { target: { value: 'popular' } });
    await waitFor(() => expect(f.mock.calls.some(c => (c[1] as RequestInit)?.body === '{"source":"popular"}')).toBe(true));
    const n = screen.getByTestId('hero-count') as HTMLInputElement;
    fireEvent.change(n, { target: { value: '50' } }); fireEvent.blur(n);
    await waitFor(() => expect(n.value).toBe('30'));
    expect(f.mock.calls.some(c => (c[1] as RequestInit)?.body === '{"limit":30}')).toBe(true);
    fireEvent.change(n, { target: { value: '1' } }); fireEvent.blur(n);
    await waitFor(() => expect(n.value).toBe('5'));
  });

  it('a failed save puts the old value back', async () => {
    vi.stubGlobal('fetch', api(true));
    render(<HeroSettingsRows />);
    const sel = await screen.findByTestId('hero-source') as HTMLSelectElement;
    fireEvent.change(sel, { target: { value: 'popular' } });
    await waitFor(() => expect(sel.value).toBe('newest'));
  });

  it('v240: Excluded genres is a multi-select; each pick saves the full list right away', async () => {
    const f = api(); vi.stubGlobal('fetch', f);
    render(<HeroSettingsRows />);
    const btn = await screen.findByTestId('hero-genres');
    expect(screen.getByTestId('hero-genres-value').textContent).toBe('None');
    expect(btn.className).toBe((screen.getByTestId('hero-source') as HTMLElement).className.replace('cursor-pointer', '').trim() + ' cursor-pointer flex items-center justify-between text-left');
    fireEvent.click(btn);
    // v241b: app-styled boxes, not native
    expect(document.head.innerHTML + document.body.innerHTML).toContain('.luma-check{appearance:none');
    const boxes = within(screen.getByTestId('hero-genres-list')).getAllByRole('option') as HTMLInputElement[];
    expect(boxes.every(b => b.className === 'luma-check')).toBe(true);
    expect(boxes.map(b => b.parentElement!.textContent)).toEqual(['Comedy', 'Documentary', 'Horror']);
    fireEvent.click(boxes[2]!);
    await waitFor(() => expect(screen.getByTestId('hero-genres-value').textContent).toBe('Horror'));
    fireEvent.click(within(screen.getByTestId('hero-genres-list')).getAllByRole('option')[1]!);
    await waitFor(() => expect(screen.getByTestId('hero-genres-value').textContent).toBe('Horror, Documentary'));
    const bodies = f.mock.calls.map(c => (c[1] as RequestInit)?.body).filter(Boolean);
    expect(bodies).toEqual(['{"excluded_genres":["Horror"]}', '{"excluded_genres":["Horror","Documentary"]}']);
    fireEvent.click(within(screen.getByTestId('hero-genres-list')).getAllByRole('option')[2]!);
    await waitFor(() => expect(screen.getByTestId('hero-genres-value').textContent).toBe('Documentary'));
  });
});
