// Runs static/sw.js with a stub service-worker global and reports what it did.
// Usage: [SW_PUSH='{...}'] node sw_harness.js <sw.js> <scope> <request-url>...
const fs = require('fs');
const vm = require('vm');

const [swPath, scope, ...urls] = process.argv.slice(2);
const origin = new URL(scope).origin;
const listeners = {};
const added = [];
const notes = [];
const fetched = [];
const puts = [];
const deleted = [];
const responses = [];
// SW_CACHES: cache names present (for message handlers that clear them).
const cacheNames = process.env.SW_CACHES ? JSON.parse(process.env.SW_CACHES) : [];
// SW_SHELL_RESP: the response the page fetch returns ({ok, redirected, type, url, headers}).
const shellSpec = process.env.SW_SHELL_RESP ? JSON.parse(process.env.SW_SHELL_RESP) : null;
function fakeResponse(spec, url) {
  const s = spec || { ok: true };
  const headers = s.headers || {};
  return {
    ok: s.ok !== false, redirected: !!s.redirected, type: s.type || 'basic', url: s.url || url,
    headers: { get: (k) => (k in headers ? headers[k] : null) },
    clone() { return this; },
  };
}
const sandbox = {
  URL, Response: class { constructor(body, init) { this.body = body; this.init = init; } },
  MessageChannel: class {}, console: { log() {}, error() {} },
  setTimeout, Promise, JSON, Date, Array, Set,
  fetch: (url, opts) => { fetched.push({ url: String(url), redirect: opts && opts.redirect });
                          return Promise.resolve(fakeResponse(shellSpec, String(url))); },
  caches: {
    keys: () => Promise.resolve(cacheNames.slice()),
    delete: (n) => { deleted.push(String(n)); return Promise.resolve(true); },
    open: () => Promise.resolve({ addAll: (u) => { added.push(...u); return Promise.resolve(); },
                                  put: (k) => { puts.push(typeof k === "object" && k && k.url ? String(k.url) : String(k)); return Promise.resolve(); } }),
    match: () => Promise.resolve(undefined),
  },
  clients: { claim() {}, matchAll: () => Promise.resolve([]) },
};
sandbox.self = {
  registration: { scope, showNotification: (title, opts) => { notes.push(opts); return Promise.resolve(); } },
  location: { origin },
  addEventListener: (type, fn) => { listeners[type] = fn; },
  skipWaiting() {}, clients: sandbox.clients,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(swPath, 'utf8'), sandbox);

(async () => {
  let installDone;
  listeners.install({ waitUntil: (p) => { installDone = p; } });
  await installDone;
  await new Promise((r) => setTimeout(r, 0));
  const handled = {};
  for (const u of urls) {
    let responded = false;
    listeners.fetch({
      request: { url: u, method: 'GET', mode: u.endsWith('#nav') ? 'navigate' : 'cors' },
      respondWith: (p) => { responded = true; responses.push(Promise.resolve(p).catch(() => null)); },
    });
    handled[u] = responded;
  }
  await Promise.all(responses);
  await new Promise((r) => setTimeout(r, 0));
  if (process.env.SW_MESSAGE) {
    let msgDone = Promise.resolve();
    listeners.message({ data: process.env.SW_MESSAGE, waitUntil: (p) => { msgDone = p; } });
    await msgDone;
  }
  if (process.env.SW_PUSH) {
    const payload = JSON.parse(process.env.SW_PUSH);
    let pushed;
    listeners.push({ data: { json: () => payload }, waitUntil: (p) => { pushed = p; } });
    await pushed;
  }
  process.stdout.write(JSON.stringify({ shell: added, handled, notes, fetched, puts, deleted }));
})();
