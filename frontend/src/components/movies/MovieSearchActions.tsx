/** v203 (test install): the movie page's Search automatically and Interactive search, with the E2 dialog
 * (searching, grabbed, none) and the interactive list the episode page uses (TvEpisodePage). */
import { useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { FONT } from '@/components/start/StartUI';
import { glassBtn, primaryBtn } from '@/components/library/TvEdit';
import { AutoDialog, InteractiveScreen, qualityText, type Auto } from '@/components/library/TvEpisodePage';
import type { InteractiveResult, Release } from '@/components/library/tvCalendarApi';
import { apiFetch } from '@/apiClient';

const BASE = '/api/movies/watchlist';
type R<T> = { ok: true; data: T } | { ok: false; error: string };

async function post<T>(url: string, body: unknown): Promise<R<T>> {
  try {
    const res = await apiFetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({})) as T & { error?: string | null };
    if (res.ok) return { ok: true, data };
    return { ok: false, error: data.error || "Couldn't search" };
  } catch {
    return { ok: false, error: "Couldn't search" };
  }
}

export interface MoviePick { title: string; quality: string | null; size: number | null; grabbed: boolean; error?: string | null }
export const searchMovie = (tmdbId: number) =>
  post<{ ok: boolean; picks: MoviePick[]; error: string | null }>(`${BASE}/${tmdbId}/search`, {});
export const interactiveMovie = (tmdbId: number) => post<InteractiveResult>(`${BASE}/${tmdbId}/interactive`, {});
export const grabMovieRelease = (tmdbId: number, r: Release) =>
  post<{ ok: boolean; error?: string | null }>(`${BASE}/${tmdbId}/interactive/grab`,
    { download_url: r.download_url, title: r.title, size: r.size, quality: r.quality ?? undefined, indexer: r.tracker ?? undefined });

export function MovieSearchActions({ tmdbId, title, year }: { tmdbId: number; title: string; year?: number | null }) {
  const [auto, setAuto] = useState<Auto | null>(null);
  const [inter, setInter] = useState(false);
  const run = useRef(0);
  const sub = year ? `${title} (${year})` : title;

  const searchAuto = async () => {
    const my = ++run.current;
    setAuto({ k: 'searching' });
    const r = await searchMovie(tmdbId);
    if (run.current !== my) return;
    const pick = r.ok ? r.data.picks.find(p => p.grabbed) : undefined;
    if (pick) { setAuto({ k: 'grabbed', title: pick.title, quality: qualityText(pick.quality), size: pick.size }); return; }
    const err = r.ok ? r.data.error : r.error;
    setAuto(!err ? { k: 'none' } : { k: 'msg', text: err });
  };
  const btn: React.CSSProperties = { height: 52, width: '100%', borderRadius: 14, fontSize: 16, display: 'inline-flex',
    alignItems: 'center', justifyContent: 'center', gap: 8 };
  return (
    <div data-modal-keep="" data-testid="movie-search-actions" onClick={e => e.stopPropagation()}
      style={{ fontFamily: FONT, display: 'flex', flexDirection: 'column', gap: 12, width: 'calc(100% - 32px)', maxWidth: 420, margin: '12px auto 0' }}>
      <button type="button" className="lp-focus tve-primary" data-testid="movie-search-auto" onClick={() => void searchAuto()}
        style={{ ...primaryBtn(true), ...btn }}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
        Search automatically
      </button>
      <button type="button" className="lp-focus tve-glass" data-testid="movie-search-interactive" onClick={() => setInter(true)}
        style={{ ...glassBtn, ...btn }}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" /></svg>
        Interactive search
      </button>
      {auto && createPortal(
        <AutoDialog auto={auto} sub={sub} onClose={() => { run.current++; setAuto(null); }}
          onInteractive={() => { setAuto(null); setInter(true); }} />, document.body)}
      {inter && createPortal(
        <div className="luma-bleed-down" data-testid="movie-interactive-overlay" style={{ position: 'fixed', inset: 0, zIndex: 1000, overflowY: 'auto', padding: '16px 16px calc(32px + var(--luma-end-pad))',
          background: 'rgba(21,16,42,0.96)', backdropFilter: 'blur(40px)', WebkitBackdropFilter: 'blur(40px)' }}>
          <div style={{ maxWidth: 720, margin: '0 auto' }}>
            <InteractiveScreen title={title} sub={year ? String(year) : 'Movie'} load={() => interactiveMovie(tmdbId)}
              doGrab={r => grabMovieRelease(tmdbId, r)} onBack={() => setInter(false)}
              onGrabbed={r => { setInter(false); setAuto({ k: 'grabbed', title: r.title, quality: r.quality_label || qualityText(r.quality), size: r.size }); }} />
          </div>
        </div>, document.body)}
    </div>
  );
}
