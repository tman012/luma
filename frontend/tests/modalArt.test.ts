// Run: node --experimental-strip-types --test frontend/tests/*.test.ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  artReady, artFadeStyle, preloadImage, movieBackdropFinalAfterDetail, ART_PLACEHOLDER_BG,
} from '../src/utils/modalArt.ts';

const base = { final: true, backdropUrl: '/b.jpg', logoUrl: '/l.png', settled: {}, timedOut: false };

test('nothing shows before the final URLs are known', () => {
  assert.equal(artReady({ ...base, final: false, settled: { '/b.jpg': true, '/l.png': true } }), false);
  assert.equal(artReady({ ...base, final: false, timedOut: true }), false);
});

test('backdrop and logo appear together, only after both settle', () => {
  assert.equal(artReady(base), false);
  assert.equal(artReady({ ...base, settled: { '/b.jpg': true } }), false);
  assert.equal(artReady({ ...base, settled: { '/l.png': true } }), false);
  assert.equal(artReady({ ...base, settled: { '/b.jpg': true, '/l.png': true } }), true);
});

test('a failed image still settles (title shows instead of logo)', () => {
  assert.equal(artReady({ ...base, settled: { '/b.jpg': true, '/l.png': false } }), true);
});

test('a stale URL decoding does not count for the current one', () => {
  assert.equal(artReady({ ...base, backdropUrl: '/tmdb.jpg', settled: { '/plex.jpg': true, '/l.png': true } }), false);
});

test('missing art does not block, and a slow decode is capped', () => {
  assert.equal(artReady({ ...base, backdropUrl: null, logoUrl: null }), true);
  assert.equal(artReady({ ...base, logoUrl: null, settled: { '/b.jpg': true } }), true);
  assert.equal(artReady({ ...base, timedOut: true }), true);
});

test('fade style hides until ready, then fades in', () => {
  assert.deepEqual(artFadeStyle(false), { opacity: 0, transition: 'opacity 0.6s ease-out' });
  assert.equal(artFadeStyle(true).opacity, 1);
  assert.equal(artFadeStyle(true, 0.6).opacity, 0.6);
  assert.equal(ART_PLACEHOLDER_BG, '#15102a');
});

class FakeImage {
  static mode = 'load';
  static decodeFails = false;
  static seen: string[] = [];
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  decoded = false;
  set src(url: string) {
    FakeImage.seen.push(url);
    setTimeout(() => (FakeImage.mode === 'load' ? this.onload?.() : this.onerror?.()), 0);
  }
  decode() {
    this.decoded = true;
    return FakeImage.decodeFails ? Promise.reject(new Error('x')) : Promise.resolve();
  }
}

test('preload waits for load + decode, reports failures', async () => {
  FakeImage.mode = 'load'; FakeImage.decodeFails = false;
  assert.equal(await preloadImage('/a.jpg', FakeImage as never), true);
  FakeImage.decodeFails = true;
  assert.equal(await preloadImage('/a.jpg', FakeImage as never), true);
  FakeImage.mode = 'error';
  assert.equal(await preloadImage('/b.jpg', FakeImage as never), false);
});

test('movie: Plex-only first paint with a TMDb id is not final', () => {
  assert.equal(movieBackdropFinalAfterDetail({ tmdbIdProp: false, tmdbMerged: false, parsedTmdbId: true }), false);
  assert.equal(movieBackdropFinalAfterDetail({ tmdbIdProp: false, tmdbMerged: false, parsedTmdbId: false }), true);
  assert.equal(movieBackdropFinalAfterDetail({ tmdbIdProp: true, tmdbMerged: false, parsedTmdbId: true }), true);
  assert.equal(movieBackdropFinalAfterDetail({ tmdbIdProp: true, tmdbMerged: true, parsedTmdbId: true }), true);
});