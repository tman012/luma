/** v284 Rooms sheet chrome mirrors ActivitySheet (near-full height, phase enter/exit). */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

describe('v284 Rooms sheet mirrors Activity chrome', () => {
  const rooms = readFileSync('src/components/rooms/RoomsSheet.tsx', 'utf8');
  const activity = readFileSync('src/components/activity/ActivitySheet.tsx', 'utf8');

  it('uses fixed near-full height like Activity (not content-sized maxHeight)', () => {
    expect(rooms).toContain("height: 'calc(100% - env(safe-area-inset-top, 0px) - 6px)'");
    expect(rooms).not.toContain('maxHeight');
    expect(activity).toContain("height: 'calc(100% - env(safe-area-inset-top, 0px) - 6px)'");
  });

  it('shares Activity enter/exit timing and spring', () => {
    expect(rooms).toContain('const ENTER_DURATION = 600');
    expect(rooms).toContain('const EXIT_DURATION = 400');
    expect(rooms).toContain("const SPRING = 'cubic-bezier(0.16, 1, 0.3, 1)'");
    expect(rooms).toContain("useState<'entering' | 'visible' | 'exiting' | 'gone'>('gone')");
    expect(activity).toContain('const ENTER_DURATION = 600');
  });

  it('keeps Rooms title chrome matching Activity (h2 18px) and data-sheet', () => {
    expect(rooms).toContain('data-sheet="RoomsSheet"');
    expect(rooms).toContain('>Rooms</h2>');
    expect(rooms).toContain('text-[18px] font-bold text-white');
    expect(activity).toContain('data-sheet="ActivitySheet"');
    expect(activity).toContain('>Activity</h2>');
  });

  it('keeps Rooms content markers', () => {
    expect(rooms).toContain('Nothing Playing');
    expect(rooms).toContain('Friends & Family');
    expect(rooms).toContain('Open Remote');
    expect(rooms).toContain('FullScreenRemote');
  });
});
