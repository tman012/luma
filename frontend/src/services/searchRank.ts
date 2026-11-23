/**
 * v301: title search ranking. Every title, in your library or not, is ranked together by how well its title fits
 * the query (exact, starts with, contains the phrase, has every word, a partial last word), then by popularity
 * (TMDb votes, then popularity, then TMDb's own relevance). A library title is not pinned above better matches; it
 * carries an In Library badge. Loose matches (the library's typo-tolerant search returns "The Boy" for "the box")
 * are dropped when anything fits; when nothing fits (a typo), every result stays, in the old order.
 */

const STOP = new Set(['the', 'a', 'an', 'of', 'and']);

export function normTitle(s: string | null | undefined): string {
  return String(s || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/&/g, ' and ').replace(/['\u2018\u2019]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
}
const bare = (s: string) => s.replace(/^(the|a|an) /, '');

/** 0 exact, 1 starts with the query, 2 contains it as words, 3 every word (the last may be partial), 4 loose. */
export function matchTier(query: string, title: string | null | undefined): number {
  const q = normTitle(query); const t = normTitle(title);
  if (!q || !t) return 4;
  const bq = bare(q); const bt = bare(t);
  if (t === q || (bq && bt === bq)) return 0;
  if (t.startsWith(`${q} `) || (bq && bt.startsWith(`${bq} `))) return 1;
  const pad = ` ${t} `;
  if (pad.includes(` ${q} `) || (bq && pad.includes(` ${bq} `))) return 2;
  const tw = t.split(' '); const tset = new Set(tw);
  let qw = q.split(' '); const content = qw.filter(w => !STOP.has(w)); if (content.length) qw = content;
  if (qw.every(w => tset.has(w))) return 3;
  const last = qw[qw.length - 1]!; const head = qw.slice(0, -1);
  if (head.every(w => tset.has(w)) && tw.some(w => w.startsWith(last))) return 3;
  return 4;
}

export interface Rankable {
  title?: string; name?: string; isOwned?: boolean;
  vote_count?: number; popularity?: number; relevance_rank?: number;
}

/** Best first; loose matches dropped when any title fits. Stable for equal keys. */
export function rankTitles<T extends Rankable>(query: string, items: T[]): T[] {
  const list = items.map((x, i) => ({ x, i, tier: matchTier(query, x.title || x.name) }));
  const strong = list.some(r => r.tier < 4);
  const kept = strong ? list.filter(r => r.tier < 4) : list;
  kept.sort((a, b) => a.tier - b.tier
    || (strong ? ((b.x.vote_count || 0) - (a.x.vote_count || 0) || (b.x.popularity || 0) - (a.x.popularity || 0)) : 0)
    || (a.x.relevance_rank ?? Number.MAX_SAFE_INTEGER) - (b.x.relevance_rank ?? Number.MAX_SAFE_INTEGER)
    || Number(!!b.x.isOwned) - Number(!!a.x.isOwned)
    || a.i - b.i);
  return kept.map(r => r.x);
}
