"""Match a TV library folder to a TMDB show (Phase 1 scan).

A top-level folder in a TV library path is one show, whatever its naming.
* An id tag decides: {tmdb-N} / [tmdbid-N] (checked with /tv/{id}),
  {tvdb-N} / [tvdbid-N] and [imdbid-tt...] / {imdb-tt...} (through /find).
* Otherwise /search/tv by the folder's title (and year, when it has one).
  Accepted only when exact: with a year, exactly one result whose normalized
  title (or original title) and year both equal the folder's; without a year,
  exactly one result whose normalized title equals it. Anything else is
  unmatched with the top 5 results as candidates.

Pure logic plus TMDB reads: nothing here touches the file system.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from app import tv_tmdb

TOP_N = 5

_TAG_RE = re.compile(
    r'[\[{]\s*(tmdb|tmdbid|tvdb|tvdbid|imdb|imdbid)\s*[-=:]\s*(tt\d{5,10}|\d{1,10})\s*[\]}]', re.I)
_YEAR_TAIL_RE = re.compile(r'[\s._-]*[(\[]?((?:19|20)\d{2})[)\]]?\s*$')


def normalize(title: Any) -> str:
    """Casefolded, accents and punctuation gone, '&' read as 'and'."""
    s = unicodedata.normalize('NFKD', str(title or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c)).casefold()
    s = s.replace('&', ' and ').replace("'", '').replace('\u2019', '')
    s = re.sub(r'[^0-9a-z]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def parse_folder(name: str) -> Dict[str, Any]:
    """{'title', 'year', 'ids': {'tmdb'|'tvdb'|'imdb': value}} from a folder name."""
    ids: Dict[str, Any] = {}
    for kind, val in _TAG_RE.findall(name or ''):
        k = kind.lower().replace('id', '') if kind.lower() != 'imdbid' else 'imdb'
        k = {'tmdb': 'tmdb', 'tvdb': 'tvdb', 'imdb': 'imdb'}.get(k, k)
        if k == 'imdb':
            if val.lower().startswith('tt'):
                ids.setdefault('imdb', val.lower())
        elif val.isdigit() and int(val) > 0:
            ids.setdefault(k, int(val))
    rest = _TAG_RE.sub(' ', name or '')
    rest = re.sub(r'\s+', ' ', rest).strip(' ._-')
    year = None
    m = _YEAR_TAIL_RE.search(rest)
    if m and m.start() > 0:
        year = int(m.group(1))
        rest = rest[:m.start()]
    title = re.sub(r'[._]+', ' ', rest).strip(' -')
    return {'title': title, 'year': year, 'ids': ids}


def _exact(results: List[Dict[str, Any]], title: str, year: Optional[int]) -> Optional[Dict[str, Any]]:
    want = normalize(title)
    if not want:
        return None
    hits = [r for r in results
            if want in (normalize(r.get('title')), normalize(r.get('original_title')))
            and (year is None or r.get('year') == year)]
    return hits[0] if len(hits) == 1 else None


def match(folder: str) -> Dict[str, Any]:
    """{'status': 'matched', 'tmdb_id', 'via', 'guess'} or
    {'status': 'unmatched', 'candidates': [...], 'reason', 'guess'}.
    Raises tv_tmdb.TmdbError when TMDB can't be asked."""
    g = parse_folder(folder)
    guess = {'title': g['title'], 'year': g['year']}
    ids = g['ids']
    if 'tmdb' in ids:
        data = tv_tmdb.show(ids['tmdb'])
        if data:
            return {'status': 'matched', 'tmdb_id': ids['tmdb'], 'via': 'tmdb', 'guess': guess}
    for k, source in (('tvdb', 'tvdb_id'), ('imdb', 'imdb_id')):
        if k in ids:
            found = tv_tmdb.find(str(ids[k]), source)
            if len(found) == 1:
                return {'status': 'matched', 'tmdb_id': found[0]['tmdb_id'], 'via': k, 'guess': guess}
    if not g['title']:
        return {'status': 'unmatched', 'candidates': [], 'reason': 'no_title', 'guess': guess}
    results = tv_tmdb.search(g['title'], g['year'])
    hit = _exact(results, g['title'], g['year'])
    if hit:
        return {'status': 'matched', 'tmdb_id': hit['tmdb_id'], 'via': 'search', 'guess': guess}
    if g['year'] is not None and not results:
        results = tv_tmdb.search(g['title'])          # candidates only: never auto-accepted
    reason = 'no_results' if not results else 'not_exact'
    return {'status': 'unmatched', 'candidates': results[:TOP_N], 'reason': reason, 'guess': guess}


def show_fields(data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """series columns from a /tv/{id} answer, and its aliases."""
    ext = data.get('external_ids') if isinstance(data.get('external_ids'), dict) else {}
    alt = data.get('alternative_titles') if isinstance(data.get('alternative_titles'), dict) else {}
    aliases = sorted({str(a.get('title')) for a in alt.get('results') or [] if isinstance(a, dict) and a.get('title')})
    nets = [n.get('name') for n in data.get('networks') or [] if isinstance(n, dict) and n.get('name')]
    tvdb = ext.get('tvdb_id')
    first = str(data.get('first_air_date') or '')
    return {
        'title': str(data.get('name') or data.get('original_name') or ''),
        'original_title': str(data.get('original_name') or ''),
        'year': int(first[:4]) if first[:4].isdigit() else None,
        'tvdb_id': tvdb if isinstance(tvdb, int) and not isinstance(tvdb, bool) else None,
        'imdb_id': ext.get('imdb_id') if isinstance(ext.get('imdb_id'), str) else None,
        'status': str(data.get('status') or '') or None,
        'network': nets[0] if nets else None,
        'overview': str(data.get('overview') or '')[:2000],
        'poster_path': data.get('poster_path') if isinstance(data.get('poster_path'), str) else None,
        'backdrop_path': data.get('backdrop_path') if isinstance(data.get('backdrop_path'), str) else None,
        'original_language': data.get('original_language') if isinstance(data.get('original_language'), str) else None,
    }, aliases[:50]


def season_numbers(data: Dict[str, Any]) -> List[int]:
    out = []
    for s in data.get('seasons') or []:
        n = s.get('season_number') if isinstance(s, dict) else None
        if isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 2999:  # some daily shows number seasons by year
            out.append(n)
    return sorted(set(out))
