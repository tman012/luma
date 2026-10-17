import { readFileSync } from 'fs';
import { resolve } from 'path';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Runs static/sw.js against a fake service-worker global.
const SRC = readFileSync(resolve(__dirname, '../../static/sw.js'), 'utf8');

type Handler = (e: unknown) => void;
let handlers: Record<string, Handler>;
let stores: Map<string, Map<string, Response>>;
let net: (url: string) => Response;

function boot() {
  handlers = {};
  stores = new Map();
  const caches = {
    open: async (n: string) => {
      if (!stores.has(n)) stores.set(n, new Map());
      const m = stores.get(n)!;
      return { put: async (req: Request | string, r: Response) => { m.set(typeof req === 'string' ? req : req.url, r); },
               addAll: async () => {}, match: async (k: string) => m.get(k) };
    },
    keys: async () => [...stores.keys()],
    delete: async (n: string) => stores.delete(n),
    match: async (req: Request | string) => {
      const k = typeof req === 'string' ? req : req.url;
      for (const m of stores.values()) if (m.has(k)) return m.get(k);
      return undefined;
    },
  };
  const self = {
    registration: { scope: 'http://luma.test/' },
    location: { origin: 'http://luma.test', href: 'http://luma.test/' },
    addEventListener: (t: string, h: Handler) => { handlers[t] = h; },
    skipWaiting: () => {}, clients: { claim: () => {} },
  };
  const fetchFn = vi.fn(async (req: Request | string) => net(typeof req === 'string' ? req : req.url));
  new Function('self', 'caches', 'fetch', 'console', SRC)(self, caches, fetchFn, { log: () => {} });
}

async function get(url: string, mode = 'cors') {
  let responded: Promise<Response> | null = null;
  handlers.fetch!({ request: new Request(url, { method: 'GET' }) as Request & { mode: string }, respondWith: (p: Promise<Response>) => { responded = p; } });
  void mode;
  if (responded) await responded;
  await new Promise((r) => setTimeout(r, 0));
  return responded !== null;
}

const allKeys = () => [...stores.values()].flatMap((m) => [...m.keys()]);

describe('service worker never keeps signed-in images', () => {
  beforeEach(() => {
    net = () => new Response('img', { status: 200, headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'private, max-age=86400' } });
    boot();
  });

  it.each(['/api/img/plex?url=/library/metadata/1/thumb/2', '/api/img/tmdb?path=/a.jpg&size=w500',
           '/static/hero_cache/movie_1_poster.jpg'])('leaves %s to the browser (no SW cache)', async (p) => {
    const handled = await get(`http://luma.test${p}`);
    expect(handled).toBe(false);
    expect(allKeys()).toEqual([]);
  });

  it('does not keep no-store or private replies from /static either', async () => {
    net = () => new Response('x', { status: 200, headers: { 'Cache-Control': 'private, no-store' } });
    await get('http://luma.test/static/js/a.js');
    net = () => new Response('x', { status: 200, headers: { 'Cache-Control': 'private, max-age=60' } });
    await get('http://luma.test/static/images/b.png');
    expect(allKeys()).toEqual([]);
  });

  it('still keeps public static files and hashed assets', async () => {
    net = () => new Response('x', { status: 200, headers: { 'Cache-Control': 'public, max-age=300' } });
    await get('http://luma.test/static/images/logo.png');
    await get('http://luma.test/assets/index-abc.js');
    expect(allKeys().sort()).toEqual(['http://luma.test/assets/index-abc.js', 'http://luma.test/static/images/logo.png']);
  });

  it('CLEAR_ALL_CACHES empties every cache', async () => {
    net = () => new Response('x', { status: 200, headers: { 'Cache-Control': 'public, max-age=300' } });
    await get('http://luma.test/static/images/logo.png');
    expect(stores.size).toBeGreaterThan(0);
    let wait: Promise<unknown> = Promise.resolve();
    handlers.message!({ data: 'CLEAR_ALL_CACHES', waitUntil: (p: Promise<unknown>) => { wait = p; } });
    await wait;
    expect(stores.size).toBe(0);
  });
});
