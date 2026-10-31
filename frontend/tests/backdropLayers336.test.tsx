/** BackdropHero upgrade layers: a new upgrade decodes first and fades over the layer on screen (never a cut to the
 *  seed); the old layer stays until the fade lands; better art stays once shown; stale decodes are ignored. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act, cleanup, fireEvent } from '@testing-library/react';
import { BackdropHero } from '@/components/media/BackdropHero';
import type { BackdropEffects } from '@/hooks/useBackdropEffects';

const decodes: Record<string, () => void> = {};
class FakeImage { private u = ''; onload: unknown; onerror: unknown;
  set src(v: string) { this.u = v; } get src() { return this.u; }
  decode() { const u = this.u; return new Promise<void>(r => { decodes[u] = r; }); } }
let anims: { el: Element; a: { onfinish: null | (() => void) } }[] = [];
beforeEach(() => {
  for (const k of Object.keys(decodes)) delete decodes[k];
  anims = [];
  vi.stubGlobal('Image', FakeImage);
  (Element.prototype as unknown as { animate: unknown }).animate = function (this: Element) { const a = { onfinish: null }; anims.push({ el: this, a }); return a; };
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const fx = { headerImgStyle: {}, headerContainerMask: {} } as unknown as BackdropEffects;
const hero = (up: string | null) => <BackdropHero backdropUrl="/seed.jpg" posterUrl={null} effects={fx} phase="visible" instant upgradeUrl={up} />;
const sharp = (c: HTMLElement) => [...c.querySelectorAll('img[data-backdrop-sharp]')].map(i => i.getAttribute('src'));
const decode = async (u: string) => { await act(async () => { decodes[u]!(); await Promise.resolve(); await Promise.resolve(); }); };

describe('BackdropHero upgrade layers', () => {
  it('Plex art -> text-free: the art stays until the new one decodes and has faded in; no cut to the seed', async () => {
    const r = render(hero('/plex-art.jpg'));
    fireEvent.load(r.container.querySelector('img')!);                     // seed loaded
    await decode('/plex-art.jpg');
    expect(sharp(r.container)).toEqual(['/plex-art.jpg']);
    act(() => anims.at(-1)!.a.onfinish?.());
    r.rerender(hero('/textless.jpg'));                                     // /api/tv lands
    expect(sharp(r.container)).toEqual(['/plex-art.jpg']);                 // still the art, not the seed
    await decode('/textless.jpg');
    expect(sharp(r.container)).toEqual(['/plex-art.jpg', '/textless.jpg']); // new layer over the art while it fades
    const top = anims.at(-1)!;
    expect(top.el.getAttribute('src')).toBe('/textless.jpg');
    act(() => top.a.onfinish?.());
    expect(sharp(r.container)).toEqual(['/textless.jpg']);
  });

  it('better art stays when upgradeUrl goes away', async () => {
    const r = render(hero('/plex-art.jpg'));
    fireEvent.load(r.container.querySelector('img')!);
    await decode('/plex-art.jpg');
    r.rerender(hero(null));
    expect(sharp(r.container)).toEqual(['/plex-art.jpg']);
  });

  it('a decode that finishes after the URL changed is ignored', async () => {
    const r = render(hero('/a.jpg'));
    fireEvent.load(r.container.querySelector('img')!);
    r.rerender(hero('/b.jpg'));
    await decode('/a.jpg');
    expect(sharp(r.container)).toEqual([]);
    await decode('/b.jpg');
    expect(sharp(r.container)).toEqual(['/b.jpg']);
  });

  it('a new title (different first art) starts with no upgrade layers', async () => {
    const r = render(hero('/plex-art.jpg'));
    fireEvent.load(r.container.querySelector('img')!);
    await decode('/plex-art.jpg');
    r.rerender(<BackdropHero backdropUrl="/other-seed.jpg" posterUrl={null} effects={fx} phase="visible" instant upgradeUrl={null} />);
    expect(sharp(r.container)).toEqual([]);
  });
});
