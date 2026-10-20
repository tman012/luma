// Service Worker for the Luma PWA
// ============================================================================
// Cache strategy summary:
//   Navigation (HTML)       → NetworkFirst (fallback to cached shell)
//   /assets/* (hashed)      → CacheFirst (immutable, hash-busted by Vite)
//   /api/img/* (Plex proxy) → Network only (per-account; only the browser's own
//                             HTTP cache keeps them, private + Vary: Cookie)
//   /static/images/*        → NetworkFirst (icons change without hash)
//   /static/* (other)       → NetworkFirst (JS/CSS may change without hash)
//   /api/* (data)           → Network only (never cached)
//
// All paths above are relative to the URL base. The base is the service
// worker's own scope (it is registered at <base>/sw.js), so a Luma served
// under /luma caches /luma/, matches /luma/api/... and so on.
//
// Key design choice: /static/ uses NetworkFirst, NOT StaleWhileRevalidate.
// SWR serves stale immediately and updates in background — but Android WebView
// (Fully Kiosk) may kill background fetches, so the user gets stuck on old
// assets forever. NetworkFirst ensures fresh content on every load when online,
// with cache as offline fallback only.
// ============================================================================

// BUILD_ID is replaced at build time or bumped manually per deploy.
// When sw.js byte-content changes, the browser installs a new SW.
const CACHE_VERSION = 'media-kiosk-v211';

// Separate cache for large immutable assets — purged when version bumps
const IMMUTABLE_CACHE = 'media-immutable-v24';

// URL base without a trailing slash: '' at the root, '/luma' under /luma.
const BASE = new URL(self.registration.scope).pathname.replace(/\/+$/, '');

// An app path ('/api/x') as a URL path under the base ('/luma/api/x').
function at(path) {
  return BASE + path;
}

// A same-origin URL path as an app path, or null when it is outside the base.
function appPath(pathname) {
  if (!BASE) return pathname;
  if (pathname === BASE) return '/';
  return pathname.startsWith(BASE + '/') ? pathname.slice(BASE.length) : null;
}

// A link from a notification as a full URL path (adds the base unless present).
function linkPath(url) {
  if (!url.startsWith('/') || url.startsWith('//')) return at('/');
  return appPath(url) !== null && BASE ? url : at(url);
}

// An image URL from a push message: root-relative paths get the base,
// absolute URLs are kept.
function assetPath(url) {
  return typeof url === 'string' && url.startsWith('/') && !url.startsWith('//') ? linkPath(url) : url;
}

const SHELL_URLS = [
  at('/manifest.json'),
];

// Only the real app page may become the offline copy: never a redirect, an
// error, or the sign-in page (its form token goes stale).
function isAppShell(resp) {
  if (!resp || !resp.ok || resp.redirected || resp.type === 'opaqueredirect') return false;
  if (resp.headers.get('X-Luma-App-Shell') !== '1') return false;
  const p = appPath(new URL(resp.url || self.location.href).pathname);
  return p !== '/login' && p !== '/logout';
}

function saveShell(resp) {
  if (!isAppShell(resp)) return Promise.resolve();
  const copy = resp.clone();
  return caches.open(CACHE_VERSION).then(c => c.put(at('/'), copy));
}

// ---------------------------------------------------------------------------
// Install — purge ALL old caches, cache new shell, skip waiting immediately
// ---------------------------------------------------------------------------
self.addEventListener('install', event => {
  event.waitUntil(
    // Purge every cache except the ones we're about to create
    caches.keys().then(names => {
      const keep = new Set([CACHE_VERSION, IMMUTABLE_CACHE, 'notification-deeplink']);
      return Promise.all(
        names.filter(n => !keep.has(n)).map(n => {
          console.log('[SW] Install: purging old cache:', n);
          return caches.delete(n);
        })
      );
    })
    .then(() => caches.open(CACHE_VERSION))
    .then(cache => cache.addAll(SHELL_URLS))
    .then(() => fetch(at('/'), { redirect: 'manual', credentials: 'include', cache: 'no-store' })
      .then(saveShell)
      .catch(() => {}))
  );
  // Activate new SW immediately — don't wait for old tabs to close
  self.skipWaiting();
});

// ---------------------------------------------------------------------------
// Activate — purge ALL old version caches, claim clients
// ---------------------------------------------------------------------------
self.addEventListener('activate', event => {
  const keepCaches = new Set([CACHE_VERSION, IMMUTABLE_CACHE, 'notification-deeplink']);
  event.waitUntil(
    caches.keys().then(names =>
      Promise.all(
        names
          .filter(name => !keepCaches.has(name))
          .map(name => {
            console.log('[SW] Purging old cache:', name);
            return caches.delete(name);
          })
      )
    )
  );
  // Take control of all open tabs immediately
  event.waitUntil(self.clients.claim());
});

// Only plain successful responses are kept: never one the server marked
// no-store (or private), and never anything under /api/ (proxied images
// included), whatever its headers say.
function cacheable(resp) {
  if (!resp || !resp.ok) return false;
  let p = null;
  try { p = appPath(new URL(resp.url, self.location.origin).pathname); } catch (e) { /* no URL: headers decide */ }
  if (p !== null && p.startsWith('/api/')) return false;
  const cc = (resp.headers && resp.headers.get && resp.headers.get('Cache-Control')) || '';
  return !/no-store|private/i.test(cc);
}

// Drop any proxied image an older worker cached (sign-out, account switch).
function purgeImages() {
  return caches.keys().then(names => Promise.all(names.map(n =>
    caches.open(n).then(c => c.keys().then(reqs => Promise.all(
      reqs.filter(r => appPath(new URL(r.url).pathname)?.startsWith('/api/img/')).map(r => c.delete(r))
    )))
  )));
}

// ---------------------------------------------------------------------------
// Message handler — supports cache clearing from client
// ---------------------------------------------------------------------------
self.addEventListener('message', event => {
  if (event.data === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  if (event.data === 'PURGE_IMAGES') {
    event.waitUntil(purgeImages());
  }
  if (event.data === 'CLEAR_ALL_CACHES') {
    event.waitUntil(
      caches.keys().then(names =>
        Promise.all(names.map(n => caches.delete(n)))
      ).then(() => {
        console.log('[SW] All caches cleared via message');
      })
    );
  }
});

// ---------------------------------------------------------------------------
// Fetch strategies
// ---------------------------------------------------------------------------
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  const path = appPath(url.pathname);
  if (path === null) return; // outside Luma's URL base

  // --- API data routes — network only (never cache) ---
  // Sign-in and sign-out pages: network only, never cached.
  if (path === '/login' || path === '/logout' || path.startsWith('/auth/')) {
    return;
  }

  if (path.startsWith('/api/')) {
    return; // Data and proxied images: network only, never cached
  }

  // --- Hashed assets (/assets/index-ABC123.js) — CacheFirst (immutable) ---
  // These have content hashes in filenames; a new build = new URL = cache miss.
  if (path.startsWith('/assets/')) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(resp => {
          if (cacheable(resp)) {
            const clone = resp.clone();
            caches.open(IMMUTABLE_CACHE).then(c => c.put(event.request, clone));
          }
          return resp;
        });
      })
    );
    return;
  }

  // --- Navigation requests — NetworkFirst (offline fallback to shell) ---
  if (event.request.mode === 'navigate') {
    // Always ask the network (bypassing the HTTP cache) so a deploy shows
    // up on the next load; the saved shell is only for offline.
    event.respondWith(
      fetch(event.request, { cache: 'no-store' }).then(resp => {
        saveShell(resp);
        return resp;
      }).catch(() => caches.match(at('/')))
    );
    return;
  }

  // --- /static/images/* — NetworkFirst ---
  // Rating icons, logos etc. change without filename hash.
  // MUST go to network first so deploys are picked up immediately.
  // Cache is offline fallback only.
  if (path.startsWith('/static/images/')) {
    event.respondWith(
      fetch(event.request).then(resp => {
        if (cacheable(resp)) {
          const clone = resp.clone();
          caches.open(CACHE_VERSION).then(c => c.put(event.request, clone));
        }
        return resp;
      }).catch(() => caches.match(event.request))
    );
    return;
  }

  // --- /static/* (other: JS, CSS, manifest) — NetworkFirst ---
  // Unhashed static files may change between deploys.
  if (path.startsWith('/static/')) {
    event.respondWith(
      fetch(event.request).then(resp => {
        if (cacheable(resp)) {
          const clone = resp.clone();
          caches.open(CACHE_VERSION).then(c => c.put(event.request, clone));
        }
        return resp;
      }).catch(() => caches.match(event.request))
    );
    return;
  }
});

// ---------------------------------------------------------------------------
// Push notification handler
// ---------------------------------------------------------------------------
self.addEventListener('push', event => {
  try {
    const data = event.data ? event.data.json() : {};
    const title = data.title || 'New media available';
    const options = {
      body: data.body || '',
      icon: data.icon ? assetPath(data.icon) : at('/static/images/ic_launcher_round.png'),
      badge: data.badge ? assetPath(data.badge) : at('/static/images/ic_launcher_round.png'),
      tag: data.tag || 'default',
      renotify: true,
      // tmdb_id rides along so an action press can be resolved by the worker
      // alone, with no window and no URL parsing.
      data: { url: data.url || '/', tmdb_id: data.tmdb_id },
    };
    if (data.image) options.image = assetPath(data.image);
    // Action buttons, when the sender supplies them. Chrome/Android renders up
    // to two; iOS Safari ignores the field entirely, which is why every
    // notification must still work as a plain tap-to-open deep link.
    if (Array.isArray(data.actions) && data.actions.length) {
      options.actions = data.actions.slice(0, 2);
      options.data.actions = data.actions.slice(0, 2);
    }
    event.waitUntil(self.registration.showNotification(title, options));
  } catch (err) {
    console.error('[SW] Push handler error:', err);
  }
});

// ---------------------------------------------------------------------------
// Notification click — focus existing tab or open new one
//
// Cache Storage is the source of truth for the pending deep link, and it is
// written on EVERY click, not just when no window is found. Neither of the two
// hand-off routes is reliable on its own:
//
//   postMessage  — needs a live, reachable client. An iOS standalone PWA that
//                  the system has suspended is not one; matchAll() can come
//                  back empty even though the app is "open".
//   openWindow   — a standalone PWA is usually just brought to the foreground,
//                  restoring the page it was last on and ignoring the URL. It
//                  may not even re-mount, so there is nothing to navigate.
//
// Writing the link down first means whichever route the platform actually takes
// — message, cold start, or a bare resume — the app can pick it up. Reads
// delete the entry, so only one of them ever acts on it.
// ---------------------------------------------------------------------------
const DEEPLINK_CACHE = 'notification-deeplink';
const DEEPLINK_KEY = '/pending-deeplink';
// How long to let a live SPA claim the deep link before forcing a navigation on
// it. A page that is awake answers in single-digit milliseconds; anything
// slower is a page that isn't going to answer at all.
const ACK_TIMEOUT_MS = 1200;

function storeDeepLink(url) {
  return caches.open(DEEPLINK_CACHE)
    .then(cache => cache.put(
      DEEPLINK_KEY,
      new Response(JSON.stringify({ url, ts: Date.now() }),
                   { headers: { 'Content-Type': 'application/json' } }),
    ))
    .catch(e => console.error('[SW] Failed to cache deep link:', e));
}

self.addEventListener('notificationclick', event => {
  event.notification.close();

  // An action button was pressed rather than the notification body. The worker
  // can POST the verdict itself, so on platforms that support actions this
  // resolves without opening the app at all. Android only — iOS never gets
  // here because it doesn't render the buttons.
  const action = event.action;
  if (action && action.startsWith('verdict:')) {
    const verdict = action.slice('verdict:'.length);
    const tmdbId = event.notification.data && event.notification.data.tmdb_id;
    event.waitUntil(
      fetch(at('/api/taste/rate'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ tmdb_id: tmdbId, verdict }),
      })
        .then(r => r.json())
        .then(res => {
          const n = (res.suggestions || []).length;
          return self.registration.showNotification(
            verdict === 'liked' ? 'Rating saved' : 'Rating saved',
            {
              body: n
                ? `${n} similar film${n === 1 ? '' : 's'} you don't own — tap to review.`
                : 'Noted. Nothing will be added.',
              icon: at('/static/images/ic_launcher_round.png'),
              tag: `taste-done-${tmdbId}`,
              data: { url: n ? `/rate/${tmdbId}` : '/' },
            },
          );
        })
        .catch(err => console.error('[SW] Verdict POST failed:', err)),
    );
    return;
  }

  const relativeUrl = (event.notification.data && event.notification.data.url) || '/';
  const absoluteUrl = new URL(linkPath(relativeUrl), self.location.origin).href;

  event.waitUntil(
    storeDeepLink(relativeUrl)
      .then(() => clients.matchAll({ type: 'window', includeUncontrolled: true }))
      .then(async windowClients => {
      // Try to find an existing app window to reuse
      for (const client of windowClients) {
        if (new URL(client.url).origin === self.location.origin) {
          // Fast path: ask the SPA to route there itself, no reload, state kept.
          // It answers on the port when it has actually done it.
          const acked = new Promise(resolve => {
            const ch = new MessageChannel();
            ch.port1.onmessage = () => resolve(true);
            client.postMessage({ type: 'NOTIFICATION_CLICK', url: relativeUrl }, [ch.port2]);
            setTimeout(() => resolve(false), ACK_TIMEOUT_MS);
          });
          // focus() has to happen promptly while the click still counts as user
          // activation — before we start waiting on anything.
          await client.focus().catch(() => {});
          if (await acked) return;
          // No answer. The page is suspended, or running a build that doesn't
          // know about this message, or the queue never started. Either way the
          // tap must not silently do nothing, so force the window to the link.
          // Costs a reload; landing on the wrong screen costs the whole feature.
          if (client.navigate) {
            try {
              await client.navigate(absoluteUrl);
              return;
            } catch (e) {
              // fall through to openWindow
            }
          }
          return clients.openWindow(absoluteUrl);
        }
      }
      return clients.openWindow(absoluteUrl);
    }).catch(err => {
      console.error('[SW] Notification click error:', err);
      return clients.openWindow(absoluteUrl);
    })
  );
});
