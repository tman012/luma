/** The readiness gate for Home's first screen: the data has loaded AND the artwork above the fold has decoded.
 *  Nothing shows before both (the splash covers the wait); a hard cap hands Home over with whatever loaded. */
export type GateReason = 'ready' | 'timeout' | 'error';
export interface GateResult<T> { data: T | null; reason: GateReason }

export interface GateDeps<T> {
  load: () => Promise<T>;
  /** The artwork URLs of the first screen, from the loaded data. */
  urls: (data: T) => string[];
  decode: (urls: string[], capMs: number) => Promise<void>;
  /** The longest the data may take (from the gate's start). */
  capMs: number;
  /** The longest the artwork may take after the data arrived (also never past the cap). Infinity: only the cap. */
  holdMs: number;
  now?: () => number;
}

export function runHomeGate<T>(deps: GateDeps<T>): { promise: Promise<GateResult<T>>; cancel: () => void } {
  const now = deps.now ?? Date.now;
  const t0 = now();
  let settled = false;
  let timer = 0;
  let resolve!: (r: GateResult<T>) => void;
  const promise = new Promise<GateResult<T>>(r => { resolve = r; });
  const settle = (r: GateResult<T>) => { if (settled) return; settled = true; window.clearTimeout(timer); resolve(r); };
  let data = false;
  timer = window.setTimeout(() => { if (!data) settle({ data: null, reason: 'timeout' }); }, deps.capMs);
  deps.load().then(d => {
    if (settled) return;
    data = true;
    window.clearTimeout(timer);
    const left = Math.max(0, Math.min(deps.holdMs, deps.capMs - (now() - t0)));
    void deps.decode(deps.urls(d), left).then(() => settle({ data: d, reason: 'ready' }), () => settle({ data: d, reason: 'ready' }));
  }, () => { if (!settled && !data) settle({ data: null, reason: 'error' }); });
  return { promise, cancel: () => { settled = true; window.clearTimeout(timer); } };
}
