"""v301: title match tiers for search (the library's typo-tolerant search returns loose matches such as
"The Boy" for "the box"; they are ordered after real matches). Mirrors frontend/src/services/searchRank.ts:
0 exact, 1 starts with the query, 2 contains it as words, 3 every word (the last may be partial), 4 loose."""
import re
import unicodedata

_STOP = {'the', 'a', 'an', 'of', 'and'}


def norm_title(s) -> str:
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace('&', ' and ')
    s = re.sub(r"['\u2018\u2019]", '', s)
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()


def _bare(s: str) -> str:
    return re.sub(r'^(the|a|an) ', '', s)


def match_tier(query, title) -> int:
    q, t = norm_title(query), norm_title(title)
    if not q or not t:
        return 4
    bq, bt = _bare(q), _bare(t)
    if t == q or (bq and bt == bq):
        return 0
    if t.startswith(q + ' ') or (bq and bt.startswith(bq + ' ')):
        return 1
    pad = f' {t} '
    if f' {q} ' in pad or (bq and f' {bq} ' in pad):
        return 2
    tw = t.split(' ')
    tset = set(tw)
    qw = q.split(' ')
    content = [w for w in qw if w not in _STOP]
    if content:
        qw = content
    if all(w in tset for w in qw):
        return 3
    last, head = qw[-1], qw[:-1]
    if all(w in tset for w in head) and any(w.startswith(last) for w in tw):
        return 3
    return 4


def order_by_match(query, items, key='title'):
    """Stable: real matches first (by tier), loose ones after, each item tagged with match_tier."""
    for it in items:
        it['match_tier'] = match_tier(query, it.get(key))
    return sorted(items, key=lambda it: it['match_tier'])
