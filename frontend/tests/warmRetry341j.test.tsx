import { describe, it, expect, beforeEach } from 'vitest';
import { scheduleWarm, _resetModalPrefetch } from '@/services/modalPrefetch';
import { beginSheetPriority, endSheetPriority, _resetSheetPriority } from '@/loading/sheetPriority';

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('v341j: a warm-up an open cut short runs again after the reveal (warm opens stay warm)', () => {
  beforeEach(() => { _resetModalPrefetch(); _resetSheetPriority(); });
  it('reruns once the open ends, at most WARM_RETRIES times; an uncut warm-up runs once', async () => {
    let cut = 0, plain = 0;
    let release: () => void = () => {};
    scheduleWarm('cut', () => { cut++; return cut === 1 ? new Promise<void>((r) => { release = r; }) : Promise.resolve(); });
    scheduleWarm('plain', () => { plain++; return Promise.resolve(); });
    await flush();
    beginSheetPriority();           // a tap while 'cut' is in flight (dropOtherWarms aborts it)
    release(); await flush();
    expect(cut).toBe(1);            // waits for the reveal
    endSheetPriority(); await flush();
    expect(cut).toBe(2);
    await flush();
    expect(plain).toBe(1);
  });
});
