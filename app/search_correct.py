"""Typo rescue for TMDb title search.

TMDb's search is literal: 'the nit job' returns The Bank Job, not The Nut
Job, and The Nut Job isn't in TMDb's popular list, so the title dictionary
can't correct it either. When the direct results don't resemble the query,
this tries one-letter variants of the query's words (same first letter,
same length: a vowel for a vowel, or a neighbouring key) and keeps a variant
only when TMDb returns a title that reads exactly like it. The caller ranks
those corrected results ahead of the weak direct ones.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from app.fast_search import _one_edit, normalize_text

try:
    from rapidfuzz import fuzz as _fuzz
except Exception:  # pragma: no cover - rapidfuzz is a dependency
    _fuzz = None

VOWELS = 'aeiouy'
_ROWS = ('qwertyuiop', 'asdfghjkl', 'zxcvbnm')
_NEIGHBOURS: Dict[str, str] = {}
for _r, _row in enumerate(_ROWS):
    for _i, _ch in enumerate(_row):
        near = []
        if _i > 0:
            near.append(_row[_i - 1])
        if _i + 1 < len(_row):
            near.append(_row[_i + 1])
        _NEIGHBOURS[_ch] = ''.join(near)

STOP_WORDS = frozenset({'the', 'and', 'for', 'with', 'from', 'into', 'are', 'was', 'his', 'her', 'its', 'our'})
MAX_VARIANTS = 12
STRONG_RATIO = 90.0


def _ratio(a: str, b: str) -> float:
    if _fuzz is not None:
        return float(_fuzz.ratio(a, b))
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio() * 100.0


def _title(item: dict) -> str:
    return str(item.get('title') or item.get('name') or '')


def matches(query: str, title: str) -> bool:
    """A title reads like the query: contains it, is nearly the same, or has
    every query word (allowing one wrong letter in each)."""
    q, t = normalize_text(query), normalize_text(title)
    if not q or not t:
        return False
    if q in t or _ratio(q, t) >= STRONG_RATIO:
        return True
    # Every query word is in the title, exactly or one letter off ('warz').
    tw = t.split()
    return all(any(w == x or _one_edit(w, x) for x in tw) for w in q.split())


def strong(query: str, items: Iterable[dict], top: int = 3) -> bool:
    """Any of the first `top` results reads like the query."""
    for i, item in enumerate(items):
        if i >= top:
            break
        if matches(query, _title(item)):
            return True
    return False


def word_variants(word: str) -> List[str]:
    """One-letter substitutions after the first letter: vowel for vowel,
    then neighbouring keys. Most likely first; no duplicates."""
    word = word.lower()
    if len(word) < 3 or not word.isalpha():
        return []
    out: List[str] = []
    for pos in range(1, len(word)):
        ch = word[pos]
        pool = (VOWELS.replace(ch, '') if ch in VOWELS else '') + _NEIGHBOURS.get(ch, '')
        for sub in pool:
            v = word[:pos] + sub + word[pos + 1:]
            if v != word and v not in out:
                out.append(v)
    # Vowel swaps lead: they're the usual slip ('nit' -> 'nut').
    out.sort(key=lambda v: 0 if any(a != b and a in VOWELS and b in VOWELS for a, b in zip(v, word)) else 1)
    return out


def query_variants(query: str, limit: int = MAX_VARIANTS) -> List[str]:
    """Queries with one word changed by one letter, shortest words first
    (a slip in a short word hurts TMDb's literal match the most)."""
    words = query.split()
    order = sorted((i for i, w in enumerate(words) if w.lower() not in STOP_WORDS and len(w) >= 3),
                   key=lambda i: len(words[i]))
    per_word = [(i, word_variants(words[i])) for i in order]
    out: List[str] = []
    depth = 0
    while len(out) < limit and any(depth < len(v) for _, v in per_word):
        for i, vs in per_word:
            if depth < len(vs) and len(out) < limit:
                out.append(' '.join(words[:i] + [vs[depth]] + words[i + 1:]))
        depth += 1
    return out


def pick(variant_results: Dict[str, List[dict]]) -> Optional[Tuple[str, List[dict]]]:
    """The variant whose results include a title that reads exactly like it
    (normalized title equals or starts with the variant). Ties go to the
    better-known title."""
    best = None
    for variant, items in variant_results.items():
        v = normalize_text(variant)
        if not v:
            continue
        for rank, item in enumerate(items[:5]):
            t = normalize_text(_title(item))
            if t == v:
                score = 2.0
            elif t.startswith(v + ' '):
                score = 1.0
            else:
                continue
            key = (score, float(item.get('vote_count') or 0), float(item.get('popularity') or 0), -rank)
            if best is None or key > best[0]:
                best = (key, variant, items)
            break
    return (best[1], best[2]) if best else None
