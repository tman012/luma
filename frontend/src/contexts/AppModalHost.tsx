import { useState, useCallback, useEffect, useMemo, useRef, lazy, Suspense, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { ModalHostContext, type ModalHostContextValue, type ModalOpenOptions } from './ModalHostContext';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { MOTION } from '@/theme/motion';
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

/** One open surface. Layers are kept in open order and every one stays mounted while another sits on top of it
 *  (a parent sheet keeps its scroll and state under a child); only the last live layer is `active` (Escape, Back,
 *  swipe). A layer that is closing is `leaving`: it plays its exit, no longer counts as the stack, and is removed
 *  when it reports the exit done. Layer ids are never reused, so a late callback from a layer that is already
 *  gone (or replaced) changes nothing. */
interface Layer {
  uid: number;
  kind: 'movie' | 'tv' | 'person';
  target?: ModalTarget;
  person?: PersonTarget;
  leaving: boolean;
}

const sameTitle = (a: Layer, b: Pick<Layer, 'kind' | 'target' | 'person'>) =>
  a.kind === b.kind && (a.kind === 'person'
    ? a.person?.id === b.person?.id
    : (a.target?.ratingKey ?? null) === (b.target?.ratingKey ?? null) && (a.target?.tmdbId ?? null) === (b.target?.tmdbId ?? null));

/** Back (popstate) dismissal: the longest sheet exit (desktop 300ms owner timers, phone --m-dur-sheet-exit 260ms). */
const DISMISS_EXIT_MS = MOTION.dur.dismissExit;
export function AppModalHost({ children }: { children: ReactNode }) {
  const { mode } = useLayoutMode();
  const location = useLocation();
  const navigate = useNavigate();
  const [layers, setLayersState] = useState<Layer[]>([]);
  const [vibeEngineOpen, setVibeEngineOpen] = useState(false);
  // Always the latest layers, also between a state update and its render: every change goes through `commit`.
  const layersRef = useRef<Layer[]>([]);
  const uidRef = useRef(0);
  const commit = useCallback((fn: (prev: Layer[]) => Layer[]) => {
    const next = fn(layersRef.current);
    if (next === layersRef.current) return next;
    layersRef.current = next;
    setLayersState(next);
    return next;
  }, []);
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
  openRef.current = layers.length > 0 || vibeEngineOpen;
  // Nested UI (Sources sheet, etc.) can consume system/gesture back first: one handler per layer, and only the
  // top live layer's is asked.
  const backHandlersRef = useRef(new Map<number, () => boolean>());
  const topLive = (list: Layer[] = layersRef.current) => {
    for (let i = list.length - 1; i >= 0; i--) if (!list[i]!.leaving) return list[i]!;
    return undefined;
  };

  const announceClosedIfEmpty = useCallback((next: Layer[]) => {
    if (next.length === 0) window.dispatchEvent(new Event('modal-closed'));
  }, []);

  /** A layer finished its exit (or was removed outright): drop it. Unknown ids (already gone) are ignored. */
  const removeLayer = useCallback((uid: number) => {
    if (!layersRef.current.some((l) => l.uid === uid)) return;
    backHandlersRef.current.delete(uid);
    announceClosedIfEmpty(commit((prev) => prev.filter((l) => l.uid !== uid)));
  }, [commit, announceClosedIfEmpty]);

  const markLeaving = useCallback((uid: number) => {
    if (!layersRef.current.some((l) => l.uid === uid && !l.leaving)) return;
    commit((prev) => prev.map((l) => (l.uid === uid ? { ...l, leaving: true } : l)));
  }, [commit]);

  const pushLayer = useCallback((layer: Pick<Layer, 'kind' | 'target' | 'person'>, replaceStack?: boolean) => {
    const cur = layersRef.current;
    const top = topLive(cur);
    // The same title opened again while it is the top sheet (a double tap, a second Enter) is the same open.
    if (!replaceStack && top && sameTitle(top, layer)) return;
    commit((prev) => {
      const entry: Layer = { ...layer, uid: ++uidRef.current, leaving: false };
      if (replaceStack) { backHandlersRef.current.clear(); return [entry]; }
      // A person opened over a person replaces it; everything else stacks on what is open.
      const replacedPerson = layer.kind === 'person' && top?.kind === 'person' ? top.uid : null;
      return [...prev.filter((l) => l.uid !== replacedPerson), entry];
    });
    window.dispatchEvent(new Event('modal-opened'));
  }, [commit]);

  const registerBackHandler = useCallback((uid: number, handler: (() => boolean) | null) => {
    if (handler) backHandlersRef.current.set(uid, handler); else backHandlersRef.current.delete(uid);
  }, []);

  const openMovie = useCallback((opts: ModalOpenOptions) => {
    console.log('[MODAL OPEN]', { mode, source: opts.origin ?? 'unknown', mediaType: 'movie', tmdbId: opts.tmdbId, ratingKey: opts.ratingKey });
    pushLayer({ kind: 'movie', target: { ...opts, mediaType: 'movie' } }, opts.replaceStack);
  }, [mode, pushLayer]);

  const openTv = useCallback((opts: ModalOpenOptions) => {
    console.log('[MODAL OPEN]', { mode, source: opts.origin ?? 'unknown', mediaType: 'tv', tmdbId: opts.tmdbId, ratingKey: opts.ratingKey });
    pushLayer({ kind: 'tv', target: { ...opts, mediaType: 'tv' } }, opts.replaceStack);
  }, [mode, pushLayer]);

  const openPerson = useCallback((opts: { personId: number; personName: string }) => {
    pushLayer({ kind: 'person', person: { id: opts.personId, name: opts.personName } });
  }, [pushLayer]);

  const openVibeEngine = useCallback(() => {
    setVibeEngineOpen(true);
  }, []);

  /** Back out of the one surface that is on top (no exit animation; the owner of the exit calls removeLayer). */
  const close = useCallback(() => {
    const top = topLive();
    if (top && layersRef.current.filter((l) => !l.leaving).length > 1) {
      backHandlersRef.current.delete(top.uid);
      commit((prev) => prev.filter((l) => l.uid !== top.uid));
      return;
    }
    backHandlersRef.current.clear();
    commit(() => []);
    setVibeEngineOpen(false);
    window.dispatchEvent(new Event('modal-closed'));
  }, [commit]);

  const closeVibeEngine = useCallback(() => {
    setVibeEngineOpen(false);
  }, []);

  /** Tear down every open surface. Returns whether anything was showing.
   *  Reads openRef rather than the state values so it stays referentially
   *  stable — it is a dependency of the service-worker message listener, which
   *  must not re-subscribe every time a modal opens. */
  const dismissAll = useCallback(() => {
    const was = openRef.current;
    backHandlersRef.current.clear();
    commit(() => []);
    setVibeEngineOpen(false);
    if (was) {
      popClosingRef.current = true;
      window.dispatchEvent(new Event('modal-closed'));
    }
    return was;
  }, [commit]);

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
  const hasDetail = layers.some((l) => l.kind !== 'person');
  useEffect(() => {
    if (!((vibeEngineOpen || isVibeRoute) && hasDetail)) return;
    const style = document.createElement('style');
    style.textContent = '[data-modal="MovieDetailModal"], [data-modal="TvDetailModal"] { z-index: 10000 !important; }';
    document.head.appendChild(style);
    return () => { document.head.removeChild(style); };
  }, [vibeEngineOpen, isVibeRoute, hasDetail]);

  // Android/PWA hardware & gesture back should close/pop an open modal instead of
  // navigating the route away (or exiting the PWA). Maintain one history entry
  // per open layer so back pops one level at a time.
  const depth = layers.length > 0 ? layers.length : vibeEngineOpen ? 1 : 0;
  useEffect(() => {
    const desired = depth;

    const onPop = () => {
      if (syncingHistoryRef.current > 0) {
        syncingHistoryRef.current -= 1;
        return;
      }
      historyDepthRef.current = Math.max(0, historyDepthRef.current - 1);

      // Nested sheet (e.g. Sources) consumes back → the detail sheet under it stays.
      const top = topLive();
      if (top && backHandlersRef.current.get(top.uid)?.()) {
        window.history.pushState({ __modal: true }, '');
        historyDepthRef.current += 1;
        return;
      }

      const all = layersRef.current;
      const live = all.filter((l) => !l.leaving);
      // Everything that is open is already closing: let it finish.
      if (all.length > 0 && live.length === 0) return;

      // MOTION.md §1.6: back runs the top sheet's exit animation first (the sheet claims the dismissal with
      // preventDefault on 'luma:modal-dismiss'), then the host removes it.
      if (live.length > 1 && top) {
        const claimed = !window.dispatchEvent(new CustomEvent('luma:modal-dismiss', { cancelable: true }));
        if (claimed) { markLeaving(top.uid); window.setTimeout(() => removeLayer(top.uid), DISMISS_EXIT_MS); } else removeLayer(top.uid);
        return;
      }

      // No stacked parent: dismiss everything.
      popClosingRef.current = true;
      const clear = () => {
        backHandlersRef.current.clear();
        commit(() => []);
        setVibeEngineOpen(false);
        window.dispatchEvent(new Event('modal-closed'));
      };
      const claimed = !window.dispatchEvent(new CustomEvent('luma:modal-dismiss', { cancelable: true }));
      if (claimed) {
        live.forEach((l) => markLeaving(l.uid));
        window.setTimeout(clear, DISMISS_EXIT_MS);
      } else clear();
    };

    window.addEventListener('popstate', onPop);

    // Push missing entries for newly opened root or nested modals.
    while (historyDepthRef.current < desired) {
      window.history.pushState({ __modal: true }, '');
      historyDepthRef.current += 1;
    }
    // A layer that closed from the UI leaves an extra entry: consume it without re-entering onPop logic.
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
  }, [depth, commit, markLeaving, removeLayer]);

  // When modal is open, mark app content as inert for focus trapping
  const anyModalOpen = hasDetail || vibeEngineOpen;
  useEffect(() => {
    const appRoot = document.getElementById('root');
    if (!appRoot) return;
    if (anyModalOpen) {
      appRoot.setAttribute('data-modal-open', 'true');
    } else {
      appRoot.removeAttribute('data-modal-open');
    }
  }, [anyModalOpen]);

  const hostValue = useMemo<ModalHostContextValue>(() => ({
    openMovie, openTv, openPerson, openVibeEngine, close, registerBackHandler: (h) => registerBackHandler(0, h),
  }), [openMovie, openTv, openPerson, openVibeEngine, close, registerBackHandler]);
  // Each layer reads the same host with its own back handler slot (one stable value per layer and host).
  const layerValues = useRef(new Map<number, { host: ModalHostContextValue; value: ModalHostContextValue }>());
  const valueFor = (uid: number): ModalHostContextValue => {
    const hit = layerValues.current.get(uid);
    if (hit && hit.host === hostValue) return hit.value;
    const value = { ...hostValue, registerBackHandler: (h: (() => boolean) | null) => registerBackHandler(uid, h) };
    layerValues.current.set(uid, { host: hostValue, value });
    return value;
  };
  for (const uid of layerValues.current.keys()) if (!layers.some((l) => l.uid === uid)) layerValues.current.delete(uid);
  const topUid = topLive(layers)?.uid;

  return (
    <ModalHostContext.Provider value={hostValue}>
      {children}
      {vibeEngineOpen && (
        <Suspense fallback={null}>
          <VibeEngineModal onClose={closeVibeEngine} />
        </Suspense>
      )}
      {layers.map((l) => {
        const done = () => removeLayer(l.uid);
        const leave = () => markLeaving(l.uid);
        const active = l.uid === topUid;
        return (
          <ModalHostContext.Provider key={l.uid} value={valueFor(l.uid)}>
            {l.kind === 'person' && l.person ? (
              <PersonDetailModal personId={l.person.id} personName={l.person.name} onClose={done} onLeave={leave} active={active} />
            ) : l.kind === 'tv' && l.target ? (
              <TvDetailModal
                ratingKey={l.target.ratingKey}
                tmdbId={l.target.tmdbId}
                origin={l.target.origin}
                onClose={done}
                onLeave={leave}
                active={active}
                onPersonSearch={l.target.onPersonSearch}
                episodeContext={l.target.episodeContext}
                initialSeason={l.target.initialSeason}
                searchQuery={l.target.searchQuery}
              />
            ) : l.target ? (
              <MovieDetailModal
                ratingKey={l.target.ratingKey}
                tmdbId={l.target.tmdbId}
                origin={l.target.origin}
                onClose={done}
                onLeave={leave}
                active={active}
                onPersonSearch={l.target.onPersonSearch}
                restoreSimilar={l.target.restoreSimilar}
                searchQuery={l.target.searchQuery}
              />
            ) : null}
          </ModalHostContext.Provider>
        );
      })}
    </ModalHostContext.Provider>
  );
}
