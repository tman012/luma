import { describe, expect, it } from 'vitest';
import {
  checkPriority, checkUrl, initialForm, isDirty, keptSecrets, splitServerErrors, toPayload, validate,
} from '@/components/trackers/trackerForm';
import type { Tracker, TrackerType } from '@/components/trackers/trackersApi';
import TYPES from './fixtures/trackerTypes.json';

const T = (id: string) => (TYPES as TrackerType[]).find(t => t.id === id)!;
const saved = (over: Partial<Tracker>): Tracker => ({
  id: 1, type: 'torznab', type_name: 'Torznab', protocol: 'torrent', name: 'Idx', enabled: true, url: 'https://idx.example',
  categories: [2000], priority: 25, status: { state: 'untested', message: '', checked_at: null }, ...over,
});

describe('checkUrl', () => {
  it.each([
    ['https://idx.example', 'torznab', null],
    ['http://idx.example:9117/x', 'torznab', null],
    ['', 'torznab', 'Required'],
    ['idx.example', 'torznab', 'Invalid URL'],
    ['ftp://idx.example', 'torznab', 'Invalid URL'],
    ['https://user:pw@idx.example', 'torznab', 'Invalid URL'],
    ['https://idx.example/?apikey=1', 'torznab', 'Invalid URL'],
    ['https://idx.example/#x', 'torznab', 'Invalid URL'],
    ['https://idx .example', 'torznab', 'Invalid URL'],
    ['http://iptorrents.com', 'iptorrents', 'Invalid URL'],
    ['https://iptorrents.com', 'iptorrents', null],
  ])('%s (%s) -> %s', (url, type, want) => expect(checkUrl(url, T(type))).toBe(want));
});

describe('checkPriority', () => {
  it.each([['1', null], ['50', null], ['0', 'Invalid'], ['51', 'Invalid'], ['2.5', 'Invalid'], ['-3', 'Invalid'], ['', 'Required']])(
    '%s -> %s', (v, want) => expect(checkPriority(v)).toBe(want));
});

describe('form', () => {
  it('a new form starts from the schema defaults and never holds a secret', () => {
    const f = initialForm(T('beyondhd'));
    expect(f.values).toEqual({ name: 'BeyondHD', url: 'https://beyond-hd.me', priority: '25' });
    expect(f.secrets).toEqual({ api_key: { value: '', touched: false }, rss_key: { value: '', touched: false } });
    expect(f.categories).toEqual([2000, 5000]);
    expect(validate(T('beyondhd'), f)).toEqual({ api_key: 'Required', rss_key: 'Required' });
  });

  it('an edit form never copies the mask into a secret', () => {
    const t = saved({ api_key: '********' });
    const f = initialForm(T('torznab'), t);
    expect(f.secrets.api_key).toEqual({ value: '', touched: false });
    expect(JSON.stringify(f)).not.toContain('********');
    expect(toPayload(T('torznab'), f, t)).not.toHaveProperty('api_key');
    expect(isDirty(T('torznab'), f, f, t)).toBe(false);
  });

  it('kept secrets need the same URL and readable credentials', () => {
    const t = saved({ type: 'iptorrents', url: 'https://iptorrents.com', cookie: '********' });
    const f = initialForm(T('iptorrents'), t);
    expect([...keptSecrets(T('iptorrents'), f, t)]).toEqual(['cookie']);
    expect(validate(T('iptorrents'), f, t)).toEqual({});
    const moved = { ...f, values: { ...f.values, url: 'https://ipt.example/' } };
    expect(keptSecrets(T('iptorrents'), moved, t).size).toBe(0);
    expect(validate(T('iptorrents'), moved, t)).toEqual({ cookie: 'Required' });
    const trailing = { ...f, values: { ...f.values, url: 'https://iptorrents.com/' } };
    expect(keptSecrets(T('iptorrents'), trailing, t).size).toBe(1);
    const bad = { ...t, status: { state: 'error' as const, message: 'Credentials unreadable', checked_at: 1 } };
    expect(keptSecrets(T('iptorrents'), f, bad).size).toBe(0);
  });

  it('server errors split into fields and a form-level message', () => {
    expect(splitServerErrors(T('torznab'), { url: 'Invalid URL', _: 'Too many trackers' }))
      .toEqual({ fields: { url: 'Invalid URL' }, form: 'Too many trackers' });
    expect(splitServerErrors(T('torznab'), undefined)).toEqual({ fields: {}, form: '' });
  });

  it('name is collapsed and capped at 60', () => {
    const f = initialForm(T('torznab'));
    f.values = { ...f.values, name: '  a   b ', url: 'https://x.example' };
    expect(toPayload(T('torznab'), f).name).toBe('a b');
    f.values.name = 'x'.repeat(61);
    expect(validate(T('torznab'), f).name).toBe('Too long');
  });
});
