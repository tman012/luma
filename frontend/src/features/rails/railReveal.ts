/**
 * Synchronized rail reveal: pure logic, no React.
 *
 * Every rail on a page appears at one single moment. Failed fetches retry in
 * the background while skeletons stay up. A hard cap reveals whatever has
 * succeeded; anything still missing is left out until the next full load or
 * refresh, and late results are ignored so a rail never fills in on its own.
 */

export const REVEAL_CAP_MS = 15_000;
export const RETRY_DELAYS_MS: readonly number[] = [800, 1600, 3200, 5000];

export interface RevealResult<TId extends string, TItem> {
  /** Rails that succeeded. `null` means the fetcher reported "disabled". */
  ok: Map<TId, TItem[] | null>;
  /** Rails that never succeeded before the cap. */
  failed: TId[];
  timedOut: boolean;
}

export interface Timers {
  set: (fn: () => void, ms: number) => unknown;
  clear: (handle: unknown) => void;
}

const defaultTimers: Timers = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
};

export interface RevealGateOptions<TId extends string, TItem> {
  ids: readonly TId[];
  /** The signal aborts when the gate reveals (cap) or is cancelled. */
  fetch: (id: TId, signal: AbortSignal) => Promise<TItem[] | null>;
  capMs?: number;
  retryDelaysMs?: readonly number[];
  timers?: Timers;
}

export interface RevealGate<TId extends string, TItem> {
  done: Promise<RevealResult<TId, TItem>>;
  cancel: () => void;
}

export function runRevealGate<TId extends string, TItem>(
  opts: RevealGateOptions<TId, TItem>,
): RevealGate<TId, TItem> {
  const capMs = opts.capMs ?? REVEAL_CAP_MS;
  const delays = opts.retryDelaysMs ?? RETRY_DELAYS_MS;
  const timers = opts.timers ?? defaultTimers;
  const ok = new Map<TId, TItem[] | null>();
  const pending = new Set<unknown>();
  let finished = false;
  const controller = new AbortController();
  let resolveDone!: (r: RevealResult<TId, TItem>) => void;
  const done = new Promise<RevealResult<TId, TItem>>((r) => { resolveDone = r; });

  const finish = (timedOut: boolean) => {
    if (finished) return;
    finished = true;
    for (const h of pending) timers.clear(h);
    pending.clear();
    controller.abort(); // stragglers can never land after the reveal
    resolveDone({
      ok: new Map(ok),
      failed: opts.ids.filter((id) => !ok.has(id)),
      timedOut,
    });
  };

  const attempt = (id: TId, n: number) => {
    if (finished) return;
    let p: Promise<TItem[] | null>;
    try {
      p = opts.fetch(id, controller.signal);
    } catch (e) {
      p = Promise.reject(e);
    }
    p.then(
      (items) => {
        if (finished) return;
        ok.set(id, items);
        if (ok.size === opts.ids.length) finish(false);
      },
      () => {
        if (finished) return;
        const delay = delays[Math.min(n, delays.length - 1)] ?? 1000;
        const h = timers.set(() => {
          pending.delete(h);
          attempt(id, n + 1);
        }, delay);
        pending.add(h);
      },
    );
  };

  if (opts.ids.length === 0) {
    finish(false);
  } else {
    const cap = timers.set(() => finish(true), capMs);
    pending.add(cap);
    for (const id of opts.ids) attempt(id, 0);
  }

  return {
    done,
    cancel: () => {
      if (finished) return;
      finished = true;
      for (const h of pending) timers.clear(h);
      pending.clear();
      controller.abort();
    },
  };
}

// ---------------------------------------------------------------------------
// State shaping
// ---------------------------------------------------------------------------

export interface RevealRailDef<TId extends string> {
  id: TId;
  title: string;
  conditional: boolean;
}

export interface RevealRailState<TItem, TId extends string> {
  id: TId;
  title: string;
  items: TItem[];
  isLoading: boolean;
  /** True on the render where rails replace skeletons (one shared fade). */
  fadeIn: boolean;
}

/** Skeleton rails: always-on rails plus conditional ones seen last time. */
export function skeletonRails<TItem, TId extends string>(
  defs: readonly RevealRailDef<TId>[],
  lastLayout: ReadonlySet<TId> | null,
): RevealRailState<TItem, TId>[] {
  return defs
    .filter((d) => !d.conditional || (lastLayout?.has(d.id) ?? false))
    .map((d) => ({ id: d.id, title: d.title, items: [], isLoading: true, fadeIn: false }));
}

/** Final rails in definition order. Failed and empty conditional rails are left out. */
export function revealedRails<TItem, TId extends string>(
  defs: readonly RevealRailDef<TId>[],
  ok: ReadonlyMap<TId, TItem[] | null>,
  fadeIn: boolean,
): RevealRailState<TItem, TId>[] {
  const out: RevealRailState<TItem, TId>[] = [];
  for (const d of defs) {
    if (!ok.has(d.id)) continue;
    const items = ok.get(d.id);
    if (items == null) continue;
    if (d.conditional && items.length === 0) continue;
    out.push({ id: d.id, title: d.title, items, isLoading: false, fadeIn });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Storage scope: per user and per URL base, cleared on logout
// ---------------------------------------------------------------------------

export const RAIL_STORAGE_ROOT = 'luma.rails.';

export function railStorageKey(urlBase: string, userId: number | string | null, key: string): string {
  return `${RAIL_STORAGE_ROOT}${encodeURIComponent(urlBase || '/')}|${userId ?? 'anon'}|${key}`;
}

/** Remove every rail snapshot and layout (all users, all bases). */
export function clearRailStorage(...stores: Array<StorageLike & { length: number; key(i: number): string | null } | null>): void {
  for (const store of stores) {
    if (!store) continue;
    try {
      const doomed: string[] = [];
      for (let i = 0; i < store.length; i++) {
        const k = store.key(i);
        if (k && k.startsWith(RAIL_STORAGE_ROOT)) doomed.push(k);
      }
      for (const k of doomed) store.removeItem(k);
    } catch {
      // Storage unavailable.
    }
  }
}

// ---------------------------------------------------------------------------
// Snapshot cache (all rails together, so a cached page is also all-or-nothing)
// ---------------------------------------------------------------------------

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

interface Snapshot<TId extends string, TItem> {
  ts: number;
  rails: Array<[TId, TItem[]]>;
}

export function loadSnapshot<TId extends string, TItem>(
  storage: StorageLike | null,
  key: string,
  ttlMs: number,
  now: number = Date.now(),
): Map<TId, TItem[]> | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(key);
    if (!raw) return null;
    const snap = JSON.parse(raw) as Snapshot<TId, TItem>;
    if (!snap || !Array.isArray(snap.rails) || now - snap.ts > ttlMs) {
      storage.removeItem(key);
      return null;
    }
    return new Map(snap.rails);
  } catch {
    return null;
  }
}

export function saveSnapshot<TId extends string, TItem>(
  storage: StorageLike | null,
  key: string,
  rails: readonly RevealRailState<TItem, TId>[],
  now: number = Date.now(),
): void {
  if (!storage) return;
  try {
    const snap: Snapshot<TId, TItem> = { ts: now, rails: rails.map((r) => [r.id, r.items]) };
    storage.setItem(key, JSON.stringify(snap));
  } catch {
    // Storage full or unavailable.
  }
}

export function loadLayout<TId extends string>(storage: StorageLike | null, key: string): Set<TId> | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(key);
    if (!raw) return null;
    const ids = JSON.parse(raw) as TId[];
    return Array.isArray(ids) ? new Set(ids) : null;
  } catch {
    return null;
  }
}

export function saveLayout<TId extends string>(
  storage: StorageLike | null,
  key: string,
  rails: readonly { id: TId }[],
): void {
  if (!storage) return;
  try {
    storage.setItem(key, JSON.stringify(rails.map((r) => r.id)));
  } catch {
    // ignore
  }
}

/** Resolve with the promise's value, or with `fallback` after `ms`. */
export function withDeadline<T>(p: Promise<T>, ms: number, fallback: T, timers: Timers = defaultTimers): Promise<T> {
  return new Promise<T>((resolve) => {
    const h = timers.set(() => resolve(fallback), ms);
    p.then(
      (v) => { timers.clear(h); resolve(v); },
      () => { timers.clear(h); resolve(fallback); },
    );
  });
}
