import { describe, expect, it } from 'vitest';
import { trackerStatusText } from '@/services/allSourcesApi';

describe('v185.1 test-guard tracker status', () => {
  it('reads Blocked by test guard, never a connection error', () => {
    expect(trackerStatusText({ name: 'PrivateHD', status: 'guard' })).toBe('PrivateHD: Blocked by test guard');
    expect(trackerStatusText({ name: 'PrivateHD', status: 'unavailable' })).toBe('PrivateHD unavailable');
  });
});
