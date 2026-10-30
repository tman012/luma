/** v319 (Taylor 11:05 PM): desktop week cards center the show name and episode line under the centered air time;
 * the STATE_COLOR stripe stays, text still ellipsizes. Phone U3Row is a separate component and is unchanged. */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const cal = readFileSync(resolve(__dirname, '../src/components/library/TvCalendar.tsx'), 'utf8');
const card = cal.slice(cal.indexOf('function GridCard'), cal.indexOf('export function Agenda'));
const u3 = cal.slice(cal.indexOf('function U3Row'));

describe('v319 TV Calendar card centering', () => {
  it('air, show and episode line are all centered', () => {
    expect(card).toMatch(/data-testid="tv-air" style=\{\{[^}]*textAlign: 'center'/);
    expect(card).toMatch(/data-testid="tv-card-show"[^>]*\.\.\.one, textAlign: 'center'/);
    expect(card).toMatch(/data-testid="tv-card-title"[^>]*\.\.\.one, textAlign: 'center'/);
  });
  it('stripe kept, ellipsis kept, symmetric bookmark padding, no thumbnail', () => {
    expect(card).toContain('<Stripe color={STATE_COLOR[st]} w={3} />');
    expect(cal).toMatch(/const one: React\.CSSProperties = \{[^}]*textOverflow: 'ellipsis'/);
    expect(cal).toContain('[data-testid=tv-card-show]{padding-left:28px;padding-right:28px}');
    expect(card).not.toMatch(/Poster|poster_path/);
  });
  it('phone U3Row is untouched (no centering added)', () => {
    expect(u3).not.toContain("textAlign: 'center'");
  });
});
