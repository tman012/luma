/** Reviewer follow-ups on b618f7b4: a scrim tap closes the sheet during the hold (before it moves); the primed
 *  start is cancelled on unmount; SheetFrame takes `go` from its layout (no second timer of its own). */
import { cleanup, fireEvent, render } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SheetCard, SheetFrame } from '@/components/layout/SheetFrame';

const read = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
afterEach(cleanup);

describe('phone sheet hold', () => {
  it('a scrim tap during the hold closes the sheet', () => {
    const onClose = vi.fn();
    render(<SheetFrame name="TvDetailModal" phase="entering" composed={false} go={false} onScrimClick={onClose}><SheetCard background="#111318"><p>x</p></SheetCard></SheetFrame>);
    const scrim = document.querySelector('[data-sheet-scrim]') as HTMLElement;
    expect(scrim).toBeTruthy();
    expect(getComputedStyle(scrim).visibility).not.toBe('hidden');
    fireEvent.click(scrim);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
  it('the enter starts on the first frame (no deferred start) and unmount cancels it', () => {
    const cancel = vi.fn();
    const animate = vi.fn(() => ({ finished: new Promise(() => {}), cancel, play() {}, pause() {} }));
    (HTMLElement.prototype as unknown as { animate: unknown }).animate = animate;
    const { unmount } = render(<SheetFrame name="TvDetailModal" phase="entering" composed go><SheetCard background="#111318"><p>x</p></SheetCard></SheetFrame>);
    expect(animate).toHaveBeenCalled();   // synchronously, in the mount commit
    unmount();
    expect(cancel).toHaveBeenCalled();
    delete (HTMLElement.prototype as unknown as { animate?: unknown }).animate;
  });
  it('SheetFrame has no useSheetGo of its own (layouts pass go)', () => {
    const s = read('components/layout/SheetFrame.tsx');
    const frame = s.slice(s.indexOf('export function SheetFrame('), s.indexOf('export function SheetCard('));
    expect(frame).not.toContain('useSheetGo(');
    expect(frame).toContain('go: boolean;');
  });
});
