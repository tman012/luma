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
  it('Tester B2: close during the hold or mid-enter dismisses (exit runs, nothing re-opens)', () => {
    const calls: unknown[][] = [];
    (HTMLElement.prototype as unknown as { animate: unknown }).animate = vi.fn((k: unknown) => { calls.push([k]); return { finished: new Promise(() => {}), cancel() {}, play() {}, pause() {} }; });
    const ui = (phase: 'entering' | 'exiting', go: boolean) => <SheetFrame name="MovieDetailModal" phase={phase} composed={false} go={go}><SheetCard background="#111318"><p>x</p></SheetCard></SheetFrame>;
    // during the hold (not moving yet): exit leaves the card hidden, never starts the enter
    const a = render(ui('entering', false));
    a.rerender(ui('exiting', false));
    const card = document.querySelector('[data-sheet-card]') as HTMLElement;
    expect(card.style.visibility).toBe('hidden');
    expect(calls.length).toBe(0);
    a.rerender(ui('exiting', true));   // details/budget arrive after the close: still no enter
    expect(calls.some(([k]) => JSON.stringify(k).includes('"translate3d(0, 0, 0)"') && JSON.stringify(k).indexOf('100%') < JSON.stringify(k).indexOf('(0, 0, 0)'))).toBe(false);
    a.unmount();
    // mid-enter: the exit animates to translateY(100%)
    calls.length = 0;
    const b = render(ui('entering', true));
    b.rerender(ui('exiting', true));
    expect(JSON.stringify(calls[calls.length - 2]?.[0])).toContain('translate3d(0, 100%, 0)');
    b.unmount();
    delete (HTMLElement.prototype as unknown as { animate?: unknown }).animate;
  });
  it('SheetFrame has no useSheetGo of its own (layouts pass go)', () => {
    const s = read('components/layout/SheetFrame.tsx');
    const frame = s.slice(s.indexOf('export function SheetFrame('), s.indexOf('export function SheetCard('));
    expect(frame).not.toContain('useSheetGo(');
    expect(frame).toContain('go: boolean;');
  });
});
