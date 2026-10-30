import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { BackdropHero } from '@/components/media/BackdropHero';
import type { BackdropEffects } from '@/hooks/useBackdropEffects';

const effects = { headerContainerMask: {}, headerImgStyle: { width: '100%' } } as unknown as BackdropEffects;
const hero = (url: string) => <BackdropHero backdropUrl={url} effects={effects} phase="visible" />;

describe('details popup backdrop', () => {
  afterEach(cleanup);

  it('stays navy until the right image loads, then fades it in; never shows the previous one', () => {
    const view = render(hero('/api/img/plex?url=/library/metadata/1/art/1'));
    let img = view.container.querySelector('img')!;
    expect(img.style.opacity).toBe('0');
    expect((view.container.firstChild as HTMLElement).style.background).toMatch(/#111318|rgb\(17, 19, 24\)/);
    fireEvent.load(img);
    expect(img.style.opacity).toBe('1');
    expect(img.style.transition).toContain('opacity');

    view.rerender(hero('/api/img/plex?url=/library/metadata/2/art/1'));
    const imgs = view.container.querySelectorAll('img');
    expect(imgs).toHaveLength(1);
    img = imgs[0]!;
    expect(img.getAttribute('src')).toContain('/metadata/2/');
    expect(img.style.opacity).toBe('0');
    fireEvent.load(img);
    expect(img.style.opacity).toBe('1');
  });
});
