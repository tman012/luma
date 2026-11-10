/** Reviewer follow-ups on b618f7b4: a scrim tap closes the sheet during the hold (before it moves); the primed
 *  start is cancelled on unmount; SheetFrame takes `go` from its layout (no second timer of its own). */
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SheetCard, SheetFrame } from '@/components/layout/SheetFrame';

const read = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
afterEach(cleanup);

describe('phone sheet hold', () => {
  it('the hold has no sheet and no scrim; a scrim tap only exists once the sheet is open', () => {
    const onClose = vi.fn();
    const view = render(<SheetFrame name="TvDetailModal" phase="entering" composed={false} go={false} onScrimClick={onClose}><SheetCard background="#111318"><p>x</p></SheetCard></SheetFrame>);
    expect(document.querySelector('[data-sheet-scrim]')).toBeNull();
    expect(document.querySelector('[data-modal="TvDetailModal"]')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    view.rerender(<SheetFrame name="TvDetailModal" phase="entering" composed go onScrimClick={onClose}><SheetCard background="#111318"><p>x</p></SheetCard></SheetFrame>);
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
    const ui = (phase: 'entering' | 'exiting', go: boolean) => <SheetFrame name="MovieDetailModal" phase={phase} composed={go} go={go}><SheetCard background="#111318"><p>x</p></SheetCard></SheetFrame>;
    // during the hold the sheet is not in the document, so an exit never starts an enter
    const a = render(ui('entering', false));
    a.rerender(ui('exiting', false));
    expect(document.querySelector('[data-sheet-card]')).toBeNull();
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
  it('a second open on the same sheet freezes the slide again', async () => {
    const finishes: Array<() => void> = [];
    (HTMLElement.prototype as unknown as { animate: unknown }).animate = vi.fn(() => ({
      finished: new Promise<void>((resolve) => { finishes.push(resolve); }),
      cancel() {}, play() {}, pause() {},
    }));
    const ui = (go: boolean, label: string) => (
      <SheetFrame name="MovieDetailModal" phase="entering" composed={go} go={go}>
        <SheetCard background="#111318"><p>{label}</p></SheetCard>
      </SheetFrame>
    );
    const view = render(ui(true, 'ready'));
    expect(document.querySelector('[data-sheet-card]')?.textContent).toBe('ready');
    await act(async () => { finishes.splice(0).forEach((resolve) => resolve()); });
    view.rerender(ui(true, 'after'));
    expect(document.querySelector('[data-sheet-card]')?.textContent).toBe('after');
    view.rerender(ui(false, 'hidden'));
    expect(document.querySelector('[data-sheet-card]')).toBeNull();
    view.rerender(ui(true, 'reopen'));
    expect(document.querySelector('[data-sheet-card]')?.textContent).toBe('reopen');
    view.rerender(ui(true, 'during'));
    expect(document.querySelector('[data-sheet-card]')?.textContent).toBe('reopen');
    view.unmount();
    delete (HTMLElement.prototype as unknown as { animate?: unknown }).animate;
  });
  it('hold={false}: content that lands while the card slides commits at once; hold freezes it as of the render that set it', async () => {
    const finishes: Array<() => void> = [];
    (HTMLElement.prototype as unknown as { animate: unknown }).animate = vi.fn(() => ({
      finished: new Promise<void>((resolve) => { finishes.push(resolve); }),
      cancel() {}, play() {}, pause() {},
    }));
    const ui = (hold: boolean, label: string) => (
      <SheetFrame name="MovieDetailModal" phase="entering" composed go>
        <SheetCard background="#111318" hold={hold}><p>{label}</p></SheetCard>
      </SheetFrame>
    );
    const view = render(ui(false, 'seed'));
    view.rerender(ui(false, 'details'));
    expect(document.querySelector('[data-sheet-card]')?.textContent).toBe('details');
    view.unmount();
    const held = render(ui(true, 'seed'));
    held.rerender(ui(true, 'details'));
    expect(document.querySelector('[data-sheet-card]')?.textContent).toBe('seed');
    held.unmount();
    const late = render(ui(false, 'seed'));
    late.rerender(ui(false, 'details'));
    late.rerender(ui(true, 'revealed'));
    late.rerender(ui(true, 'ratings'));
    expect(document.querySelector('[data-sheet-card]')?.textContent).toBe('revealed');
    late.unmount();
    delete (HTMLElement.prototype as unknown as { animate?: unknown }).animate;
  });
  it('SheetFrame has no hold timer of its own: it opens unless a layout passes go false', () => {
    const s = read('components/layout/SheetFrame.tsx');
    const frame = s.slice(s.indexOf('export function SheetFrame('), s.indexOf('export function SheetCard('));
    expect(frame).not.toContain('useSheetGo(');
    expect(frame).toContain('go = true');
    expect(s).not.toContain('export function useSheetGo');
  });
});
