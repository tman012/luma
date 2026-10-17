"""Release and file names for TV: which episodes a name covers.

parse(name) understands
  S01E05, S01E05E06, S01E05-E07, S01E05-07, s1e5, 1x05, 1x05-1x06,
  S01, Season 1, S01.COMPLETE, S01-S03, Seasons 1-3,
  daily shows (2024.10.05), anime absolute numbers ([Group] Title - 1052),
plus PROPER/REPACK, resolution and the release group.

parse_file(name, rel_path) also uses a "Season N" folder for files that only
carry an episode number.

scope(name) is a port of the TV Sources popup's detectTvScope
(frontend useTvSourcesData.ts): {'pack_type': 'season'|'episode',
'pack_episode': n|None}. tests/fixtures/tv_release_names.tsv runs through both
so the server and the popup agree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

VIDEO_EXTS = ('.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.m2ts', '.webm', '.mpg', '.mpeg')
MAX_RANGE = 60          # an episode range wider than this is not a range


@dataclass
class Release:
    kind: str = 'unknown'                  # episode | multi | season | seasons | daily | absolute | unknown
    title: str = ''
    year: Optional[int] = None
    season: Optional[int] = None
    seasons: List[int] = field(default_factory=list)
    episodes: List[int] = field(default_factory=list)
    absolute: List[int] = field(default_factory=list)
    air_date: Optional[str] = None
    complete: bool = False
    proper: int = 0
    resolution: str = ''
    group: str = ''

    def summary(self) -> str:
        """Compact form used by the shared corpus."""
        if self.kind == 'episode':
            return f'ep S{self.season:02d}E{self.episodes[0]:02d}'
        if self.kind == 'multi':
            return f'multi S{self.season:02d}' + ''.join(f'E{e:02d}' for e in self.episodes)
        if self.kind == 'season':
            return f'season S{self.season:02d}'
        if self.kind == 'seasons':
            return 'seasons ' + ','.join(f'S{s:02d}' for s in self.seasons)
        if self.kind == 'daily':
            return f'daily {self.air_date}'
        if self.kind == 'absolute':
            return 'abs ' + ','.join(str(a) for a in self.absolute)
        return 'none'


_SEP = r'[ ._\-]'
_EXT_RE = re.compile(r'\.(' + '|'.join(e[1:] for e in VIDEO_EXTS) + r'|nfo|srt|sub|idx|ass|torrent)$', re.I)
_GROUP_TAG_RE = re.compile(r'^\s*(\[[^\]]{1,40}\]\s*)+')
_SXXEYY_RE = re.compile(r'(?<![a-z0-9])s(\d{1,3})' + _SEP + r'?e(\d{1,4})'
                        r'((?:' + _SEP + r'?-?' + _SEP + r'?e\d{1,4}|-\d{1,4}(?!\d|p\b))*)', re.I)
_MORE_EP_RE = re.compile(r'e(\d{1,4})|-(\d{1,4})', re.I)
_NXM_RE = re.compile(r'(?<![a-z0-9])(\d{1,2})x(\d{2,3})((?:-(?:\d{1,2}x)?\d{2,3})*)(?![0-9])', re.I)
_SEASONS_RE = re.compile(r'(?<![a-z0-9])(?:s(\d{1,2})' + _SEP + r'?-' + _SEP + r'?s?(\d{1,2})(?!' + _SEP + r'?e\d)'
                         r'|seasons?' + _SEP + r'?(\d{1,2})' + _SEP + r'?(?:-|to|' + _SEP + r')' + _SEP + r'?(\d{1,2}))'
                         r'(?![0-9])', re.I)
_SEASON_RE = re.compile(r'(?<![a-z0-9])(?:s(\d{1,3})(?![0-9]|' + _SEP + r'?e\d)|season' + _SEP + r'?(\d{1,3})(?![0-9]))', re.I)
_ANIME_SEASON_RE = re.compile(r'(?<![a-z0-9])s(\d{1,2})\s-\s(\d{1,4})(?:v\d)?(?=\s|\[|\(|$)', re.I)
_DAILY_RE = re.compile(r'(?<![0-9])((?:19|20)\d{2})' + _SEP + r'(\d{2})' + _SEP + r'(\d{2})(?![0-9])')
_ABS_RE = re.compile(r'\s-\s(\d{1,4})(?:v\d)?(?:\s*-\s*(\d{1,4})(?:v\d)?)?(?=\s|\[|\(|$|\.)')
_YEAR_RE = re.compile(r'(?<![0-9])((?:19|20)\d{2})(?![0-9])')
_PROPER_RE = re.compile(r'(?<![a-z])(proper|repack|rerip)(?![a-z])', re.I)
_RES_RE = re.compile(r'(?<![0-9])(2160|1080|720|576|480)[pi](?![a-z0-9])', re.I)
_GROUP_RE = re.compile(r'-([A-Za-z0-9]{2,20})(?:\[[^\]]*\])?$')
_COMPLETE_RE = re.compile(r'(?<![a-z])complete(?![a-z])', re.I)
_SEASON_DIR_RE = re.compile(r'^(?:season|series|staffel|saison)' + _SEP + r'?(\d{1,3})$|^s(\d{1,3})$', re.I)
_EP_ONLY_RE = re.compile(r'(?<![a-z0-9])(?:e|ep|episode)' + _SEP + r'?(\d{1,4})(?![0-9])', re.I)


def _strip(name: str) -> str:
    n = (name or '').strip()
    return _EXT_RE.sub('', n)


def _title_from(prefix: str) -> (str, Optional[int]):
    t = _GROUP_TAG_RE.sub('', prefix)
    t = re.sub(r'[._]+', ' ', t)
    t = re.sub(r'[\[\(]\s*[\]\)]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip(' -_[]()')
    year = None
    m = None
    for m in _YEAR_RE.finditer(t):
        pass
    if m and m.end() == len(t.rstrip(' )]')) or (m and t[m.end():].strip(' )]') == ''):
        rest = t[:m.start()].strip(' -([')
        if rest:                           # '1923' alone stays the title
            year, t = int(m.group(1)), rest
    return t.strip(), year


def _expand(first: int, tail: str) -> List[int]:
    eps = [first]
    for m in _MORE_EP_RE.finditer(tail or ''):
        n = int(m.group(1) or m.group(2))
        dash = m.group(2) is not None or tail[max(0, m.start() - 1):m.start() + 1].find('-') >= 0
        last = eps[-1]
        if dash:
            if last < n <= last + MAX_RANGE:
                eps.extend(range(last + 1, n + 1))
        elif n not in eps:
            eps.append(n)
    return sorted(set(eps))


def _common(r: Release, s: str) -> Release:
    r.proper = len(_PROPER_RE.findall(s))
    m = _RES_RE.search(s)
    r.resolution = (m.group(1) + 'p') if m else ''
    g = _GROUP_RE.search(s)
    r.group = g.group(1) if g and not re.fullmatch(r'\d+x\d+|[Ee]?\d+|\d{3,4}p', g.group(1)) else ''
    r.complete = bool(_COMPLETE_RE.search(s))
    return r


def parse(name: str) -> Release:
    s = _strip(name)
    r = Release()
    m = _SXXEYY_RE.search(s)
    if m:
        r.season = int(m.group(1))
        r.episodes = _expand(int(m.group(2)), m.group(3))
        r.kind = 'multi' if len(r.episodes) > 1 else 'episode'
        r.title, r.year = _title_from(s[:m.start()])
        return _common(r, s)
    m = _NXM_RE.search(s)
    if m and not _RES_RE.search(m.group(0)):
        r.season = int(m.group(1))
        tail = re.sub(r'\d{1,2}x', '', m.group(3) or '')
        r.episodes = _expand(int(m.group(2)), tail)
        r.kind = 'multi' if len(r.episodes) > 1 else 'episode'
        r.title, r.year = _title_from(s[:m.start()])
        return _common(r, s)
    m = _ANIME_SEASON_RE.search(s)
    if m:                                  # '[Group] Title S2 - 03'
        r.kind, r.season, r.episodes = 'episode', int(m.group(1)), [int(m.group(2))]
        r.title, r.year = _title_from(s[:m.start()])
        return _common(r, s)
    m = _SEASONS_RE.search(s)
    if m:
        a, b = (int(m.group(1)), int(m.group(2))) if m.group(1) else (int(m.group(3)), int(m.group(4)))
        if a < b <= a + 50:
            r.kind, r.seasons, r.season = 'seasons', list(range(a, b + 1)), a
            r.title, r.year = _title_from(s[:m.start()])
            return _common(r, s)
    m = _SEASON_RE.search(s)
    if m:
        r.season = int(m.group(1) or m.group(2))
        r.kind, r.seasons = 'season', [r.season]
        r.title, r.year = _title_from(s[:m.start()])
        return _common(r, s)
    m = _DAILY_RE.search(s)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            d = None
        if d:
            r.kind, r.air_date = 'daily', d.isoformat()
            r.title, r.year = _title_from(s[:m.start()])
            return _common(r, s)
    spaced = _GROUP_TAG_RE.sub(lambda g: g.group(0).replace('.', ' '), s)
    m = _ABS_RE.search(spaced)
    if m and not _RES_RE.search(spaced[m.start():m.end() + 1]):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a <= b <= a + MAX_RANGE:
            r.kind, r.absolute = 'absolute', list(range(a, b + 1))
            r.title, r.year = _title_from(spaced[:m.start()])
            return _common(r, s)
    r.title, r.year = _title_from(s)
    return _common(r, s)


def season_from_folder(rel_path: str) -> Optional[int]:
    """Season number from the nearest 'Season N' / 'Specials' folder in *rel_path*."""
    parts = [p for p in (rel_path or '').replace('\\', '/').split('/') if p][:-1]
    for p in reversed(parts):
        if p.strip().lower() == 'specials':
            return 0
        m = _SEASON_DIR_RE.match(p.strip())
        if m:
            return int(m.group(1) or m.group(2))
    return None


def parse_file(name: str, rel_path: str = '') -> Release:
    """parse() for a file inside a pack: a file with only an episode number
    ('E05', 'Episode 5', '05 - Title') takes the season of its folder or,
    failing that, of the pack's name in *rel_path*."""
    r = parse(name)
    if r.kind in ('episode', 'multi', 'daily'):
        return r
    folder_season = season_from_folder(rel_path)
    if folder_season is None:
        for part in reversed([p for p in (rel_path or '').replace('\\', '/').split('/') if p][:-1]):
            pr = parse(part)
            if pr.kind == 'season':
                folder_season = pr.season
                break
    if folder_season is None:
        return r
    s = _strip(name)
    m = _EP_ONLY_RE.search(s) or re.match(r'^\s*(\d{1,3})(?![0-9])\s*(?:[-._ ]|$)', s)
    if m:
        out = Release(kind='episode', season=folder_season, episodes=[int(m.group(1))])
        out.title = r.title
        return _common(out, s)
    return r


# --------------------------------------------------------------------------- scope (UI port)

_SC_RANGE_1 = re.compile(r'S\d{1,2}\s?E\d{1,3}\s?-\s?E?\d{1,3}', re.I)
_SC_RANGE_2 = re.compile(r'\bE\d{1,3}\s?-\s?E\d{1,3}\b', re.I)
_SC_SINGLE = re.compile(r'S\d{1,2}\s?E(\d{1,3})(?!\s?-\s?E?\d)', re.I)
_SC_NXM = re.compile(r'\b\d{1,2}x(\d{1,3})\b', re.I)


def scope(title: str) -> dict:
    """detectTvScope: {'pack_type', 'pack_episode'} exactly as the popup computes it."""
    t = title or ''
    if _SC_RANGE_1.search(t) or _SC_RANGE_2.search(t):
        return {'pack_type': 'season', 'pack_episode': None}
    m = _SC_SINGLE.search(t)
    if m:
        return {'pack_type': 'episode', 'pack_episode': int(m.group(1))}
    m = _SC_NXM.search(t)
    if m:
        return {'pack_type': 'episode', 'pack_episode': int(m.group(1))}
    return {'pack_type': 'season', 'pack_episode': None}
