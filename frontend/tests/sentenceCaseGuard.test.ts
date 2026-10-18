/**
 * v173 (Designer, v171 polish): sentence case everywhere. No label or eyebrow is upper-cased by CSS
 * (Tailwind `uppercase`, `textTransform: 'uppercase'`, `text-transform: uppercase`), and no JSX text
 * is written in capitals except acronyms. Tracker and origin chips are neutral glass; 'No destination'
 * is amber #f59e0b.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';
import { getBadgeColor as movieBadge } from '@/components/media/MovieDetailModal/sourcesUtils';
import { getBadgeColor as tvBadge } from '@/components/media/TvDetailModal/tvSourcesUtils';
import { NEUTRAL_GLASS } from '@/components/media/chipGlass';
import { sourceTypeLabel } from '@/components/media/sourcesShared';
import { activeEdge, EDGE_NO_DESTINATION } from '@/components/activity/ActivityFeed';
import { sentenceCase } from '@/utils/sentenceCase';

const SRC = join(__dirname, '..', 'src');

function files(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...files(p));
    else if (/\.(tsx?|css)$/.test(name) && !/\.test\./.test(name)) out.push(p);
  }
  return out;
}

/** Comments out: a comment may talk about upper case. */
function code(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:'"`\\])\/\/[^\n]*/g, (m, p1) => p1 + ' '.repeat(m.length - p1.length))
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '));
}

function hits(re: RegExp): string[] {
  const out: string[] = [];
  for (const f of files(SRC)) {
    const lines = code(readFileSync(f, 'utf8')).split('\n');
    lines.forEach((l, i) => { if (re.test(l)) out.push(`${relative(SRC, f)}:${i + 1}: ${l.trim().slice(0, 120)}`); });
  }
  return out;
}

// Acronyms and fixed codes that are written in capitals on purpose.
const ACRONYMS = new Set(['TV', 'PIN', 'URL', 'API', 'SSL', 'VPN', 'APK', 'SMB', 'NFS', 'MLB', 'ADB', 'IMDb', 'TMDB',
  'HDR', 'UHD', 'WEB', 'DL', 'NAS', 'RSS', 'ID', 'OK', 'HD', 'SD', 'AI', 'PWA', 'CA', 'ET', 'PT', 'UTC', 'DNS', 'IP',
  'LAN', 'HTTP', 'HTTPS', 'JSON', 'CSV', 'TBA', 'NFL', 'NBA', 'NHL', 'MLS', 'ESPN', 'HBO', 'BBC', 'AMC', 'FX', 'MKV',
  'QR', 'TLS', 'CPU', 'GPU', 'RAM', 'USB', 'HDMI', 'IR', 'CEC']);

describe('sentence case guard', () => {
  it('no CSS upper-casing anywhere in the app', () => {
    expect(hits(/(^|['"`\s])uppercase(?=['"`\s]|$)/)).toEqual([]);
    expect(hits(/textTransform:\s*['"]uppercase['"]/)).toEqual([]);
    expect(hits(/text-transform:\s*uppercase/)).toEqual([]);
  });

  it('no JSX text in capitals except acronyms', () => {
    const bad: string[] = [];
    for (const f of files(SRC).filter((p) => p.endsWith('.tsx'))) {
      const text = code(readFileSync(f, 'utf8'));
      for (const m of text.matchAll(/>([^<>{}]+)</g)) {
        if (/[;=()[\]]/.test(m[1]!)) continue;          // code between a > and a <, not JSX text
        for (const w of m[1]!.match(/\b[A-Z][A-Z0-9]{2,}\b/g) || []) {
          if (!ACRONYMS.has(w) && !/^\d/.test(w)) bad.push(`${relative(SRC, f)}: ${w} in "${m[1]!.trim().slice(0, 60)}"`);
        }
      }
    }
    expect(bad).toEqual([]);
  });

  it('the Designer examples read in sentence case', () => {
    const mn = readFileSync(join(SRC, 'pages/settings/MovieNightCard.tsx'), 'utf8');
    expect(mn).toContain("Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'never'}");
    const rooms = readFileSync(join(SRC, 'pages/settings/components/RoomAccordion.tsx'), 'utf8');
    expect(rooms).toMatch(/text-white\/25">Services<\/span>/);
    expect(sentenceCase('kodi')).toBe('Kodi');
    expect(sentenceCase('tv')).toBe('TV');
    expect(sentenceCase('movie')).toBe('Movie');
    expect(sourceTypeLabel('REMUX')).toBe('Remux');
    expect(sourceTypeLabel('BLURAY')).toBe('Blu-ray');
    expect(sourceTypeLabel('WEB-DL')).toBe('WEB-DL');
  });
});

describe('neutral glass chips', () => {
  it('every tracker chip is the same neutral glass, movie and TV', () => {
    for (const name of ['IPTorrents', 'IPT', 'PrivateHD', 'PHD', 'BeyondHD', 'AvistaZ', 'Some Torznab', '']) {
      expect(movieBadge(name)).toEqual(NEUTRAL_GLASS);
      expect(tvBadge(name)).toEqual(NEUTRAL_GLASS);
    }
    expect(NEUTRAL_GLASS).toEqual({ bg: 'rgba(255,255,255,0.08)', fg: 'rgba(255,255,255,0.7)', border: 'rgba(255,255,255,0.1)' });
  });

  it('no per-origin tint is left (Genre Roulette violet)', () => {
    const feed = readFileSync(join(SRC, 'components/activity/ActivityFeed.tsx'), 'utf8');
    expect(feed).not.toMatch(/167,\s*139,\s*250|196,\s*181,\s*253/);
  });

  it("'No destination' is amber #f59e0b, edge and text", () => {
    expect(EDGE_NO_DESTINATION).toBe('#f59e0b');
    expect(activeEdge('no_destination')).toBe('#f59e0b');
    expect(activeEdge('stalled')).toBe('#fbbf24');
  });
});
