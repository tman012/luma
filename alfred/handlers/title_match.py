"""Shared title-matching utility for voice command handlers.

Scores TMDb/Plex search results against a spoken query to pick the
best match instead of blindly taking the first result.

Key heuristics:
  - Exact title match (case-insensitive) scores highest
  - Prefer shorter titles over longer ones (less noise)
  - Popularity matters — well-known titles beat obscure ones
  - When no year is specified, prefer the original/earliest release
  - Penalize titles that only partially overlap with the query
"""

import math
import re
from typing import Any, Dict, List, Optional


def _normalize(s: str) -> str:
    """Lowercase, strip 'the', collapse whitespace."""
    s = re.sub(r'^the\s+', '', s.lower().strip())
    return re.sub(r'\s+', ' ', s)


def score_result(query: str, result: Dict[str, Any]) -> float:
    """Score a single search result against the spoken query.

    result must have 'title' (str) and optionally:
      - 'year' (str or int)
      - 'release_date' / 'first_air_date' (str, YYYY-MM-DD)
      - 'popularity' (float, TMDb popularity — typically 0-500+)
      - 'vote_count' (int, TMDb vote count)

    Returns a float score (higher = better match).
    """
    title = result.get('title') or result.get('name') or ''
    q = _normalize(query)
    t = _normalize(title)

    score = 0.0

    # --- Exact match ---
    if t == q:
        score += 100
    # --- Title starts with query ---
    elif t.startswith(q):
        score += 60
    # --- Query starts with title (e.g. query="the lion king" title="Lion King") ---
    elif q.startswith(t):
        score += 55
    # --- Query is contained in title ---
    elif q in t:
        score += 30
    # --- Title is contained in query ---
    elif t in q:
        score += 25
    # --- Some word overlap ---
    else:
        q_words = set(q.split())
        t_words = set(t.split())
        overlap = len(q_words & t_words)
        if overlap:
            score += overlap * 5

    # --- Penalize extra words in the title (noise) ---
    # "The Lion King" vs "Mufasa: The Lion King" — prefer shorter
    q_word_count = len(q.split())
    t_word_count = len(t.split())
    extra_words = max(0, t_word_count - q_word_count)
    score -= extra_words * 3

    # --- Popularity bonus (TMDb popularity / vote_count) ---
    # Use log scale so huge popularity values don't overwhelm title match.
    # A blockbuster (popularity ~100, votes ~10000) gets ~+15 points.
    # An obscure title (popularity ~2, votes ~20) gets ~+3 points.
    popularity = float(result.get('popularity', 0) or 0)
    vote_count = int(result.get('vote_count', 0) or 0)
    if popularity > 0:
        score += min(15, math.log(1 + popularity) * 2.5)
    if vote_count > 0:
        score += min(10, math.log(1 + vote_count) * 1.0)

    # --- Year preference: favor older/original when no year in query ---
    year = 0
    year_raw = result.get('year') or ''
    if year_raw:
        try:
            year = int(str(year_raw)[:4])
        except (ValueError, TypeError):
            pass
    if not year:
        for key in ('release_date', 'first_air_date'):
            val = result.get(key, '')
            if val and len(str(val)) >= 4:
                try:
                    year = int(str(val)[:4])
                    break
                except (ValueError, TypeError):
                    pass

    if year:
        # Slight bonus for older releases — originals tend to be what people mean
        # when they say a bare title without a year qualifier.
        # Scale: 2025 → 0 bonus, 1994 → +3.1 bonus, 1960 → +6.5
        score += max(0, (2025 - year) * 0.1)

    return score


def best_match(query: str, results: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pick the best-matching result from a list. Returns None if empty."""
    if not results:
        return None
    if len(results) == 1:
        return results[0]

    scored = [(score_result(query, r), r) for r in results]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]
