/** The startup splash (index.html paints it before any script runs). This module owns what happens next: hold it until
 *  the first screen is ready, then hand it off, once per page load. A background refresh never shows it again: there is
 *  no way back from 'done'. Transform and opacity only. */
import { SPLASH_MOTION, reducedMotion } from '@/theme/motion';

export type SplashPhase = 'showing' | 'handoff' | 'done';
export type SplashReason = 'ready' | 'cap';

export interface ReleaseOptions {
  /** The images on the first screen. Each is decoded before the handoff (the caller makes them eager). */
  images?: () => HTMLImageElement[];
  /** Home's own logo: the splash logo flies into it. Without one (or when it has no size) the splash just crossfades. */
  target?: () => HTMLElement | null;
}

export interface SplashStats {
  armedAt: number;
  releasedAt?: number;
  handoffAt?: number;
  doneAt?: number;
  reason?: SplashReason;
  mode?: 'flip' | 'fade' | 'none';
}

interface Env {
  doc: Document;
  now: () => number;
  reduced: () => boolean;
  capMs: number;
}

const ART_ATTR = 'data-splash-art';
const FLIP_ATTR = 'data-splash-flip';

/** The artwork's box inside a target image: `data-splash-art="x y w h naturalW naturalH"` names the crop of the
 *  image that is the logo (Home's phone logo carries transparent padding); without it the whole box is. */
export function artRect(el: HTMLElement): { x: number; y: number; w: number; h: number } {
  const r = el.getBoundingClientRect();
  const a = el.getAttribute(ART_ATTR)?.split(/\s+/).map(Number);
  if (!a || a.length !== 6 || a.some(n => !Number.isFinite(n)) || !a[4] || !a[5]) return { x: r.left, y: r.top, w: r.width, h: r.height };
  const [cx, cy, cw, ch, nw, nh] = a as [number, number, number, number, number, number];
  const sx = r.width / nw, sy = r.height / nh;
  return { x: r.left + cx * sx, y: r.top + cy * sy, w: cw * sx, h: ch * sy };
}

/** Transform that moves the splash logo's box onto the target's: both scale about their centers. */
export function flipTransform(from: { left: number; top: number; width: number; height: number }, to: { x: number; y: number; w: number; h: number }): string {
  const dx = to.x + to.w / 2 - (from.left + from.width / 2);
  const dy = to.y + to.h / 2 - (from.top + from.height / 2);
  const s = from.width > 0 ? to.w / from.width : 1;
  return `translate(${dx}px, ${dy}px) scale(${s})`;
}

function wait(ms: number): Promise<void> {
  return new Promise<void>((done) => { setTimeout(done, ms); });
}

export class Splash {
  phase: SplashPhase = 'showing';
  readonly stats: SplashStats;
  private opts: ReleaseOptions = {};
  private released = false;
  private capTimer: ReturnType<typeof setTimeout> | null = null;
  private resolveDone!: () => void;
  readonly finished: Promise<void> = new Promise<void>((done) => { this.resolveDone = done; });

  constructor(private env: Env) {
    this.stats = { armedAt: env.now() };
  }

  private el<T extends HTMLElement>(id: string): T | null { return this.env.doc.getElementById(id) as T | null; }

  /** True while the splash is on screen (or leaving it). Without a splash element in the page it is never active. */
  active(): boolean {
    if (this.phase === 'done') return false;
    if (!this.el('splash')) { this.finish('none'); return false; }
    return true;
  }

  /** Milliseconds left of the hard cap (Infinity once the splash is gone). */
  remainingMs(): number {
    return this.active() ? Math.max(0, this.env.capMs - this.env.now()) : Infinity;
  }

  /** Starts the hard cap: whatever has loaded is shown by then, even if nothing ever calls release(). */
  arm(): void {
    if (this.capTimer != null || this.phase !== 'showing' || !this.active()) return;
    this.capTimer = setTimeout(() => { void this.go('cap'); }, Math.max(0, this.env.capMs - this.env.now()));
  }

  /** The first screen is on the page: hand the splash off once its images are decoded and its intro has played. */
  release(opts: ReleaseOptions = {}): Promise<void> {
    if (!this.active()) return this.finished;
    this.opts = { ...this.opts, ...opts };
    if (this.released) return this.finished;
    this.released = true;
    this.stats.releasedAt = this.env.now();
    this.arm();
    void Promise.all([this.decodeImages(), this.introDone()]).then(() => this.go('ready'), () => this.go('ready'));
    return this.finished;
  }

  private decodeImages(): Promise<void> {
    let imgs: HTMLImageElement[] = [];
    try { imgs = this.opts.images?.() ?? []; } catch { imgs = []; }
    const target = this.opts.target?.();
    if (target instanceof HTMLImageElement && !imgs.includes(target)) imgs = [...imgs, target];
    return Promise.all(imgs.map(i => (typeof i.decode === 'function' ? i.decode() : Promise.resolve()).catch(() => {}))).then(() => {});
  }

  /** Resolves when the logo's intro has finished (the minimum the splash stays up). */
  private introDone(): Promise<void> {
    const logo = this.el('splash-logo');
    const anims = typeof logo?.getAnimations === 'function' ? logo.getAnimations() : [];
    if (anims.length) return Promise.all(anims.map(a => a.finished.catch(() => {}))).then(() => {});
    const ms = this.env.reduced() ? SPLASH_MOTION.reducedIntroMs : SPLASH_MOTION.introMs;
    const marks = (this.env.doc.defaultView as (Window & { __lumaSplash?: { introStart?: number } }) | null)?.__lumaSplash;
    const start = marks?.introStart ?? this.stats.armedAt;
    return wait(Math.max(0, start + ms - this.env.now()));
  }

  private async go(reason: SplashReason): Promise<void> {
    if (this.phase !== 'showing') return;
    this.phase = 'handoff';
    if (this.capTimer != null) { clearTimeout(this.capTimer); this.capTimer = null; }
    this.stats.reason = reason;
    this.stats.handoffAt = this.env.now();
    const doc = this.env.doc;
    const splash = this.el('splash');
    if (!splash) { this.finish('none'); return; }
    // Taps reach Home at once; nothing here waits on the animation.
    splash.style.pointerEvents = 'none';
    const logo = this.el('splash-logo');
    const bg = this.el('splash-bg');
    let mode: SplashStats['mode'] = 'fade';
    try {
      if (typeof splash.animate !== 'function') { this.finish('none'); return; }
      if (logo) { logo.style.animation = 'none'; logo.style.opacity = '1'; }
      const target = this.env.reduced() ? null : this.opts.target?.() ?? null;
      const from = logo?.getBoundingClientRect();
      const to = target ? artRect(target) : null;
      const ms = SPLASH_MOTION.handoffMs;
      if (logo && bg && from && to && to.w > 0 && to.h > 0 && from.width > 0) {
        mode = 'flip';
        doc.documentElement.setAttribute(FLIP_ATTR, '');
        const moves = [
          logo.animate([{ transform: 'translate(0px, 0px) scale(1)' }, { transform: flipTransform(from, to) }], { duration: ms, easing: SPLASH_MOTION.handoffEase, fill: 'forwards' }),
          bg.animate([{ opacity: 1 }, { opacity: 0 }], { duration: ms, easing: SPLASH_MOTION.handoffEase, fill: 'forwards' }),
        ];
        await Promise.all(moves.map(a => a.finished.catch(() => {})));
      } else {
        const a = splash.animate([{ opacity: 1 }, { opacity: 0 }], { duration: this.env.reduced() ? SPLASH_MOTION.reducedHandoffMs : SPLASH_MOTION.handoffMs, easing: SPLASH_MOTION.handoffEase, fill: 'forwards' });
        await a.finished.catch(() => {});
      }
    } catch {
      mode = 'none';
    }
    this.finish(mode);
  }

  /** Removes the splash and shows Home's own logo in the same task, so no frame has neither or both. */
  private finish(mode: SplashStats['mode']): void {
    if (this.phase === 'done') return;
    if (this.capTimer != null) { clearTimeout(this.capTimer); this.capTimer = null; }
    this.phase = 'done';
    this.stats.mode = mode;
    this.stats.doneAt = this.env.now();
    this.env.doc.documentElement.removeAttribute(FLIP_ATTR);
    this.env.doc.getElementById('splash')?.remove();
    this.resolveDone();
  }
}

export function createSplash(env: Partial<Env> = {}): Splash {
  return new Splash({
    doc: env.doc ?? document,
    now: env.now ?? (() => performance.now()),
    reduced: env.reduced ?? reducedMotion,
    capMs: env.capMs ?? SPLASH_MOTION.capMs,
  });
}

let instance: Splash | null = null;
export function splash(): Splash {
  if (!instance) {
    instance = createSplash();
    if (typeof window !== 'undefined') (window as Window & { __lumaSplashStats?: SplashStats }).__lumaSplashStats = instance.stats;
  }
  return instance;
}
/** Tests only: a fresh splash for the next page. */
export function resetSplash(next: Splash | null = null): void { instance = next; }

/** Starts the hard cap. Called once, before the app renders. */
export function armSplash(): void { splash().arm(); }
/** The first screen is ready (or there is nothing to wait for): hand the splash off. */
export function releaseSplash(opts?: ReleaseOptions): Promise<void> { return splash().release(opts); }
export function splashActive(): boolean { return splash().active(); }
export function splashRemainingMs(): number { return splash().remainingMs(); }
