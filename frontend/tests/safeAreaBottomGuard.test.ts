/**
 * v210 (PM, Taylor): full-bleed guard. A full-screen page or sheet container (position fixed with
 * inset 0 / top+bottom 0, or Tailwind `fixed inset-0`), and html/body/#root, must not put bottom
 * safe-area padding or margin (env(safe-area-inset-bottom)) on itself: that leaves an empty band at the
 * bottom of an installed iPhone PWA. The inset belongs on the scroll content inside the container.
 * A legit exception (e.g. a bottom-anchored action bar that is not a full-screen surface) goes in ALLOW
 * with a reason.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(__dirname, '..', 'src');
/** file:line-snippet -> reason. */
const ALLOW: Record<string, string> = {};

function files(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...files(p));
    else if (/\.(tsx?|css)$/.test(name) && !/\.test\./.test(name)) out.push(p);
  }
  return out;
}

function stripComments(t: string): string {
  return t.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:'"`\\])\/\/[^\n]*/g, (m, p1) => p1 + ' '.repeat(m.length - p1.length));
}

/** Innermost-to-outer {...} blocks with their start offsets (style objects, CSS rules). */
export function blocks(t: string): { start: number; body: string }[] {
  const out: { start: number; body: string }[] = [];
  const stack: number[] = [];
  for (let i = 0; i < t.length; i++) {
    if (t[i] === '{') stack.push(i);
    else if (t[i] === '}' && stack.length) {
      const s = stack.pop()!;
      // direct body only: drop nested blocks so a fixed scrim's inner content is not blamed on it
      let body = t.slice(s + 1, i);
      let prev = '';
      while (prev !== body) { prev = body; body = body.replace(/\{[^{}]*\}/g, ''); }
      out.push({ start: s, body });
    }
  }
  return out;
}

const SAFE_BOTTOM_PAD = /(padding(Bottom|Block|-bottom|-block)?|margin(Bottom|Block|-bottom|-block)?)\s*:\s*[^,;\n]*safe-area-inset-bottom/i;
const FIXED = /position\s*:\s*['"]?fixed/;
const FULL = /inset\s*:\s*['"]?0(px)?['"]?\s*[,;\n}]|inset\s*:\s*0\s*$|\bbottom\s*:\s*['"]?0/m;
const ROOT_SEL = /(^|[\s,}])(html|body|#root)\s*[,{]/;

export function violations(): string[] {
  const out: string[] = [];
  for (const f of files(SRC)) {
    const text = stripComments(readFileSync(f, 'utf8'));
    const rel = relative(SRC, f);
    const lineOf = (o: number) => text.slice(0, o).split('\n').length;
    for (const b of blocks(text)) {
      if (!SAFE_BOTTOM_PAD.test(b.body)) continue;
      const head = text.slice(Math.max(0, b.start - 80), b.start);
      const full = FIXED.test(b.body) && FULL.test(b.body);
      const root = f.endsWith('.css') && ROOT_SEL.test(head.split('}').pop() || '');
      if (full || root) out.push(`${rel}:${lineOf(b.start)}`);
    }
    // Tailwind: one className with fixed + inset-0 + bottom safe-area padding/margin.
    const cls = /className=(?:"([^"]*)"|\{`([^`]*)`\}|\{'([^']*)'\})/g;
    let m: RegExpExecArray | null;
    while ((m = cls.exec(text))) {
      const c = m[1] ?? m[2] ?? m[3] ?? '';
      if (/\bfixed\b/.test(c) && /\binset-0\b/.test(c) && /(\bsafe-bottom\b|\b[pm][by]-\[[^\]]*safe-area-inset-bottom)/.test(c)) {
        out.push(`${rel}:${lineOf(m.index)}`);
      }
    }
  }
  return out.filter((v) => !ALLOW[v]);
}

describe('safe-area bottom guard (full-screen pages and sheets run to the bottom edge)', () => {
  it('no full-screen container or html/body/#root carries bottom safe-area padding or margin', () => {
    expect(violations()).toEqual([]);
  });
  it('catches a fixed full-screen wrapper with bottom inset padding (self-test)', () => {
    const bad = "const w = { position: 'fixed', inset: 0, paddingBottom: 'env(safe-area-inset-bottom)' };";
    const b = blocks(bad).find((x) => SAFE_BOTTOM_PAD.test(x.body))!;
    expect(FIXED.test(b.body) && FULL.test(b.body)).toBe(true);
    const ok = "const w = { position: 'fixed', inset: 0, overflowY: 'auto' }; const c = { paddingBottom: 'env(safe-area-inset-bottom)' };";
    expect(blocks(ok).some((x) => SAFE_BOTTOM_PAD.test(x.body) && FIXED.test(x.body))).toBe(false);
  });
  it('the phone Settings sheet keeps the inset inside its scroll content', () => {
    const t = readFileSync(join(SRC, 'pages', 'LibrariesPage.tsx'), 'utf8');
    expect(t).toMatch(/data-scroll-content style=\{\{ paddingBottom: SHEET_SCROLL_PAD \}\}>\{content/);
    // v276: Home-match in-flow page; no fixed luma-fullscreen / negative-bottom
    expect(t).toMatch(/const sheetPage:[\s\S]*minHeight:\s*'100dvh'/);
    expect(t).not.toMatch(/data-layout="phone"[^>]*luma-fullscreen/);
    expect(t).not.toMatch(/bottom: 'calc\(-1 \* var\(--luma-bleed\)\)'/);
  });
});
