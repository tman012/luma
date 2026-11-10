/** Main-thread scheduling for work that must not share a task with the user's tap or an animating frame: every helper
 *  ends the current task and continues in a new one, so no single task holds the page for more than one piece of work.
 *  No timing literal: nothing here waits a set time, it only hands the thread back. */

const waiting: Array<() => void> = [];
let channel: MessageChannel | null | undefined;

/** Created on first use so importing this module never opens a port (a bare port keeps a Node process alive). */
function getChannel(): MessageChannel | null {
  if (channel !== undefined) return channel;
  if (typeof MessageChannel === 'undefined') return (channel = null);
  const c = new MessageChannel();
  c.port1.onmessage = () => { const run = waiting.splice(0); for (const fn of run) fn(); };
  (c.port1 as unknown as { unref?: () => void }).unref?.();
  return (channel = c);
}

/** Resolves in a later task (before the next timer, with rendering free to happen in between). */
export function yieldToMain(): Promise<void> {
  const sched = (globalThis as { scheduler?: { yield?: () => Promise<void> } }).scheduler;
  if (sched?.yield) return sched.yield();
  return new Promise<void>((resolve) => {
    const c = getChannel();
    if (!c) { setTimeout(resolve, 0); return; }
    waiting.push(resolve);
    c.port2.postMessage(0);
  });
}

/** Runs `fn` in a later task, after the frame that is being produced now has been presented. Returns a cancel. */
export function afterNextFrame(fn: () => void): () => void {
  let cancelled = false;
  let frame = 0;
  frame = requestAnimationFrame(() => {
    void yieldToMain().then(() => { if (!cancelled) fn(); });
  });
  return () => { cancelled = true; cancelAnimationFrame(frame); };
}
