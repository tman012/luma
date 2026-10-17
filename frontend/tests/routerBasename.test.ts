import { afterEach, describe, expect, it } from 'vitest';
import { routerBasename } from '@/App';

describe('routerBasename', () => {
  afterEach(() => { delete (window as { __LUMA_URL_BASE__?: string }).__LUMA_URL_BASE__; });
  it('is undefined without a base', () => { expect(routerBasename()).toBeUndefined(); });
  it('uses the base without a trailing slash', () => {
    (window as { __LUMA_URL_BASE__?: string }).__LUMA_URL_BASE__ = '/luma/';
    expect(routerBasename()).toBe('/luma');
  });
});
