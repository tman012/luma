/** Instant color blur from the poster already on screen: sample its four
 * quadrants on a tiny canvas. Posters come through our same-origin proxies
 * (/api/img/plex, /api/img/tmdb), so the canvas isn't tainted; anything that
 * fails returns null and the caller falls back to the fetched palette. */
import type { UltraBlurColors } from '@/utils/modalParsers';

const NAVY: [number, number, number] = [0x15, 0x10, 0x2a];

function rgb([r, g, b]: [number, number, number]): string {
  return `rgb(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)})`;
}

export function colorsFromImage(img: HTMLImageElement | null | undefined): UltraBlurColors | null {
  if (!img || !img.complete || !img.naturalWidth) return null;
  try {
    const c = document.createElement('canvas');
    c.width = 2; c.height = 2;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    if (!ctx) return null;
    ctx.drawImage(img, 0, 0, 2, 2); // each pixel averages one quadrant
    const d = ctx.getImageData(0, 0, 2, 2).data;
    const px = (i: number): [number, number, number] => [d[i * 4]!, d[i * 4 + 1]!, d[i * 4 + 2]!];
    return { topLeft: rgb(px(0)), topRight: rgb(px(1)), bottomLeft: rgb(px(2)), bottomRight: rgb(px(3)) } as UltraBlurColors;
  } catch {
    return null; // tainted or unsupported
  }
}

function parse(color: string): [number, number, number] | null {
  const m = /rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)/i.exec(color);
  if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
  const h = color.replace('#', '');
  if (/^[0-9a-f]{6}$/i.test(h)) return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  return null;
}

/** The sheet's own background, derived from the palette (a deep tint of its
 * average), so the first paint is the color blur, never plain navy. */
export function sheetBase(colors: UltraBlurColors | null | undefined): string | null {
  if (!colors) return null;
  const list = [colors.topLeft, colors.topRight, colors.bottomLeft, colors.bottomRight]
    .filter((x): x is string => typeof x === 'string').map(parse).filter((x): x is [number, number, number] => !!x);
  if (!list.length) return null;
  const avg = list.reduce((a, c) => [a[0] + c[0], a[1] + c[1], a[2] + c[2]], [0, 0, 0]).map(v => v / list.length);
  const mix = 0.42; // enough color to read as the blur, dark enough for text
  return rgb([0, 1, 2].map(i => NAVY[i]! * (1 - mix) + avg[i]! * mix) as [number, number, number]);
}
