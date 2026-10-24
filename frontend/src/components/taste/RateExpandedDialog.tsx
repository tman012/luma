/**
 * RateExpandedDialog — the verdict prompt on a tablet or desktop.
 *
 * Centred modal on PageModal/MovieDetailModal's shell: dark card, scale-in from
 * 0.96. Explicitly NOT the bottom sheet the compact layout uses — a sheet is a
 * thumb-reach affordance, and on a large pointer-driven display it reads as a
 * phone screenshot.
 *
 * Deliberately sticky: neither Escape nor a backdrop click closes it. The only
 * exits are the X and "Did not like it". A stray click on a wall-mounted or
 * always-on display would otherwise dismiss the question before it was read,
 * and the whole point is to actually capture an answer.
 *
 * The two stages get different widths on purpose: the question is a portrait
 * composition (one poster, one decision) and the answer is a landscape one (a
 * row of posters to scan).
 */

import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { posterUrl } from '@/utils/image';
import type { RateLayoutProps } from './RatePrompt';

const ENTER_MS = 500;
const EXIT_MS = 260;
const SPRING = 'cubic-bezier(0.16, 1, 0.3, 1)';

export function RateExpandedDialog({
  film, loading, error, saving, showList, suggestions, behindModal,
  onChoose, onOpenSuggestion, onDismiss,
}: RateLayoutProps) {
  const [phase, setPhase] = useState<'entering' | 'visible' | 'exiting'>('entering');

  useEffect(() => {
    const f = requestAnimationFrame(() => setPhase('visible'));
    return () => cancelAnimationFrame(f);
  }, []);

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, []);

  const close = useCallback(() => {
    setPhase('exiting');
    setTimeout(onDismiss, EXIT_MS);
  }, [onDismiss]);

  const visible = phase === 'visible';

  return createPortal(
    <div className="luma-fullscreen luma-bleed"
      data-modal="RateExpandedDialog"
      style={{
        position: 'fixed', inset: 0,
        zIndex: behindModal ? 99 : 10001,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: 32,
        background: behindModal ? 'rgba(0,0,0,0)' : visible ? 'rgba(0,0,0,0.78)' : 'rgba(0,0,0,0)',
        backdropFilter: behindModal ? 'none' : 'blur(8px)',
        opacity: behindModal ? 0 : 1,
        transition: `background-color ${visible ? ENTER_MS : EXIT_MS}ms ease, opacity 200ms ease`,
        pointerEvents: behindModal ? 'none' : 'auto',
      }}
    >
      <div
        style={{
          position: 'relative',
          width: '100%',
          // Portrait question, landscape answer.
          maxWidth: showList ? 1460 : 1060,
          maxHeight: '92vh',
          borderRadius: 24,
          overflow: 'hidden',
          backgroundColor: '#141414',
          boxShadow: '0 40px 120px rgba(0,0,0,0.85)',
          transform: visible ? 'scale(1)' : 'scale(0.96)',
          opacity: visible ? 1 : 0,
          transition: phase === 'exiting'
            ? `transform ${EXIT_MS}ms ease, opacity ${EXIT_MS}ms ease`
            : `transform ${ENTER_MS}ms ${SPRING}, opacity ${ENTER_MS}ms ${SPRING}, max-width ${ENTER_MS}ms ${SPRING}`,
          display: 'flex', flexDirection: 'column',
        }}
      >
        {/* The only exit besides "Did not like it". */}
        <button
          type="button"
          onClick={close}
          aria-label="Close"
          className="absolute right-5 top-5 z-10 flex h-11 w-11 items-center justify-center rounded-full bg-white/8 text-white/60 transition-colors hover:bg-white/15 hover:text-white/90"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>

        {loading ? (
          <div className="flex gap-12 p-12">
            <div className="h-[450px] w-[300px] shrink-0 animate-pulse rounded-2xl bg-white/8" />
            <div className="flex-1 pt-8">
              <div className="h-4 w-28 animate-pulse rounded bg-white/8" />
              <div className="mt-5 h-12 w-2/3 animate-pulse rounded bg-white/10" />
              <div className="mt-20 h-16 w-full animate-pulse rounded-xl bg-white/8" />
            </div>
          </div>
        ) : error && !film ? (
          <div className="p-12">
            <p className="text-[17px] text-rose-300/80">{error}</p>
          </div>
        ) : !showList ? (
          /* ── Stage 1: the question ────────────────────────────────── */
          <div className="flex gap-12 p-12">
            {posterUrl(film?.poster_path, 'w780') && (
              <img
                src={posterUrl(film?.poster_path, 'w780')!}
                alt=""
                className="h-[450px] w-[300px] shrink-0 rounded-2xl object-cover ring-1 ring-white/10"
              />
            )}
            <div className="flex min-w-0 flex-1 flex-col pr-12 pt-3">
              <p className="text-[13px] text-white/35">
                You watched
              </p>
              <h1 className="mt-3.5 text-[46px] font-semibold leading-[1.06] text-white/95">
                {film?.title}
              </h1>
              <p className="mt-4 text-[17px] text-white/50">
                {[film?.year,
                  film?.runtime ? `${film.runtime} min` : null,
                  film?.genres?.slice(0, 3).join(', ')].filter(Boolean).join('  ·  ')}
              </p>
              {film?.overview && (
                <p className="mt-5 line-clamp-4 max-w-[52ch] text-[16px] leading-relaxed text-white/45">
                  {film.overview}
                </p>
              )}

              <div className="mt-auto pt-10">
                {/* Centred over the pair of buttons below it. */}
                <p className="text-center text-[21px] font-medium text-white/75">
                  Did you like it?
                </p>
                <div className="mt-5 flex gap-4">
                  <button
                    type="button"
                    onClick={() => onChoose('liked')}
                    disabled={saving !== null}
                    className="min-h-[66px] flex-1 rounded-2xl border border-emerald-400/30 bg-emerald-400/12 px-8 text-[18px] font-semibold text-emerald-100 transition-colors hover:bg-emerald-400/20 disabled:opacity-50"
                  >
                    {saving === 'liked' ? 'Finding similar…' : 'Liked it'}
                  </button>
                  <button
                    type="button"
                    onClick={() => onChoose('disliked')}
                    disabled={saving !== null}
                    className="min-h-[66px] flex-1 rounded-2xl border border-white/12 bg-white/[0.04] px-8 text-[18px] font-medium text-white/65 transition-colors hover:bg-white/10 disabled:opacity-50"
                  >
                    {saving === 'disliked' ? 'Saving…' : 'Did not like it'}
                  </button>
                </div>
                {error && <p className="mt-4 text-center text-[15px] text-rose-300/80">{error}</p>}
              </div>
            </div>
          </div>
        ) : (
          /* ── Stage 2: what to watch next ──────────────────────────── */
          <>
            <div className="shrink-0 px-12 pb-7 pt-12">
              {/* Eyebrow + title centred on the modal; the caption below stays
                  left-aligned with the poster grid it describes. */}
              <p className="text-center text-[13px] text-white/35">
                Because you liked
              </p>
              <h2 className="mt-3 text-center text-[38px] font-semibold leading-tight text-white/95">
                {film?.title}
              </h2>
              <p className="mt-7 text-[16px] text-white/40">
                Similar films missing from your library
              </p>
            </div>

            <div data-scroll-container className="luma-scroll-y min-h-0 flex-1 px-12 pb-12">
              {suggestions.length === 0 ? (
                <p className="text-[17px] text-white/35">
                  Nothing new to suggest — you already own the close matches.
                </p>
              ) : (
                <div className="grid grid-cols-5 gap-6">
                  {suggestions.map((s) => {
                    const sp = posterUrl(s.poster_path, 'w500');
                    return (
                      <button
                        key={s.tmdb_id}
                        type="button"
                        onClick={() => onOpenSuggestion(s)}
                        className="group text-left"
                      >
                        {sp ? (
                          <img
                            src={sp}
                            alt=""
                            className="aspect-[2/3] w-full rounded-2xl object-cover ring-1 ring-white/10 transition-all duration-200 group-hover:-translate-y-1.5 group-hover:brightness-110 group-hover:ring-white/30"
                          />
                        ) : (
                          <div className="aspect-[2/3] w-full rounded-2xl bg-white/8" />
                        )}
                        {/* Reserve two lines so a wrapping title doesn't push
                            its year out of line with the rest of the row. */}
                        <p className="mt-4 line-clamp-2 min-h-[2.6em] text-[17px] font-medium leading-snug text-white/90 transition-colors group-hover:text-white">
                          {s.title}
                        </p>
                        <p className="text-[15px] text-white/40">{s.year || ' '}</p>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
