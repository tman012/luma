/** v290: Live TV empty-art fallback on Rooms card + FullScreenRemote hero. */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import {
  isLiveTvNoArt,
  liveTvDisplayTitle,
} from '@/components/rooms/liveTvArt';

describe('v290 liveTvArt helpers', () => {
  it('detects TiViMate / Watching Live TV / mediaType=live when no art', () => {
    expect(isLiveTvNoArt({
      appName: 'TiViMate', title: 'Watching Live TV', thumb: null, art: null,
    }, false)).toBe(true);
    expect(isLiveTvNoArt({
      appName: 'TiViMate', title: 'CNN HD', thumb: null, art: null,
    }, false)).toBe(true);
    expect(isLiveTvNoArt({
      appName: 'YouTube', title: 'Watching Live TV', thumb: null, art: null,
    }, false)).toBe(true);
    expect(isLiveTvNoArt({
      appName: 'Unknown', title: 'News', thumb: null, art: null, mediaType: 'live',
    }, false)).toBe(true);
  });

  it('does not treat as live fallback when art/thumb or Plex session', () => {
    expect(isLiveTvNoArt({
      appName: 'TiViMate', title: 'Watching Live TV', thumb: 'http://x/t.jpg', art: null,
    }, false)).toBe(false);
    expect(isLiveTvNoArt({
      appName: 'TiViMate', title: 'Watching Live TV', thumb: null, art: null,
    }, true)).toBe(false);
    expect(isLiveTvNoArt({
      appName: 'Netflix', title: 'Stranger Things', thumb: null, art: null,
    }, false)).toBe(false);
  });

  it('display title is Live TV or channel name (AmEng)', () => {
    expect(liveTvDisplayTitle({ appName: 'TiViMate', title: 'Watching Live TV' })).toBe('Live TV');
    expect(liveTvDisplayTitle({ appName: 'TiViMate', title: 'Playing in TiViMate' })).toBe('Live TV');
    expect(liveTvDisplayTitle({ appName: 'TiViMate', title: null })).toBe('Live TV');
    expect(liveTvDisplayTitle({ appName: 'TiViMate', title: 'ESPN' })).toBe('ESPN');
  });
});

describe('v290 RoomsSheet + FullScreenRemote source', () => {
  const rooms = readFileSync('src/components/rooms/RoomsSheet.tsx', 'utf8');
  const remote = readFileSync('src/components/rooms/FullScreenRemote.tsx', 'utf8');
  const helper = readFileSync('src/components/rooms/liveTvArt.tsx', 'utf8');

  it('RoomCard uses poster-sized sky Live TV slot + APP_COLORS Live TV sky; TiViMate stays purple', () => {
    expect(rooms).toContain("'Live TV': { badge: 'bg-sky-600 text-white'");
    expect(rooms).toContain("TiViMate: { badge: 'bg-purple-600 text-white'");
    expect(rooms).toContain('isLiveTvNoArt');
    expect(rooms).toContain('data-live-tv-poster');
    expect(rooms).toContain('bg-sky-950');
    expect(rooms).toContain('bg-sky-500/15');
    expect(rooms).toContain('LiveTvGlyph');
  });

  it('FullScreenRemote hero uses gradient + large glyph when live and no art', () => {
    expect(remote).toContain('isLiveTvNoArt');
    expect(remote).toContain('data-live-tv-hero');
    expect(remote).toContain('#0b1220');
    expect(remote).toContain('#0c4a6e');
    expect(remote).toContain('LiveTvGlyph size={72}');
    expect(remote).toContain('text-white/25');
  });

  it('shared glyph is TV+antenna stroke; copy is Live TV', () => {
    expect(helper).toContain('polyline points="17 2 12 7 7 2"');
    expect(helper).toContain("return 'Live TV'");
  });
});
