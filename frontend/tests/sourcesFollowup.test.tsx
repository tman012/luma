import { readFileSync } from 'fs';
import { resolve } from 'path';
import { render, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { sourcesCount, NO_SOURCES, srcPrimaryBtn, srcCancelBtn, srcConfirmCard, SRC_ROW_BG } from '@/components/media/sourcesTheme';
import { SegmentRow } from '@/components/media/sourcesShared';
import { ManualMatchPicker, MATCH_PICKER_Z } from '@/components/media/ManualMatchPicker';

const read = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
const SOURCES_FILES = [
  'components/media/MovieDetailModal/ExpandedLayout.tsx',
  'components/media/MovieDetailModal/SourcesPanel.tsx',
  'components/media/TvDetailModal/TvSourcesPanel.tsx',
  'components/media/sourcesShared.tsx',
];

afterEach(() => cleanup());

describe('Sources copy', () => {
  it('counts "1 source" and "2 sources"', () => {
    expect(sourcesCount(1)).toBe('1 source');
    expect(sourcesCount(2)).toBe('2 sources');
    expect(sourcesCount(0)).toBe('0 sources');
  });
  it.each(SOURCES_FILES)('%s has no helper lines or old copy', (p) => {
    const src = read(p);
    for (const s of ['No sources found', 'Sources not found', 'Select Resolution', 'Fancy titles',
      'Taking longer than usual', 'Radarr and the indexers', 'Check Sonarr connection', '} sources<']) {
      expect(src, s).not.toContain(s);
    }
  });
  it('empty state reads "No sources"', () => expect(NO_SOURCES).toBe('No sources'));
  it('SegmentRow says "1 source" on a glass row', () => {
    const { container } = render(<SegmentRow label="4K" count={1} releases={[]} colorFor={() => ({ bg: '', text: '' }) as never} onClick={() => {}} />);
    const row = container.querySelector('[data-source-row]') as HTMLElement;
    expect(row.textContent).toContain('1 source');
    expect(row.textContent).not.toContain('1 sources');
    expect(row.style.background.replace(/\s/g, '')).toBe(SRC_ROW_BG);
  });
});

describe('desktop Sources (Expanded) is navy', () => {
  const src = read('components/media/MovieDetailModal/ExpandedLayout.tsx');
  it('uses the navy panel with the Start glow, not a black dim', () => {
    expect(src).toContain('...srcPanel');
    expect(src).not.toContain("background: 'rgba(0,0,0,0.85)'");
    const overlay = src.slice(src.indexOf('data-sources-backdrop'));
    expect(overlay).not.toContain('rgba(30, 30, 30, 0.95)');
    expect(overlay).toContain('data-source-row=""');
  });
  it('confirm card, blue Download and glass Cancel', () => {
    expect(src).toContain('...srcConfirmCard');
    expect(src).toContain('...srcPrimaryBtn');
    expect(src).toContain('...srcCancelBtn');
    expect(srcConfirmCard.borderRadius).toBe('20px');
    expect(srcConfirmCard.background).toContain('#15102a');
    expect(srcPrimaryBtn.background).toBe('var(--color-accent)');
    expect(read('index.css')).toContain('--color-accent: #3b82f6;');
    expect(srcCancelBtn.background).toBe('rgba(255,255,255,0.08)');
    expect(srcCancelBtn.border).toBe('1px solid rgba(255,255,255,0.12)');
  });
  it('TV and phone Cancel are glass too', () => {
    for (const p of ['components/media/MovieDetailModal/SourcesPanel.tsx', 'components/media/TvDetailModal/TvSourcesPanel.tsx']) {
      expect(read(p)).not.toContain("'1px solid #777'");
    }
  });
  it('opens the match picker instead of erroring when there is no TMDb id', () => {
    expect(src).toContain('setSrcMatchFor(srcConfirmTarget)');
    expect(src).toContain('<ManualMatchPicker');
  });
});

describe('grab is admin-only in the UI', () => {
  it.each(['components/media/MovieDetailModal/ExpandedLayout.tsx', 'components/media/MovieDetailModal/CompactLayout.tsx'])(
    '%s shows Sources only to admins', (p) => {
      const src = read(p);
      const i = src.indexOf('<span data-modal-sources=""');
      expect(i).toBeGreaterThan(0);
      expect(src.slice(i - 120, i)).toContain('{isOwner && (');
    });
});

describe('TV search', () => {
  it('sends no year to the indexer search', () => {
    expect(read('components/media/TvDetailModal/useTvSourcesData.ts')).toMatch(/const indexerYear = null;/);
  });
});

describe('ManualMatchPicker z-order', () => {
  it('portals to <body> above every Sources screen', () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"results":[]}', { status: 200 })));
    const host = document.createElement('div');
    host.style.position = 'relative'; host.style.zIndex = '1';
    document.body.appendChild(host);
    render(<ManualMatchPicker initialQuery="x" onSelect={() => {}} onCancel={() => {}} />, { container: host });
    const el = document.querySelector('[data-match-picker]') as HTMLElement;
    expect(el.parentElement).toBe(document.body);
    expect(Number(el.style.zIndex)).toBe(MATCH_PICKER_Z);
    expect(MATCH_PICKER_Z).toBeGreaterThanOrEqual(10100);
    vi.unstubAllGlobals();
    host.remove();
  });
});

describe('Designer pass 2', () => {
  it.each(SOURCES_FILES)('%s: primary buttons use the accent token, sentence case, real ellipsis', (p) => {
    const src = read(p);
    for (const bad of ['#1d4ed8', '#1e3a5f', 'Full Season', 'Season or Episode', 'Add to Watchlist', 'Select Type',
      'Searching for sources...', "'Sending...'", 'srcDot1', 'tvSrcDot1']) {
      expect(src, bad).not.toContain(bad);
    }
  });
  it('TV scope label and heading are sentence case', () => {
    expect(read('components/media/TvDetailModal/tvSourcesUtils.ts')).toContain("label: 'Full season'");
    expect(read('components/media/TvDetailModal/TvSourcesPanel.tsx')).toContain('Full season or episode');
  });
  it('phone Searching uses the ellipsis character', () => {
    for (const p of ['components/media/MovieDetailModal/SourcesPanel.tsx', 'components/media/TvDetailModal/TvSourcesPanel.tsx']) {
      expect(read(p)).toContain('Searching\u2026');
    }
  });
});
