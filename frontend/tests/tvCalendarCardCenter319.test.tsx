/** v319 (Taylor 11:05 PM): desktop week cards center the show name and episode line under the centered air time;
 * the STATE_COLOR stripe stays, text still ellipsizes. v319 (Taylor 11:13 PM): phone U3Row is now centered too (time on top). */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const cal = readFileSync(resolve(__dirname, '../src/components/library/TvCalendar.tsx'), 'utf8');
const card = cal.slice(cal.indexOf('function GridCard'), cal.indexOf('export function Agenda'));
const glass = cal.slice(cal.indexOf('function GlassCard'), cal.indexOf("/** '8:00 PM' in ET"));

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
  it('v322 phone glass card (option C, replaces U3Row): time, show, episode line centered and ellipsized; a status dot, no stripe', () => {
    expect(cal).not.toContain('function U3Row');
    expect(glass).toContain("textAlign: 'center'");
    expect(glass).toMatch(/data-testid="tv-card-show"[^>]*\.\.\.one/);
    expect(cal).toMatch(/data-testid="tv-glass-ep-title"[^>]*\.\.\.one/);
    expect(glass).toContain('data-testid="tv-glass-dot"');
    expect(glass).not.toContain('<Stripe');
  });
  it('phone sheet has no title and no episode count line', () => {
    const sheet = readFileSync(resolve(__dirname, '../src/components/calendar/TvCalendarSheet.tsx'), 'utf8');
    expect(sheet).not.toMatch(/>TV calendar</);
    expect(cal).not.toContain('tv-day-count');
    expect(cal).toContain('export function DayHeader');
  });
});
