import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, describe, it, expect, vi } from 'vitest';
import * as api from '../src/components/library/tvCalendarApi';
import { InteractiveScreen } from '../src/components/library/TvEpisodePage';
import { InteractiveModal } from '../src/components/library/TvSeriesDetail';

const rel = { title: 'Eternally.Yours.S01E04.1080p.WEB.H264-CAKES', tracker: 'T', size: 1e9, seeders: 5, leechers: 0, quality: '1080p',
  published: null, freeleech: false, download_url: 'x', season: 1, episodes: [{ id: 8453, season: 1, episode: 4 }], pack: false, rejections: [], grabbable: true };
const data = { releases: [rel], grabs_allowed: true, error: null } as any;

afterEach(() => cleanup());
describe('grabbed: false shows Not grabbed (Tester probe, 0a292585)', () => {
  for (const [label, resp, expectGrabbed] of [['existing', { ok: true, existing: true, grabbed: false, reason: 'existing' }, false],
                                               ['disabled', { ok: true, grabbed: false, reason: 'disabled' }, false],
                                               ['sent', { ok: true, grabbed: true }, true],
                                               ['legacy-no-field', { ok: true }, true]] as const) {
    it(`TvEpisodePage InteractiveScreen ${label}`, async () => {
      const onGrabbed = vi.fn();
      render(<InteractiveScreen title="t" sub="s" load={async () => ({ ok: true, data })} doGrab={async () => ({ ok: true, data: resp as any })} onBack={() => {}} onGrabbed={onGrabbed} />);
      const b = await screen.findByRole('button', { name: /grab/i });
      fireEvent.click(b);
      if (expectGrabbed) await waitFor(() => expect(onGrabbed).toHaveBeenCalled());
      else { expect(await screen.findByText('Not grabbed')).toBeTruthy(); expect(onGrabbed).not.toHaveBeenCalled(); }
    });
    it(`TvSeriesDetail InteractiveModal ${label}`, async () => {
      vi.spyOn(api, 'interactiveSearch').mockResolvedValue({ ok: true, data } as any);
      vi.spyOn(api, 'grabRelease').mockResolvedValue({ ok: true, data: resp } as any);
      const onGrabbed = vi.fn();
      render(<InteractiveModal seriesId={147} target={{ episode_id: 8453 } as any} title="EY" wide={true} onClose={() => {}} onGrabbed={onGrabbed} />);
      fireEvent.click(await screen.findByRole('button', { name: /^Grab /i }));
      if (expectGrabbed) await waitFor(() => expect(onGrabbed).toHaveBeenCalled());
      else { expect((await screen.findByTestId('tv-interactive-grab-error')).textContent).toBe('Not grabbed'); expect(onGrabbed).not.toHaveBeenCalled(); }
      vi.restoreAllMocks();
    });
  }
});
