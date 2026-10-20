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

// v174 (PM, Designer): no Title Case labels. Proper nouns, brand and feature names keep their capitals;
// user data, example values, quoted OS strings and model/feed/team names are data, not labels.
export const PROPER_PHRASES = ['Home Assistant', 'Plex Media Server', 'The Movie Database', 'Open Movie Database',
  'Cloudflare Zero Trust', 'Cloudflare Tunnel', 'Zero Trust', 'Harmony Hub',
  'Daily Kiosk', 'Luma AI', 'NVIDIA Shield', 'Android TV', 'Google TV',
  'Silk Browser', 'Home Screen', 'Fire TV', 'Apple TV', 'Describe it', 'Now available',
  // TMDB genre names and tracker category names are data
  'Science Fiction', 'TV Movie', 'No Movies category'];
export const PROPER_WORDS = new Set(['Alfred', 'Plex', 'Radarr', 'Sonarr', 'Kodi', 'Trakt', 'Safari', 'Luma', 'Explore',
  'Chromecast', 'Shield', 'Android', 'Google', 'Cloudflare', 'Harmony', 'Docker', 'Flask', 'Caddy', 'OpenWeatherMap',
  'OpenAI', 'Remux', 'English', 'British', 'Fire', 'Amazon', 'Apple', 'Sonos', 'Roku', 'Jellyfin', 'Emby', 'Prowlarr',
  'Jackett', 'Overseerr', 'Netflix', 'Hulu', 'Tautulli', 'Step', 'Latest', 'Trending', 'Anticipated', 'Hub', 'Chrome',
  'Firefox', 'Windows', 'Mac', 'Linux', 'Nest', 'Alexa', 'Siri', 'Discover', 'Watchlist', 'Settings', 'Downloads',
  'IMDb', 'TMDb', 'Pair', 'I']);
/** Data that reads like labels but isn't one (feed, model, team and example names; quoted OS strings), and
 * v205 prod 1b4ea74-matched movie modal labels (PM: test modals match prod's capitalization). */
export const PROD_MATCHED = ['Add to Watchlist', 'In Watchlist', 'Select Quality', 'Upgrade Quality', 'Watchlist'];
/** v212 prod 1b4ea74-matched Home Watchlist rail date label ('NOV 3'): prod upper-cases it by CSS, so test does too
 * (PM: test matches prod's capitalization). Only this file's cardLabel style may do it. */
export const PROD_MATCHED_UPPERCASE = ["components/home/Rails.tsx: textTransform: 'uppercase'"];
const prodUpper = (h: string) => /^components\/home\/Rails\.tsx:\d+: .*cardLabel|^components\/home\/Rails\.tsx:\d+: marginTop: 4, fontSize: 9/.test(h);
const DATA_RE = /^(Add to Watchlist$|In Watchlist$|Select Quality$|Upgrade Quality$|Watchlist$|BBC News|NPR News|GPT-|e\.g\. |Sport, Sports$|Recently Released$|Watch Shield$|"|.*"$|A (blue )?banner will appear|-apple-system|ui-monospace|Amazon Ember|Your download is complete|I've added|Here are some movies)/;
const PROPS = '(?:label|aria-label|ariaLabel|title|placeholder|heading|subtitle|eyebrow|description|emptyText|buttonLabel|confirmLabel|cancelLabel|actionLabel|text|name|header|caption|tooltip|alt|cta)';
const LABEL_RES = [
  new RegExp(PROPS + '=\\s*"([^"{}]+)"', 'g'),
  new RegExp(PROPS + "=\\s*\\{\\s*'([^'{}]+)'\\s*\\}", 'g'),
  new RegExp(PROPS + "\\s*:\\s*'([^'\\n]+)'", 'g'),
  new RegExp(PROPS + '\\s*:\\s*"([^"\\n]+)"', 'g'),
  />([^<>{}\n]+)</g,
  /[?:]\s*'([^'\n]+)'/g,                       // ternary operands: cond ? 'Run now' : 'Running…'
  /\b\w+\s*=\s*'([^'\n]+)'\s*[,)]/g,            // defaults: label = 'Test connection'
];

/** The Title Case words in a label ('' words when it's sentence case). */
export function titleCaseWords(label: string): string[] {
  let t = label.trim();
  if (DATA_RE.test(t)) return [];
  if (/[;=[\]]/.test(t) || t.includes('_') || (t.includes('/') && !t.includes(' '))) return [];
  if (/\(\s*\w+\??\s*:|\bas (Promise|Record)\b|^\(?\w+\??: |\): Promise|^boolean\)/.test(t)) return [];   // TS, not text
  t = t.replace(/["“][^"”]*["”]/g, '""');                     // a quoted OS string is kept as written
  for (const ph of PROPER_PHRASES) t = t.split(ph).join('\u0000');
  const bad: string[] = [];
  let first = true;
  for (const tok of t.split(/\s+/)) {
    if (!tok) continue;
    const core = tok.replace(/^[^A-Za-z\u0000]+/, '');
    const w = (core.match(/^[A-Za-z][A-Za-z0-9'’-]*/) || [''])[0];
    if (!first && w && /^[A-Z]/.test(w)) {
      const keep = w === w.toUpperCase() || /^[A-Z]{2}/.test(w) || /\d/.test(w) || /[a-z][A-Z]/.test(w) || PROPER_WORDS.has(w)
        || PROPER_WORDS.has(w.split('-')[0]!) || (w.length > 1 && w.slice(1) === w.slice(1).toUpperCase());
      if (!keep) bad.push(w);
    }
    if (/[A-Za-z\u0000]/.test(tok)) first = /[.!?]["”)]?$/.test(tok);
  }
  return bad;
}

function titleCaseHits(): string[] {
  const out: string[] = [];
  for (const f of files(SRC).filter((p) => /\.tsx?$/.test(p))) {
    const lines = code(readFileSync(f, 'utf8')).split('\n');
    lines.forEach((line, i) => {
      if (/^\s*(import |console\.)/.test(line) || /\{ id: \d+, name:/.test(line)) return;
      for (const re of LABEL_RES) {
        for (const m of line.matchAll(re)) {
          // "A · B" badges: each segment is its own label and starts with a capital
          const bad = m[1]!.split(/\s·\s/).flatMap((seg) => titleCaseWords(seg));
          if (bad.length) out.push(`${relative(SRC, f)}:${i + 1}: "${m[1]!.trim()}" (${bad.join(', ')})`);
        }
      }
    });
  }
  return out;
}

describe('sentence case guard', () => {
  it('no CSS upper-casing anywhere in the app', () => {
    expect(hits(/(^|['"`\s])uppercase(?=['"`\s]|$)/).filter((h) => !prodUpper(h))).toEqual([]);
    expect(hits(/textTransform:\s*['"]uppercase['"]/).filter((h) => !prodUpper(h))).toEqual([]);
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

  it('v174: no Title Case labels anywhere in the app', () => {
    expect(titleCaseHits()).toEqual([]);
  });

  it('v174: the Title Case check catches new labels and keeps proper nouns and data', () => {
    expect(titleCaseWords('Quality Profile')).toEqual(['Profile']);
    expect(titleCaseWords('Scan Interval (minutes)')).toEqual(['Interval']);
    expect(titleCaseWords('Quality profile')).toEqual([]);
    expect(titleCaseWords('Movie quality profile (Radarr)')).toEqual([]);
    expect(titleCaseWords('Enable Movie Night')).toEqual(['Movie', 'Night']);   // v175: feature names are sentence case too
    expect(titleCaseWords('Genre roulette ran')).toEqual([]);
    expect(titleCaseWords('Plex server URL')).toEqual([]);
    expect(titleCaseWords('Import from Prowlarr')).toEqual([]);
    expect(titleCaseWords('4K Remux auto-grab')).toEqual([]);
    expect(titleCaseWords('qBittorrent and IPTorrents and TMDB')).toEqual([]);
    expect(titleCaseWords('Setup closed. Restart Luma to try again.')).toEqual([]);
    expect(titleCaseWords('BBC News: Top Stories')).toEqual([]);
    expect(titleCaseWords('e.g. Living Room')).toEqual([]);
    expect(titleCaseWords('Fix Match')).toEqual(['Match']);
    expect(titleCaseWords('Home Assistant area')).toEqual([]);
    expect(titleCaseWords('Run Now')).toEqual(['Now']);
  });

  it('v174: the PM examples', () => {
    const auto = readFileSync(join(SRC, 'pages/settings/AutomationTab.tsx'), 'utf8');
    expect(auto).toContain('label="Quality profile"');
    expect(auto).not.toContain('label="Quality Profile"');
    expect(auto).toContain('label="Scan interval (minutes)"');
  });
});
