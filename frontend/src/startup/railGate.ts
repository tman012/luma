/** The startup gate for Home's rails. Every rail on a phone is covered, below the fold or not (the page scrolls): its data
 *  has loaded (the rail is rendered) AND the art of the cards that fit its width has decoded.
 *
 *  While the gate is open the splash covers the page and Home is held at opacity 0, so every rail is simply laid out and
 *  loads its art. At the handoff the gate closes: a rail whose art is not decoded stays collapsed (display none, no
 *  reserved box, nothing above it moves) and fades in on its own once it is. The gate toggles two attributes on the rail
 *  elements directly (never React state), so closing it takes effect in the same task as the handoff. No skeletons. */

export const RAIL_PENDING_ATTR = 'data-rail-pending';
export const RAIL_REVEAL_ATTR = 'data-rail-reveal';

interface Entry { el: HTMLElement; done: boolean; started: boolean }

/** Decodes each image (an error counts as done: a broken image never holds a rail back). */
function decodeAll(imgs: HTMLImageElement[]): Promise<void> {
  return Promise.all(imgs.map(i => {
    if (typeof i.decode !== 'function') return Promise.resolve();
    return i.decode().catch(() => {});
  })).then(() => {});
}

export class RailGate {
  private entries = new Map<string, Entry>();
  private waiters = new Set<() => void>();
  private shut: boolean;

  /** `closed`: no splash is covering the page, so a rail is collapsed until its art has decoded. */
  constructor(closed = false) { this.shut = closed; }

  get closed(): boolean { return this.shut; }

  /** A rail's gated art starts loading at once (eager; `high` for the rails in the first screen). Called on every commit:
   *  a rail is started once, and a re-mounted element gets its state again. */
  bind(id: string, el: HTMLElement, imgs: HTMLImageElement[], high = false): void {
    let e = this.entries.get(id);
    if (!e) { e = { el, done: false, started: false }; this.entries.set(id, e); }
    e.el = el;
    if (!e.started) {
      e.started = true;
      for (const i of imgs) {
        i.loading = 'eager';
        if (high) i.setAttribute('fetchpriority', 'high');
      }
      const mine = e;
      void decodeAll(imgs).then(() => this.settle(mine));
    }
    this.apply(e);
  }

  private apply(e: Entry): void {
    if (!this.shut || e.done) e.el.removeAttribute(RAIL_PENDING_ATTR);
    else e.el.setAttribute(RAIL_PENDING_ATTR, '');
  }

  private settle(e: Entry): void {
    if (e.done) return;
    e.done = true;
    const wasPending = e.el.hasAttribute(RAIL_PENDING_ATTR);
    this.apply(e);
    if (wasPending) e.el.setAttribute(RAIL_REVEAL_ATTR, '');
    this.waiters.forEach(w => w());
  }

  /** Rails that have not finished decoding. */
  pending(): string[] {
    return [...this.entries].filter(([, e]) => !e.done).map(([id]) => id);
  }

  /** Resolves when every rail bound so far has decoded (at once with none). The splash's hard cap bounds the wait. */
  whenReady(): Promise<void> {
    return new Promise<void>(resolve => {
      const check = () => {
        if (this.pending().length) return;
        this.waiters.delete(check);
        resolve();
      };
      this.waiters.add(check);
      check();
    });
  }

  /** The handoff: from here a rail not yet decoded is collapsed, and fades in when it is. */
  close(): void {
    this.shut = true;
    this.entries.forEach(e => this.apply(e));
  }
}
