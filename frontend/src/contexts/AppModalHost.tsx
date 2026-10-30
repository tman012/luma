import { useState, useCallback, useEffect, useRef, lazy, Suspense, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { ModalHostContext, type ModalOpenOptions } from './ModalHostContext';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { MovieDetailModal } from '@/components/media/MovieDetailModal';
import { TvDetailModal } from '@/components/media/TvDetailModal';
import { PersonDetailModal } from '@/components/media/PersonDetailModal';

const VibeEngineModal = lazy(() =>
  import('@/components/media/VibeEngineModal').then((m) => ({ default: m.VibeEngineModal })),
);

interface ModalTarget extends ModalOpenOptions {
  mediaType: 'movie' | 'tv';
}

interface PersonTarget {
  id: number;
  name: string;
}

interface StackEntry {
  target: ModalTarget | null;
  personTarget: PersonTarget | null;
}

/** Back (popstate) dismissal: the longest sheet exit (desktop 300ms owner timers, phone --m-dur-sheet-exit 260ms). */
const DISMISS_EXIT_MS = 300;
export function AppModalHost({ children }: { children: ReactNode }) {
  const { mode } = useLayoutMode();
  const location = useLocation();
  const navigate = useNavigate();
  const [target, setTarget] = useState<ModalTarget | null>(null);
  const [personTarget, setPersonTarget] = useState<PersonTarget | null>(null);
  const [stack, setStack] = useState<StackEntry[]>([]);
  const [vibeEngineOpen, setVibeEngineOpen] = useState(false);
  // Set when the modal history entry is being disposed of by something other
  // than a back-nav, so the effect below doesn't also compensate with back().
  const popClosingRef = useRef(false);
  // Ignore popstate fired by our own history.back() when syncing depth after a
  // UI (X) close of a nested modal — otherwise onPop would pop the stack twice.
  const syncingHistoryRef = useRef(0);
  // How many {__modal:true} history entries we currently own.
  const historyDepthRef = useRef(0);
  // Mirror of "is anything showing", kept in sync on every render so callbacks
  // can read it without taking the state as a dependency.
  const openRef = useRef(false);
  openRef.current = !!target || !!personTarget || vibeEngineOpen;
  // Modal stack mirror for the popstate handler (avoids stale closures).
  const stackRef = useRef(stack);
  stackRef.current = stack;
  // Nested UI (Sources sheet, etc.) can consume system/gesture back first.
  const nestedBackHandlerRef = useRef<(() => boolean) | null>(null);
  const registerBackHandler = useCallback((handler: (() => boolean) | null) => {
    nestedBackHandlerRef.current = handler;
  }, []);

  const openMovie = useCallback((opts: ModalOpenOptions) => {
    console.log('[MODAL OPEN]', { mode, source: opts.origin ?? 'unknown', mediaType: 'movie', tmdbId: opts.tmdbId, ratingKey: opts.ratingKey });
    // If a detail OR person modal is already open, push it onto the stack so
    // closing the new modal restores the previous one (incl. a standalone
    // person modal — otherwise opening a credit would drop the filmography).
    // Unless this is an arrival from outside the app, which replaces.
    if (opts.replaceStack) {
      setStack([]);
    } else if (target || personTarget) {
      // Drilling from More Like This: remember to reopen Similar when this
      // movie is restored from the stack.
      const stackedTarget = target && opts.origin === 'similar_movies'
        ? { ...target, restoreSimilar: true }
        : target;
      setStack(s => [...s, { target: stackedTarget, personTarget }]);
    }
    setTarget({ ...opts, mediaType: 'movie' });
    setPersonTarget(null);
    window.dispatchEvent(new Event('modal-opened'));
  }, [mode, target, personTarget]);

  const openTv = useCallback((opts: ModalOpenOptions) => {
    console.log('[MODAL OPEN]', { mode, source: opts.origin ?? 'unknown', mediaType: 'tv', tmdbId: opts.tmdbId, ratingKey: opts.ratingKey });
    if (opts.replaceStack) {
      setStack([]);
    } else if (target || personTarget) {
      setStack(s => [...s, { target, personTarget }]);
    }
    setTarget({ ...opts, mediaType: 'tv' });
    setPersonTarget(null);
    window.dispatchEvent(new Event('modal-opened'));
  }, [mode, target, personTarget]);

  const openPerson = useCallback((opts: { personId: number; personName: string }) => {
    setPersonTarget({ id: opts.personId, name: opts.personName });
  }, []);

  const openVibeEngine = useCallback(() => {
    setVibeEngineOpen(true);
  }, []);

  const closeDetail = useCallback(() => {
    setTarget(null);
    setPersonTarget(null);
    setStack([]);
    window.dispatchEvent(new Event('modal-closed'));
  }, []);

  const close = useCallback(() => {
    // If we have a stacked modal, pop back to it instead of dismissing entirely.
    const next = stack.length > 0 ? stack[stack.length - 1] : undefined;
    if (next) {
      setTarget(next.target);
      setPersonTarget(next.personTarget);
      setStack(s => s.slice(0, -1));
      // History depth is synced by the anyOpen/stackLen effect below (it will
      // history.back() to drop the nest entry without dismissing the parent).
      return;
    }
    setTarget(null);
    setPersonTarget(null);
    setVibeEngineOpen(false);
    window.dispatchEvent(new Event('modal-closed'));
  }, [stack]);

  const closePerson = useCallback(() => {
    setPersonTarget(null);
  }, []);

  const closeVibeEngine = useCallback(() => {
    setVibeEngineOpen(false);
  }, []);

  /** Tear down every open surface. Returns whether anything was showing.
   *  Reads openRef rather than the state values so it stays referentially
   *  stable — it is a dependency of the service-worker message listener, which
   *  must not re-subscribe every time a modal opens. */
  const dismissAll = useCallback(() => {
    const was = openRef.current;
    setTarget(null);
    setPersonTarget(null);
    setVibeEngineOpen(false);
    setStack([]);
    if (was) {
      popClosingRef.current = true;
      window.dispatchEvent(new Event('modal-closed'));
    }
    return was;
  }, []);

  // Parse modal deep-link params from a URL string and open the modal
  const handleDeepLink = useCallback((urlStr: string) => {
    try {
      const url = new URL(urlStr, window.location.origin);
      const modalType = url.searchParams.get('modal');
      const tmdbId = parseInt(url.searchParams.get('tmdb') || '', 10);
      const seasonRaw = url.searchParams.get('season');
      const episodeRaw = url.searchParams.get('episode');
      const season = seasonRaw ? parseInt(seasonRaw, 10) : undefined;
      const episode = episodeRaw ? parseInt(episodeRaw, 10) : undefined;
      if (modalType && tmdbId) {
        if (modalType === 'tv') {
          // Single episode deep link → episode detail mode
          if (season && !isNaN(season) && episode && !isNaN(episode)) {
            openTv({ tmdbId, origin: 'notification', replaceStack: true, episodeContext: { seasonNumber: season, episodeNumber: episode } });
          } else {
            // Season-level deep link → auto-select season
            openTv({ tmdbId, origin: 'notification', replaceStack: true, initialSeason: season && !isNaN(season) ? season : undefined });
          }
        } else {
          openMovie({ tmdbId, origin: 'notification', replaceStack: true });
        }
        return;
      }
      // Not a modal deep link — a plain route (e.g. /rate/123). Navigating is
      // the whole point of the notification, and without this branch a warm
      // open just focused whatever page was already showing and dropped the
      // link on the floor. Applies to every path-style notification, not only
      // the ones that happen to carry ?modal=.
      // Read the current path off window rather than the router's `location`:
      // this callback is a dependency of the SW message listener, so taking
      // `location` as a dep would re-subscribe on every navigation — and
      // omitting it would compare against a stale path.
      const to = `${url.pathname}${url.search}`;
      const here = `${window.location.pathname}${window.location.search}`;
      if (url.origin === window.location.origin && to !== '/' && to !== here) {
        // Anything already open has to go, or the new page arrives underneath a
        // modal from an older notification and never becomes visible. `replace`
        // consumes the history entry the open modal pushed — pair it with the
        // popClosing flag so the teardown below doesn't also call back() and
        // bounce us straight off the page we just navigated to.
        if (dismissAll()) {
          navigate(to, { replace: true });
        } else {
          navigate(to);
        }
      }
    } catch { /* ignore malformed URLs */ }
  }, [openMovie, openTv, navigate, dismissAll]);

  // Take the pending deep link the service worker wrote on notification click,
  // if there is one. Delete-on-read, so the several places that call this can
  // race freely — only the first gets the link. Resolves true when one was
  // consumed.
  const consumePendingDeepLink = useCallback(async (_from: string): Promise<boolean> => {
    if (!('caches' in window)) return false;
    try {
      const cache = await caches.open('notification-deeplink');
      const resp = await cache.match('/pending-deeplink');
      if (!resp) return false;
      await cache.delete('/pending-deeplink');
      const data: { url: string; ts: number } = await resp.json();
      // Stale links are dropped so a tap from yesterday doesn't yank the screen
      // away today. Generous, because the slow route here is a cold PWA start —
      // process launch, shell fetch and React mount can easily outlast the 30s
      // this used to allow, and dropping the link then looked exactly like the
      // deep link never working at all.
      if (Date.now() - data.ts > 5 * 60_000) return false;
      handleDeepLink(data.url);
      return true;
    } catch {
      return false;
    }
  }, [handleDeepLink]);

  // Deep link: open modal from URL params on initial load (e.g. /?modal=movie&tmdb=12345)
  // Also checks Cache Storage for a pending deep link stored by the service worker
  // (PWA cold start ignores URL params — SW writes the link to cache instead).
  useEffect(() => {
    // Strategy 1: URL params (works when browser honours the URL)
    const params = new URLSearchParams(window.location.search);
    const modalType = params.get('modal');
    const tmdbId = parseInt(params.get('tmdb') || '', 10);
    if (modalType && tmdbId) {
      const seasonRaw = params.get('season');
      const episodeRaw = params.get('episode');
      const season = seasonRaw ? parseInt(seasonRaw, 10) : undefined;
      const episode = episodeRaw ? parseInt(episodeRaw, 10) : undefined;
      // Clean the URL so refreshing doesn't re-open the modal
      const clean = new URL(window.location.href);
      clean.searchParams.delete('modal');
      clean.searchParams.delete('tmdb');
      clean.searchParams.delete('season');
      clean.searchParams.delete('episode');
      window.history.replaceState({}, '', clean.pathname || '/');
      // Open the modal
      if (modalType === 'tv') {
        if (season && !isNaN(season) && episode && !isNaN(episode)) {
          openTv({ tmdbId, origin: 'notification', replaceStack: true, episodeContext: { seasonNumber: season, episodeNumber: episode } });
        } else {
          openTv({ tmdbId, origin: 'notification', replaceStack: true, initialSeason: season && !isNaN(season) ? season : undefined });
        }
      } else {
        openMovie({ tmdbId, origin: 'notification', replaceStack: true });
      }
      return; // URL params found — no need to check cache
    }

    // Strategy 2: Check Cache Storage for pending deep link from SW notification click
    // (handles PWA cold start where openWindow() ignores URL params)
    consumePendingDeepLink('mount');
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Strategy 3: check again whenever the app comes back to the foreground.
  // Tapping a notification for an already-installed PWA frequently just resumes
  // it — no new window, no re-mount, and on iOS often no reachable client for
  // the worker to postMessage either. The mount-time check above never runs in
  // that case, which is precisely the "always lands on home" symptom. This is
  // the only hook that fires on a bare resume.
  useEffect(() => {
    const onResume = (ev: Event) => {
      if (document.visibilityState === 'visible') consumePendingDeepLink(ev.type);
    };
    document.addEventListener('visibilitychange', onResume);
    window.addEventListener('pageshow', onResume);
    window.addEventListener('focus', onResume);
    return () => {
      document.removeEventListener('visibilitychange', onResume);
      window.removeEventListener('pageshow', onResume);
      window.removeEventListener('focus', onResume);
    };
  }, [consumePendingDeepLink]);

  // In-app navigation deep links (e.g. voice command → navigate('/?modal=movie&tmdb=12345'))
  // The initial-load effect above only runs once; this watches route changes.
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const modalType = params.get('modal');
    const tmdbId = parseInt(params.get('tmdb') || '', 10);
    if (!modalType || !tmdbId) return;

    const seasonRaw = params.get('season');
    const season = seasonRaw ? parseInt(seasonRaw, 10) : undefined;

    // Clean the URL
    window.history.replaceState({}, '', location.pathname || '/');

    if (modalType === 'tv') {
      openTv({ tmdbId, origin: 'search', initialSeason: season && !isNaN(season) ? season : undefined });
    } else {
      openMovie({ tmdbId, origin: 'search' });
    }
  }, [location.search]); // eslint-disable-line react-hooks/exhaustive-deps

  // Close modal on voice navigation (e.g. "go home", "exit")
  useEffect(() => {
    const handler = () => close();
    window.addEventListener('voice-navigate-home', handler);
    return () => window.removeEventListener('voice-navigate-home', handler);
  }, [close]);

  // Listen for notification clicks forwarded by the service worker
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.data?.type !== 'NOTIFICATION_CLICK') return;
      // Go through the cache first so this click is marked consumed; otherwise
      // the next foreground event would replay the same link. Fall back to the
      // URL on the message if the worker's cache write didn't survive.
      consumePendingDeepLink('sw-message').then(handled => {
        if (!handled && event.data.url) handleDeepLink(event.data.url);
        // Tell the worker the link has been routed. It waits a beat for this and
        // hard-navigates the window if it never comes, so the ack has to be sent
        // *after* the routing above, not on receipt.
        event.ports?.[0]?.postMessage({ ok: true });
      });
    };
    navigator.serviceWorker?.addEventListener('message', handler);
    // Per spec the SW message queue is only started by assigning `onmessage` or
    // calling this; addEventListener alone leaves it paused. Chromium is more
    // forgiving than that in practice — messages arrive either way — so this is
    // conformance insurance for other engines, not the thing that makes warm
    // notification taps work.
    navigator.serviceWorker?.startMessages?.();
    return () => navigator.serviceWorker?.removeEventListener('message', handler);
  }, [handleDeepLink, consumePendingDeepLink]);

  // Elevate detail modals above the carousel (z-9999) when opened from vibe engine
  // or the /describe-it route (DescribeItPage with carousel).
  const isVibeRoute = location.pathname === '/describe-it';
  useEffect(() => {
    if (!((vibeEngineOpen || isVibeRoute) && target)) return;
    const style = document.createElement('style');
    style.textContent = '[data-modal="MovieDetailModal"], [data-modal="TvDetailModal"] { z-index: 10000 !important; }';
    document.head.appendChild(style);
    return () => { document.head.removeChild(style); };
  }, [vibeEngineOpen, isVibeRoute, target]);

  // Android/PWA hardware & gesture back should close/pop an open modal instead of
  // navigating the route away (or exiting the PWA). Maintain one history entry
  // per modal stack level (1 + stack.length) so back pops one level at a time —
  // restoring the parent with restoreSimilar when drilled from More Like This.
  const anyOpen = !!target || vibeEngineOpen || !!personTarget;
  const stackLen = stack.length;
  useEffect(() => {
    const desired = anyOpen ? 1 + stackLen : 0;

    const onPop = () => {
      if (syncingHistoryRef.current > 0) {
        syncingHistoryRef.current -= 1;
        return;
      }
      historyDepthRef.current = Math.max(0, historyDepthRef.current - 1);

      // Nested sheet (e.g. Sources) consumes back → parent detail modal stays.
      if (nestedBackHandlerRef.current?.()) {
        window.history.pushState({ __modal: true }, '');
        historyDepthRef.current += 1;
        return;
      }

      const s = stackRef.current;
      if (s.length > 0) {
        // Pop one nest level — same as close() — so Similar restore works on back.
        const next = s[s.length - 1]!;
        setTarget(next.target);
        setPersonTarget(next.personTarget);
        setStack(prev => prev.slice(0, -1));
        return;
      }

      // No stacked parent: dismiss everything. MOTION.md §1.6: back runs the open sheet's exit animation first
      // (the sheet claims the dismissal with preventDefault on 'luma:modal-dismiss'), then unmounts.
      popClosingRef.current = true;
      const clear = () => {
        setTarget(null);
        setPersonTarget(null);
        setStack([]);
        setVibeEngineOpen(false);
        window.dispatchEvent(new Event('modal-closed'));
      };
      const claimed = !window.dispatchEvent(new CustomEvent('luma:modal-dismiss', { cancelable: true }));
      if (claimed) window.setTimeout(clear, DISMISS_EXIT_MS); else clear();
    };

    window.addEventListener('popstate', onPop);

    // Push missing entries for newly opened root or nested modals.
    while (historyDepthRef.current < desired) {
      window.history.pushState({ __modal: true }, '');
      historyDepthRef.current += 1;
    }
    // UI close of a nest level already calls history.back() in close(); if we
    // still have excess (e.g. dismissAll), consume without re-entering onPop logic.
    if (historyDepthRef.current > desired) {
      const drop = historyDepthRef.current - desired;
      syncingHistoryRef.current += drop;
      historyDepthRef.current = desired;
      for (let i = 0; i < drop; i++) window.history.back();
    }

    return () => {
      window.removeEventListener('popstate', onPop);
      popClosingRef.current = false;
    };
  }, [anyOpen, stackLen]);

  // When modal is open, mark app content as inert for focus trapping
  const anyModalOpen = !!target || vibeEngineOpen;
  useEffect(() => {
    const appRoot = document.getElementById('root');
    if (!appRoot) return;
    if (anyModalOpen) {
      appRoot.setAttribute('data-modal-open', 'true');
    } else {
      appRoot.removeAttribute('data-modal-open');
    }
  }, [anyModalOpen]);

  return (
    <ModalHostContext.Provider value={{ openMovie, openTv, openPerson, openVibeEngine, close, registerBackHandler }}>
      {children}
      {vibeEngineOpen && (
        <Suspense fallback={null}>
          <VibeEngineModal onClose={closeVibeEngine} />
        </Suspense>
      )}
      {target && target.mediaType === 'tv' ? (
        <TvDetailModal
          key={`tv-${target.tmdbId ?? target.ratingKey ?? 'x'}-${stack.length}`}
          ratingKey={target.ratingKey}
          tmdbId={target.tmdbId}
          origin={target.origin}
          onClose={vibeEngineOpen ? closeDetail : close}
          onPersonSearch={target.onPersonSearch}
          episodeContext={target.episodeContext}
          initialSeason={target.initialSeason}
          searchQuery={target.searchQuery}
        />
      ) : target ? (
        <MovieDetailModal
          key={`movie-${target.tmdbId ?? target.ratingKey ?? 'x'}-${stack.length}`}
          ratingKey={target.ratingKey}
          tmdbId={target.tmdbId}
          origin={target.origin}
          onClose={vibeEngineOpen ? closeDetail : close}
          onPersonSearch={target.onPersonSearch}
          restoreSimilar={target.restoreSimilar}
          searchQuery={target.searchQuery}
        />
      ) : null}
      {personTarget && (
        <PersonDetailModal
          key={`person-${personTarget.id}`}
          personId={personTarget.id}
          personName={personTarget.name}
          onClose={closePerson}
        />
      )}
    </ModalHostContext.Provider>
  );
}
