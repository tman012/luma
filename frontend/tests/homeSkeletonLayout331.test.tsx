import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { DEFAULT_SKELETON_LAYOUT, HOME_LAYOUT_KEY, SkeletonRails, readHomeLayout } from '@/components/home/Rails';

afterEach(() => { cleanup(); localStorage.clear(); });

describe('v331 Home skeleton drawn from the last Home layout', () => {
  it('first-ever load: poster rails, the first 265px', () => {
    expect(readHomeLayout()).toEqual(DEFAULT_SKELETON_LAYOUT);
    expect(DEFAULT_SKELETON_LAYOUT[0]).toEqual({ wide: false, h: 265, mt: 28 });
    render(<SkeletonRails afterFirst={<div data-testid="slot" />} />);
    const secs = screen.getByTestId('home-skeleton').querySelectorAll('section');
    expect(secs[0].getAttribute('data-skel-rail')).toBe('poster');
    expect(secs[0].style.height).toBe('265px');
  });
  it('a stored layout (CW first) shapes the rails and keeps the pills after the first one', () => {
    localStorage.setItem(HOME_LAYOUT_KEY, JSON.stringify([{ wide: true, h: 247, mt: 28 }, { wide: false, h: 265, mt: 28 }]));
    render(<SkeletonRails afterFirst={<div data-testid="slot" />} />);
    const kids = Array.from(screen.getByTestId('home-skeleton').children) as HTMLElement[];
    expect(kids.map(k => k.tagName)).toEqual(['SECTION', 'DIV', 'SECTION']);
    expect(kids[0].getAttribute('data-skel-rail')).toBe('wide');
    expect(kids[0].style.height).toBe('247px');
    expect(kids[1].getAttribute('data-testid')).toBe('slot');
  });
  it('bad or foreign data falls back to the default; only shapes and sizes are stored', () => {
    localStorage.setItem(HOME_LAYOUT_KEY, '{not json');
    expect(readHomeLayout()).toEqual(DEFAULT_SKELETON_LAYOUT);
    localStorage.setItem(HOME_LAYOUT_KEY, JSON.stringify([{ title: 'x' }]));
    expect(readHomeLayout()).toEqual(DEFAULT_SKELETON_LAYOUT);
    localStorage.setItem(HOME_LAYOUT_KEY, JSON.stringify([{ wide: 1, h: 200.4, mt: 0, title: 'leak' }]));
    expect(readHomeLayout()).toEqual([{ wide: true, h: 200, mt: 0 }]);
  });
});
