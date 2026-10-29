/** Designer note on eb0be665: a Trending TV reply missing settings fields rendered 'undefined' in List page
 * and left the selects empty. Missing values fall back to the server defaults; NumField never shows 'undefined'. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { TrendingTvSettings, TTV_DEFAULTS, normTtv } from '@/components/automation/TrendingTvSettings';
import { NumField } from '@/components/automation/GenreRouletteSettings';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const ok = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
const sparse = { settings: { enabled: true, history_kept: 200, languages: [] }, status: { running: false, enabled: true, last_run: null, next_run: null, last_result: null }, languages: [], history: [] };

describe('automation inputs never render undefined', () => {
  it('normTtv fills missing settings from the server defaults', () => {
    const v = normTtv(sparse)!;
    expect(v.settings.page).toBe(1);
    expect(v.settings.interval_hours).toBe(TTV_DEFAULTS.interval_hours);
    expect(v.settings.when_busy).toBe('skip');
    expect(v.settings.limit).toBe(10);
    expect(v.settings.history_kept).toBe(200);
    expect(v.profiles).toEqual([]);
  });
  it('NumField shows an empty field for a missing value', () => {
    render(<NumField id="x" value={undefined as unknown as number} onCommit={() => {}} />);
    expect((screen.getByTestId('x-input') as HTMLInputElement).value).toBe('');
  });
  it('Trending TV with a sparse reply shows defaults, no undefined anywhere', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok(sparse)));
    const { container } = render(<TrendingTvSettings />);
    const page = await screen.findByTestId('ttv-page-input') as HTMLInputElement;
    expect(page.value).toBe('1');
    expect(screen.getByTestId('ttv-every-select').textContent).toContain('2 hours');
    expect(screen.getByTestId('ttv-busy-select').textContent).toContain('Skip this run');
    expect(container.textContent).not.toContain('undefined');
    container.querySelectorAll('input').forEach(i => expect(i.value).not.toContain('undefined'));
  });
});
