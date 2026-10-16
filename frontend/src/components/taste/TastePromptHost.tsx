/**
 * TastePromptHost — asks for a verdict on the kiosk, the way the notification
 * asks on the phone.
 *
 * Expanded mode only. A phone already gets a push; a wall-mounted or desktop
 * display gets no notification at all, so without this the whole feature is
 * invisible on the surface the app is mostly used from. Same RatePrompt, same
 * question, different trigger.
 *
 * Restraint matters more here than on the phone: a push can be ignored, but a
 * takeover interrupts. So it asks once per film and never again — dismissing
 * writes a server-side dismissal, which also suppresses the push, because
 * having waved the question away on one device you should not be asked on the
 * other.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { RatePrompt } from './RatePrompt';
import { fetchTastePending, dismissFilm, type TasteWatch } from '@/services/tasteApi';

/** A film has to have been finished this recently to be worth interrupting for.
 *  Only the newest watch in the window is ever prompted — see latest_unrated in
 *  app/taste.py. */
const WINDOW_HOURS = 24;
/** Plex scrobbles at ~90%, so the credits are usually still rolling. Wait. */
const SETTLE_MINUTES = 20;
/** Cheap enough to poll often: filtered by accountID + viewedAt>, Plex returns
 *  ~2 KB in ~100ms rather than the 9.7 MB the unfiltered history costs. A
 *  minute is the difference between the prompt feeling automatic and feeling
 *  like it needs a page refresh. */
const POLL_MS = 60 * 1000;
/** Let the page settle before interrupting on a fresh load. */
const INITIAL_DELAY_MS = 6000;

export function TastePromptHost() {
  const { mode } = useLayoutMode();
  const { authenticated } = useAuth();
  const { pathname } = useLocation();

  // Mounted at the app root so it survives every route, which means it has to
  // rule itself out rather than rely on where it sits: not before login, and
  // not on /rate/:id, where the routed sheet is already asking.
  const active = mode === 'expanded' && authenticated && !pathname.startsWith('/rate/');

  const [film, setFilm] = useState<TasteWatch | null>(null);
  // Films handled in this tab. The server is the durable record, but polling
  // continues after a verdict and the response may not reflect it yet.
  const seen = useRef<Set<number>>(new Set());

  const check = useCallback(async () => {
    // Never stack on top of an existing prompt, and never interrupt a modal the
    // user opened themselves.
    if (film) return;
    try {
      const r = await fetchTastePending(WINDOW_HOURS);
      // `latest` only, never the `pending` backlog: if a question went
      // unanswered, the next film supersedes it rather than queueing behind it.
      const next = r.latest;
      const cutoff = Date.now() / 1000 - SETTLE_MINUTES * 60;
      if (next && next.watched_at <= cutoff && !seen.current.has(next.tmdb_id)) {
        setFilm(next);
      }
    } catch {
      /* offline or Plex down — try again next tick */
    }
  }, [film]);

  useEffect(() => {
    if (!active) return;
    const first = setTimeout(check, INITIAL_DELAY_MS);
    const iv = setInterval(check, POLL_MS);
    // Also check the moment the tab or display comes back into use — someone
    // returning to the kiosk after watching something should not have to wait
    // out the interval.
    const onVisible = () => { if (document.visibilityState === 'visible') check(); };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    return () => {
      clearTimeout(first);
      clearInterval(iv);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, [active, check]);

  // Closing after a verdict is not a dismissal — the question was answered.
  // Only an unanswered close should write one.
  const answered = useRef(false);

  const close = useCallback(() => {
    if (film) {
      seen.current.add(film.tmdb_id);
      if (!answered.current) dismissFilm(film.tmdb_id).catch(() => {});
    }
    answered.current = false;
    setFilm(null);
  }, [film]);

  const rated = useCallback(() => {
    answered.current = true;
    if (film) seen.current.add(film.tmdb_id);
  }, [film]);

  if (!active || !film) return null;
  return <RatePrompt tmdbId={film.tmdb_id} onClose={close} onRated={rated} />;
}
