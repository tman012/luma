/** cw-logo: reuse of the app's image loader for a decode a surface needs: when the loader already has the URL (the
 *  tap started it, or a warm-up queued it), that load is promoted to high priority and awaited, never duplicated. */
import { hasLoad, isDecoded, loadImage } from '@/loading/imageLoader';

/** The loader's result for `url` if it has a load for it (true when decoded), else null (no load to reuse). */
export function reuseLoad(url: string): Promise<boolean> | null {
  if (!hasLoad(url)) return null;
  return isDecoded(url) ? Promise.resolve(true) : loadImage(url, 'high').then(() => isDecoded(url));
}
