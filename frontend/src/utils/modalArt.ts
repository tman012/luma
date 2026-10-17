// ---------------------------------------------------------------------------
// Detail-modal art reveal. The backdrop and clearlogo stay hidden (navy shows)
// until the final URLs are known and both images have decoded; then they fade
// in together. Pure helpers here so they can be tested without a browser.
// ---------------------------------------------------------------------------

export const ART_PLACEHOLDER_BG = '#15102a';
export const ART_FADE_TRANSITION = 'opacity 0.6s ease-out';
/** After the final URLs are known, show the art anyway if decoding takes longer. */
export const ART_MAX_WAIT_MS = 8000;

export interface ArtState {
  /** True once the hook has settled on the final backdrop and logo URLs. */
  final: boolean;
  backdropUrl: string | null;
  logoUrl: string | null;
  /** url -> decoded (true) or failed (false). */
  settled: Record<string, boolean>;
  timedOut: boolean;
}

export function artReady(s: ArtState): boolean {
  if (!s.final) return false;
  if (s.timedOut) return true;
  return [s.backdropUrl, s.logoUrl].every((u) => !u || u in s.settled);
}

export function artFadeStyle(ready: boolean, shownOpacity = 1): { opacity: number; transition: string } {
  return { opacity: ready ? shownOpacity : 0, transition: ART_FADE_TRANSITION };
}

interface PreloadImage {
  onload: unknown;
  onerror: unknown;
  src: string;
  decode?: () => Promise<void>;
}

/** Resolves true once the image has loaded and decoded, false if it fails. */
export function preloadImage(url: string, ImageCtor: new () => PreloadImage): Promise<boolean> {
  return new Promise((resolve) => {
    const img = new ImageCtor();
    let done = false;
    const finish = (ok: boolean) => {
      if (!done) {
        done = true;
        resolve(ok);
      }
    };
    img.onload = () => {
      if (typeof img.decode === 'function') img.decode().then(() => finish(true), () => finish(true));
      else finish(true);
    };
    img.onerror = () => finish(false);
    img.src = url;
  });
}

/** Movie modal: is the backdrop already final after the first detail fetch?
 *  Not when only Plex data came back but Plex reported a TMDb id: the TMDb
 *  backdrop is still on its way and would replace the Plex one. */
export function movieBackdropFinalAfterDetail(opts: {
  tmdbIdProp: boolean;
  tmdbMerged: boolean;
  parsedTmdbId: boolean;
}): boolean {
  return opts.tmdbMerged || opts.tmdbIdProp || !opts.parsedTmdbId;
}