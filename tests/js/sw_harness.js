// Runs static/sw.js with a stub service-worker global and reports what it did.
// Usage: node sw_harness.js <sw.js> <scope> <request-url>...
const fs = require('fs');
const vm = require('vm');

const [swPath, scope, ...urls] = process.argv.slice(2);
const origin = new URL(scope).origin;
const listeners = {};
const added = [];
const sandbox = {
  URL, Response: class { constructor(body, init) { this.body = body; this.init = init; } },
  MessageChannel: class {}, console: { log() {}, error() {} },
  setTimeout, Promise, JSON, Date, Array, Set,
  fetch: () => Promise.resolve({ ok: true, clone() { return this; } }),
  caches: {
    keys: () => Promise.resolve([]),
    delete: () => Promise.resolve(true),
    open: () => Promise.resolve({ addAll: (u) => { added.push(...u); return Promise.resolve(); },
                                  put: () => Promise.resolve() }),
    match: () => Promise.resolve(undefined),
  },
  clients: { claim() {}, matchAll: () => Promise.resolve([]) },
};
sandbox.self = {
  registration: { scope, showNotification: () => Promise.resolve() },
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
  const handled = {};
  for (const u of urls) {
    let responded = false;
    listeners.fetch({
      request: { url: u, method: 'GET', mode: u.endsWith('#nav') ? 'navigate' : 'cors' },
      respondWith: () => { responded = true; },
    });
    handled[u] = responded;
  }
  process.stdout.write(JSON.stringify({ shell: added, handled }));
})();
