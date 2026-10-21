// v219 (PM): playback is on in minimal (test) builds, as prod 1b4ea74: Play/Watch in the movie and TV
// popups open prod's PlaybackModal / MobilePlaybackSheet, and the phone TV Shuffle shows.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createPlayQueue, fetchRooms, getResumeInfo, startPlayback } from '@/services/playbackApi';

const read = (f: string) => readFileSync(resolve(__dirname, '../src', f), 'utf8');

describe('v219 playback in minimal builds', () => {
  it('movie popup: Play/Watch uses prod\'s plain setShowPlayback (no minimal no-op)', () => {
    const s = read('components/media/MovieDetailModal/index.tsx');
    expect(s).toContain('const [showPlayback, setShowPlayback] = useState(false);');
    expect(s).not.toMatch(/setShowPlaybackState|v && isMinimal\(\)/);
    expect(read('components/media/MovieDetailModal/ExpandedLayout.tsx')).toContain('onClick={() => props.setShowPlayback(true)}');
    expect(read('components/media/MovieDetailModal/CompactLayout.tsx')).toContain('onClick={() => setShowPlayback(true)}');
  });
  it('TV popup: onPlayEpisode is prod\'s (sets the key and title, opens playback)', () => {
    const s = read('components/media/TvDetailModal/index.tsx');
    expect(s).toMatch(/onPlayEpisode: \(rk: number, t: string\) => \{\s*setPlaybackRatingKey\(rk\);\s*setPlaybackTitle\(t\);\s*setShowPlayback\(true\);/);
    expect(s).not.toMatch(/isMinimal/);
  });
  it('phone TV Shuffle is kept under the minimal action hide and calls prod\'s setShuffleTarget', () => {
    const s = read('components/media/TvDetailModal/CompactLayout.tsx');
    expect(s).toMatch(/<span data-modal-keep="" data-tv-shuffle="" style=\{\{ display: 'contents' \}\}>\s*\{setShuffleTarget && \(/);
    const block = s.slice(s.indexOf('data-tv-shuffle'), s.indexOf('data-tv-shuffle') + 3000);
    expect(block).toContain('label="Shuffle"');
    expect(block).toContain('setShuffleTarget!({ ratingKey: show.ratingKey!');
    expect(read('index.css')).toContain(':not([data-modal-sources]):not([data-modal-keep])');
  });
  it('SW is v219', () => {
    expect(readFileSync(resolve(__dirname, '../../static/sw.js'), 'utf8')).toContain("const CACHE_VERSION = 'media-kiosk-v219';");
  });
});

describe('playbackApi calls only the routes minimal opens', () => {
  afterEach(() => vi.unstubAllGlobals());
  it('rooms, resume info, play queue and play', async () => {
    const calls: [string, string][] = [];
    vi.stubGlobal('fetch', vi.fn(async (u: RequestInfo | URL, init?: RequestInit) => {
      calls.push([(init?.method ?? 'GET').toUpperCase(), String(u)]);
      return new Response(JSON.stringify({ success: true, rooms: [], play_queue_id: 7 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));
    await fetchRooms();
    await getResumeInfo(42);
    await createPlayQueue(42, true);
    await startPlayback(42, '10.0.0.5', 'room-1', 0, 7, 'dev-1', 'svc-1');
    const norm = calls.map(([m, u]) => [m, u.replace(/^https?:\/\/[^/]+/, '').replace(/\?.*$/, '')]);
    expect(norm).toEqual([
      ['GET', '/api/settings/rooms'],
      ['GET', '/api/plex/resume_info/42'],
      ['POST', '/api/plex/playqueue'],
      ['POST', '/api/player/play'],
    ]);
  });
});
