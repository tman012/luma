// Prod 1b4ea74 casing: PlaybackModal 'Not Configured', phone TV popup 'N Season(s)'; colors stay prod's.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
const read = (f: string) => readFileSync(resolve(__dirname, '../src', f), 'utf8');
describe('prod casing', () => {
  it('PlaybackModal says Not Configured and keeps prod colors', () => {
    const s = read('components/media/PlaybackModal.tsx');
    expect(s).toContain("'Not Configured'");
    expect(s).not.toContain("'Not configured'");
    expect(s).toContain("background: '#2a2a2a'");
    expect(s).toContain('rgba(42,42,42,0.5), rgba(42,42,42,0.92)');
  });
  it('phone TV popup uses prod Season/Seasons string', () => {
    const s = read('components/media/TvDetailModal/CompactLayout.tsx');
    expect(s).toContain("`${show.numberOfSeasons} Season${show.numberOfSeasons !== 1 ? 's' : ''}`");
    expect(s).not.toContain('seasonsLabel(');
  });
});
