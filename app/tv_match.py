"""Match a TV library folder to a TMDB show (TV Import).

A top-level folder in a TV library path is one show, whatever its naming.

* An id tag decides (tier "id"): {tmdb-N} / [tmdbid-N] (checked with
  /tv/{id}), {tvdb-N} / [tvdbid-N] and [imdbid-tt...] / {imdb-tt...}
  (through /find). Luma's own earlier mapping for a folder is the other "id"
  source; tv_scan never re-guesses those.
* Otherwise /search/tv by the folder's title (with the year when the folder
  or, failing that, the earliest year in its file names has one). Empty:
  again without the year, then on the part before ':' (' - ' reads as ':').
  Every result is scored (score()): title (exact, original or alternative
  +60, word prefix +35, fuzzy +25), year (+25 / +10 / -40), a country tag
  ((US), (UK)...) against origin_country (+15 / -30), popularity (0..15) and
  TMDB's order (a small tie-break).
* Tiers: "high" (imported without asking) when an exact title leads by 15 or
  more with a score of HIGH (HIGH_NO_YEAR when no year is known anywhere: the
  year can't add its 25). Same-name rivals (another exact title the year and
  country don't rule out) auto-pick only when the files on disk positively fit
  the pick (their S/E exist in its seasons, the highest season exists, file
  years aren't before its first air date) AND contradict every rival; the pick
  must also be the first exact title in TMDB's own order. Popularity never
  decides (PM): it only ranks.
  "likely": a best guess exists (pre-selected in review: the candidate the
  files fit best, then the score). "none": no results.

Pure logic plus TMDB reads: nothing here touches the file system (the
caller passes file hints from its read-only listing).
"""

from __future__ import annotations

import difflib
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from app import tv_tmdb

TOP_N = 5
DETAILS_FOR_TOP = 2             # /tv/{id} (alternative titles, seasons) for the top 2 when close
MAX_DETAILS = 6                 # ... and for same-name rivals, at most this many per folder

W_EXACT, W_PREFIX, W_FUZZY = 60, 35, 25
W_YEAR, W_YEAR_NEAR, W_YEAR_OFF = 25, 10, -40
W_COUNTRY, W_COUNTRY_OFF = 15, -30
W_POP = 15
HIGH, GAP = 80, 15
HIGH_NO_YEAR = 70               # PM: 70 holds while the adversarial set and the fixture give 0 wrong picks at 70
FILE_COVERAGE = 0.9
NEAR_VOTES = 2                  # a near name with at least half the best's votes is comparable

_TAG_RE = re.compile(
    r'[\[{]\s*(tmdb|tmdbid|tvdb|tvdbid|imdb|imdbid)\s*[-=:]\s*(tt\d{5,10}|\d{1,10})\s*[\]}]', re.I)
_YEAR_TAIL_RE = re.compile(r'[\s._-]*[(\[]?((?:19|20)\d{2})[)\]]?\s*$')
COUNTRIES = {'US': 'US', 'USA': 'US', 'UK': 'GB', 'GB': 'GB', 'AU': 'AU', 'NZ': 'NZ', 'CA': 'CA', 'IE': 'IE',
             'DE': 'DE', 'FR': 'FR', 'NL': 'NL', 'SE': 'SE', 'DK': 'DK', 'NO': 'NO', 'JP': 'JP', 'KR': 'KR',
             'IN': 'IN', 'ES': 'ES', 'IT': 'IT', 'BE': 'BE', 'MX': 'MX', 'BR': 'BR'}
_COUNTRY_RE = re.compile(r'[\s._-]*[(\[](' + '|'.join(COUNTRIES) + r')[)\]]', re.I)


def normalize(title: Any) -> str:
    """Casefolded, accents and punctuation gone ('#', '!', '*', '…', quotes),
    '&' read as 'and'."""
    s = unicodedata.normalize('NFKD', str(title or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c)).casefold()
    s = s.replace('&', ' and ')
    s = re.sub(r"['\u2018\u2019*#!?\u2026]", '', s)
    s = re.sub(r'[^0-9a-z]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _colons(s: str) -> str:
    """' - ', '- ' and ' -' between words read as ':'."""
    s = re.sub(r'\s+-\s+|(?<=\w)-\s+|\s+-(?=\w)', ': ', s)
    return re.sub(r'\s*:\s*', ': ', s).strip(' :')


def parse_folder(name: str) -> Dict[str, Any]:
    """{'title', 'year', 'country', 'ids': {'tmdb'|'tvdb'|'imdb': value}} from a folder name."""
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
    year, country = None, None
    for _ in range(2):                  # 'Name (US) (2005)' and 'Name (2005) (US)'
        m = _YEAR_TAIL_RE.search(rest)
        if year is None and m and m.start() > 0:
            year = int(m.group(1))
            rest = rest[:m.start()]
            continue
        m = _COUNTRY_RE.search(rest)
        if country is None and m and m.start() > 0 and not rest[m.end():].strip(' ._-'):
            country = COUNTRIES[m.group(1).upper()]
            rest = rest[:m.start()]
    title = re.sub(r'(?<!\.)[._](?!\.)', ' ', rest) if ' ' not in rest else rest
    title = _colons(re.sub(r'\s+', ' ', title).strip(' -'))
    return {'title': title, 'year': year, 'country': country, 'ids': ids}


def pre_colon(title: str) -> str:
    return title.split(':', 1)[0].strip() if ':' in title else ''


# --------------------------------------------------------------------------- file hints

@dataclass
class Hints:
    """What the files in a show folder say: parsed (season, episode) pairs,
    the highest season, and the earliest year in a file name."""
    eps: Set[Tuple[int, int]] = field(default_factory=set)
    max_season: Optional[int] = None
    file_year: Optional[int] = None
    files: int = 0
    numbered: List[Tuple[int, List[int]]] = field(default_factory=list)   # every file's (season, episodes)


_FILE_YEAR_RE = re.compile(r'\((19\d{2}|20\d{2})\)')      # 'Archer (2009) - S14E09': never a bare '1923 - S01E01'


def file_hints(rel_paths: Iterable[str]) -> Hints:
    from app import tv_release
    h = Hints()
    for rel in rel_paths:
        name = rel.replace('\\', '/').rsplit('/', 1)[-1]
        h.files += 1
        r = tv_release.parse_file(name, rel)
        if r.kind in ('episode', 'multi') and r.season is not None and r.episodes:
            h.numbered.append((r.season, list(r.episodes)))
        if r.kind in ('episode', 'multi') and r.season is not None and r.season > 0:
            for e in r.episodes:
                h.eps.add((r.season, e))
            if r.season < 1000:
                h.max_season = max(h.max_season or 0, r.season)
        m = _FILE_YEAR_RE.search(name)
        if m:
            y = int(m.group(1))
            h.file_year = y if h.file_year is None else min(h.file_year, y)
    return h


# --------------------------------------------------------------------------- scoring

def _titles(c: Dict[str, Any], details: Optional[Dict[str, Any]]) -> List[str]:
    out = [c.get('title'), c.get('original_title')] + list(c.get('aliases') or [])
    if details:
        alt = details.get('alternative_titles') if isinstance(details.get('alternative_titles'), dict) else {}
        out += [a.get('title') for a in alt.get('results') or [] if isinstance(a, dict)]
    return [n for n in {normalize(t) for t in out if t} if n]


def _word_prefix(a: str, b: str) -> bool:
    return bool(a and b) and a != b and (b.startswith(a + ' ') or a.startswith(b + ' '))


def title_score(query: str, c: Dict[str, Any], details: Optional[Dict[str, Any]] = None) -> int:
    q = normalize(query)
    if not q:
        return 0
    names = _titles(c, details)
    if q in names:
        return W_EXACT
    if any(_word_prefix(q, n) for n in names):
        return W_PREFIX
    if any(difflib.SequenceMatcher(None, q, n).ratio() >= 0.9 for n in names):
        return W_FUZZY
    return 0


def _year_score(year: Optional[int], c_year: Optional[int]) -> int:
    if year is None or c_year is None:
        return 0
    d = abs(year - c_year)
    return W_YEAR if d == 0 else W_YEAR_NEAR if d == 1 else W_YEAR_OFF


def _countries(c: Dict[str, Any], details: Optional[Dict[str, Any]]) -> List[str]:
    v = (details or {}).get('origin_country') or c.get('origin_country') or []
    return [str(x).upper() for x in v if isinstance(x, str)]


def _country_score(country: Optional[str], c: Dict[str, Any], details: Optional[Dict[str, Any]]) -> int:
    have = _countries(c, details)
    if not country or not have:
        return 0
    return W_COUNTRY if country in have else W_COUNTRY_OFF


def score(results: List[Dict[str, Any]], query: Dict[str, Any],
          details: Optional[Dict[int, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """[{'c': result, 'title', 'year', 'country', 'pop', 'total', 'order'}],
    best first. *query*: {'title', 'pre', 'year', 'country'}."""
    details = details or {}
    rows = []
    for i, c in enumerate(results):
        d = details.get(c['tmdb_id'])
        t = title_score(query['title'], c, d)
        if query.get('pre'):
            t = max(t, min(title_score(query['pre'], c, d), W_PREFIX))   # the short title alone: a prefix
        rows.append({'c': c, 'title': t, 'year': _year_score(query.get('year'), c.get('year')),
                     'country': _country_score(query.get('country'), c, d), 'order': i})
    # No exact title anywhere, but exactly one result whose main title (before
    # ':') is the folder's ('Super Pumped' -> 'Super Pumped: The Battle for
    # Uber', an anthology season): that one reads as exact.
    q = normalize(query['title'])
    if q and not any(r['title'] >= W_EXACT for r in rows):
        main = [r for r in rows if any(normalize(pre_colon(t)) == q for t in (r['c'].get('title'), r['c'].get('original_title')) if t)]
        if len(main) == 1:
            main[0]['title'], main[0]['main'] = W_EXACT, True
    # popularity ranks results against those with the same title score (the
    # most-voted exact title gets the full 15 even if a prefix hit is bigger)
    top: Dict[int, int] = {}
    for r in rows:
        top[r['title']] = max(top.get(r['title'], 0), max(int(r['c'].get('vote_count') or 0), 0))
    for r in rows:
        v, t = max(int(r['c'].get('vote_count') or 0), 0), top.get(r['title'], 0)
        r['pop'] = 0.0 if r['title'] <= 0 else float(W_POP) if v >= t else round(W_POP * math.log1p(v) / math.log1p(t), 2)
        r['total'] = r['title'] + r['year'] + r['country'] + r['pop'] - 0.25 * r['order']
    rows.sort(key=lambda r: (-r['total'], r['order']))
    return rows


# --------------------------------------------------------------------------- files

def season_layout(details: Optional[Dict[str, Any]]) -> Dict[int, int]:
    out = {}
    for s in (details or {}).get('seasons') or []:
        if isinstance(s, dict) and isinstance(s.get('season_number'), int) and s['season_number'] > 0:
            out[s['season_number']] = int(s.get('episode_count') or 0)
    return out


def file_fit(h: Optional[Hints], details: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """How well the files fit a candidate: {'coverage', 'seasons_ok', 'year_ok', 'adjust'};
    None when there is nothing to compare."""
    if h is None or not details or (not h.eps and h.file_year is None):
        return None
    layout = season_layout(details)
    cov = (sum(1 for s, e in h.eps if 0 < e <= layout.get(s, 0)) / len(h.eps)) if h.eps else None
    seasons_ok = h.max_season is None or not layout or h.max_season <= max(layout)
    first = str(details.get('first_air_date') or '')[:4]
    year_ok = not (h.file_year and first.isdigit() and h.file_year < int(first) - 1)
    adjust = 30 * (cov if cov is not None else 0.5) - (0 if seasons_ok else 20) - (0 if year_ok else 40)
    # how many of the files' seasons end where the candidate's season ends (ranks pre-picks only)
    last: Dict[int, int] = {}
    for se, e in h.eps:
        last[se] = max(last.get(se, 0), e)
    tight = (sum(1 for se, e in last.items() if layout.get(se) == e) / len(last)) if last else 0.0
    return {'coverage': cov, 'seasons_ok': seasons_ok, 'year_ok': year_ok, 'adjust': adjust, 'tight': tight}


def fits(fit: Optional[Dict[str, Any]]) -> bool:
    """The files positively fit: their S/E exist (FILE_COVERAGE), their highest season exists, no file year before it."""
    return bool(fit) and fit['coverage'] is not None and fit['coverage'] >= FILE_COVERAGE and fit['seasons_ok'] and fit['year_ok']


def contradicts(fit: Optional[Dict[str, Any]]) -> bool:
    """The files rule the candidate out: S/E it doesn't have, a season past its last, or a file year before it.
    Unknown (no details, nothing to compare) never contradicts."""
    return bool(fit) and ((fit['coverage'] is not None and fit['coverage'] < FILE_COVERAGE)
                          or not fit['seasons_ok'] or not fit['year_ok'])


def _fit_key(fit: Optional[Dict[str, Any]]) -> Tuple[int, float, float]:
    if not fit:
        return (0, 0.0, 0.0)
    return (1 if fits(fit) else -1 if contradicts(fit) else 0, fit['coverage'] or 0.0, fit['tight'])


# --------------------------------------------------------------------------- match

def _guess(g: Dict[str, Any], year: Optional[int]) -> Dict[str, Any]:
    return {'title': g['title'], 'year': year, 'country': g.get('country')}


def _ids(g: Dict[str, Any], guess: Dict[str, Any], show: Callable) -> Optional[Dict[str, Any]]:
    ids = g['ids']
    if 'tmdb' in ids:
        data = show(ids['tmdb'])
        if data:
            return {'status': 'matched', 'tier': 'id', 'tmdb_id': ids['tmdb'], 'pick': ids['tmdb'], 'via': 'tmdb',
                    'guess': guess, 'candidates': [], 'details': data}
    for k, source in (('tvdb', 'tvdb_id'), ('imdb', 'imdb_id')):
        if k in ids:
            found = tv_tmdb.find(str(ids[k]), source)
            if len(found) > 1:      # several TV results: the one whose title is the folder's
                same = [f for f in found if title_score(g['title'], f) >= W_EXACT]
                found = same if len(same) == 1 else found
            if len(found) == 1:
                tid = found[0]['tmdb_id']
                return {'status': 'matched', 'tier': 'id', 'tmdb_id': tid, 'pick': tid, 'via': k,
                        'guess': guess, 'candidates': [], 'details': None}
    return None


def _search(g: Dict[str, Any], year: Optional[int], search: Callable) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Results and the query they answer: title (+year), title, then the part before ':'."""
    title, pre = g['title'], pre_colon(g['title'])
    q = {'title': title, 'pre': pre, 'year': year, 'country': g.get('country')}
    tries = [(title, year)] + ([(title, None)] if year else []) + ([(pre, None)] if pre else [])
    seen: Dict[int, Dict[str, Any]] = {}
    for i, (t, y) in enumerate(tries):
        res = search(t, y) if y else search(t)
        for r in res:
            seen.setdefault(r['tmdb_id'], r)
        if seen and (i > 0 or max((title_score(title, r) for r in res), default=0) >= W_EXACT):
            break           # an exact title on the first try, or anything at all on a retry
    return list(seen.values()), q


def match(folder: str, hints: Optional[Hints] = None, *, search: Optional[Callable] = None,
          show: Optional[Callable] = None) -> Dict[str, Any]:
    """{'status': 'matched'|'unmatched', 'tier': 'id'|'high'|'likely'|'none',
    'tmdb_id' (matched), 'pick' (best guess or None), 'candidates' (top 5),
    'reason', 'guess', 'via', 'details' (the pick's /tv/{id} when fetched)}.
    Raises tv_tmdb.TmdbError when TMDB can't be asked."""
    search = search or tv_tmdb.search
    show = show or tv_tmdb.show
    g = parse_folder(folder)
    year = g['year'] if g['year'] is not None else (hints.file_year if hints else None)
    guess = _guess(g, year)
    hit = _ids(g, guess, show)
    if hit:
        return hit
    if not g['title']:
        return {'status': 'unmatched', 'tier': 'none', 'pick': None, 'candidates': [], 'reason': 'no_title',
                'guess': guess, 'via': None, 'details': None}
    results, q = _search(g, year, search)
    if not results:
        return {'status': 'unmatched', 'tier': 'none', 'pick': None, 'candidates': [], 'reason': 'no_results',
                'guess': guess, 'via': None, 'details': None}
    details: Dict[int, Dict[str, Any]] = {}
    rows = score(results, q)
    close = len(rows) > 1 and rows[0]['total'] - rows[1]['total'] < GAP
    want = [r for r in rows[:DETAILS_FOR_TOP] if close or r['title'] < W_EXACT]
    if rows[0]['title'] >= W_EXACT:     # same-name rivals: their seasons decide against the files
        want += [r for r in rows[1:] if r['title'] >= W_EXACT and r not in want]
        if hints is not None and hints.eps:   # ... and near names the files might fit as well (Cosmos)
            want += [r for r in _near(rows) if r not in want]
    for r in want[:MAX_DETAILS]:
        d = show(r['c']['tmdb_id'])
        if d:
            details[r['c']['tmdb_id']] = d
    if details:
        rows = score(results, q, details)
    tier, why = decide(rows, q, hints, details)
    best = rows[0]['c'] if tier == 'high' else prepick(rows, hints, details)['c']
    # same-name shows the files don't single out: review pre-picks the best guess but leaves the row unchecked
    unclear = tier != 'high' and why in SAME_NAME and not _sole_fit(rows, hints, details, best['tmdb_id'])
    # ... and so does a pick nothing backs: neither an exact title nor files that fit it (WWE Raw -> Raw Classics)
    unclear = unclear or (tier != 'high' and not backed(rows, hints, details, best['tmdb_id']))
    if tier == 'high' and best['tmdb_id'] not in details:
        d = show(best['tmdb_id'])
        if d:
            details[best['tmdb_id']] = d
    cands = [r['c'] for r in rows[:TOP_N]]
    out = {'guess': guess, 'candidates': cands, 'pick': best['tmdb_id'], 'details': details.get(best['tmdb_id']),
           'why': why, 'via': 'search' if tier == 'high' else None, 'tier': tier}
    if tier == 'high':
        return {**out, 'status': 'matched', 'tmdb_id': best['tmdb_id']}
    return {**out, 'status': 'unmatched', 'reason': 'unclear' if unclear else 'not_exact'}


def decide(rows: List[Dict[str, Any]], q: Dict[str, Any], hints: Optional[Hints],
           details: Dict[int, Dict[str, Any]]) -> Tuple[str, str]:
    """('high'|'likely', why) for scored *rows* (best first)."""
    best = rows[0]
    if best['title'] < W_EXACT:
        return 'likely', 'no exact title'
    need = HIGH if q.get('year') is not None else HIGH_NO_YEAR
    # Same-name rivals: another exact title the year and country don't rule out.
    rivals = [r for r in rows[1:] if r['title'] >= W_EXACT and r['year'] >= best['year']
              and r['country'] >= min(best['country'], 0)]
    exact_order = [r for r in sorted(rows, key=lambda r: r['order']) if r['title'] >= W_EXACT
                   and r['year'] >= 0 and r['country'] >= 0]
    first_exact = exact_order[0]['c']['tmdb_id'] if exact_order else None
    gap = best['total'] - (rows[1]['total'] if len(rows) > 1 else 0)
    if not rivals:
        # a near name ('Cosmos: A Spacetime Odyssey') with as many votes that the files fit too: review
        if hints is not None and any(fits(file_fit(hints, details.get(r['c']['tmdb_id']))) for r in _near(rows)):
            return 'likely', 'files fit another show'
        if best['total'] >= need and gap >= GAP:
            return 'high', 'clear'
        fit = file_fit(hints, details.get(best['c']['tmdb_id']))
        if fit and fit['coverage'] is not None and fit['coverage'] >= FILE_COVERAGE and best['total'] >= need:
            return 'high', 'files'
        return 'likely', 'close'
    if best['c']['tmdb_id'] != first_exact:
        return 'likely', 'not first in TMDB order'
    # The files must fit the pick and rule out every rival (a rival without details can't be ruled out).
    mine = file_fit(hints, details.get(best['c']['tmdb_id']))
    if fits(mine) and all(contradicts(file_fit(hints, details.get(r['c']['tmdb_id']))) for r in rivals):
        return 'high', 'files'
    if q.get('year') is not None and any(r['c'].get('year') == best['c'].get('year') for r in rivals):
        return 'likely', 'same title and year'      # the folder's year can't tell them apart: Cheat (2019)
    return 'likely', 'same title'


SAME_NAME = ('same title', 'same title and year', 'not first in TMDB order', 'files fit another show')


def backed(rows, hints, details, tid) -> bool:
    """Review may pre-check pick *tid* only when its title is exact or the files back it: they fit it
    and no other top result, and its seasons end where the files do (a few episodes of season 1 fit
    nearly anything: WWE WrestleMania -> Road to WrestleMania)."""
    row = next((r for r in rows if r['c']['tmdb_id'] == tid), None)
    if row is not None and row['title'] >= W_EXACT:
        return True
    if hints is None:
        return False
    fit = file_fit(hints, details.get(tid))
    return fits(fit) and fit['tight'] >= 1.0 and _sole_fit(rows, hints, details, tid)


def _sole_fit(rows, hints, details, tid) -> bool:
    """*tid* is the only top result (with details) the files fit."""
    if hints is None:
        return False
    fit = [r['c']['tmdb_id'] for r in rows[:TOP_N] if fits(file_fit(hints, details.get(r['c']['tmdb_id'])))]
    return fit == [tid]


def _votes(r: Dict[str, Any]) -> int:
    return max(int(r['c'].get('vote_count') or 0), 0)


def _near(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Top results with a partial title (prefix, main title, fuzzy) and comparable or more votes than the best:
    votes can only send a folder to review here, never pick a show."""
    best = rows[0]
    if not _votes(best):                  # no vote counts: nothing says the near name is comparable
        return []
    return [r for r in rows[1:TOP_N] if 0 < r['title'] < W_EXACT and _votes(r) * NEAR_VOTES >= _votes(best)]


def prepick(rows: List[Dict[str, Any]], hints: Optional[Hints], details: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Review's best guess: among the top results with the best title score that the year and country don't
    rule out, the one the files fit best (fits, coverage, seasons that end where its seasons end); the score
    breaks ties, so without files it's the top row."""
    best = rows[0]
    if hints is None or not details:
        return best
    pool = [r for r in rows[:TOP_N] if r['title'] >= best['title'] and r['year'] >= min(best['year'], 0)
            and r['country'] >= min(best['country'], 0)]
    keyed = [(_fit_key(file_fit(hints, details.get(r['c']['tmdb_id']))), -i, r) for i, r in enumerate(pool)]
    return max(keyed, key=lambda k: (k[0], k[1]))[2] if keyed else best


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
