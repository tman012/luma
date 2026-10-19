"""v185 (Taylor): Sonarr-style episode naming for TV imports, seeded from his Sonarr NamingConfig.

Settings > TV > Options > Episode naming (tv_settings 'naming'):
  rename_episodes, standard/daily/anime episode formats, series/season/specials folder formats,
  multi_episode_style, colon_replacement, replace_illegal.
Sonarr tokens: {Series Title} {Series TitleYear} {Series CleanTitle} {Series Year} {season:00}
{episode:00} {absolute:000} {Episode Title} {Episode CleanTitle} {Air-Date} {Air Date}
{Quality Full} {Quality Title} {Release Group}. A token's case sets the value's case ({series title},
{SERIES TITLE}); a '.' or '_' in it is the word separator ({Series.Title}).

Library rules (Ops, from /workspace/sonarr-naming): a colon becomes '-' with the following space kept
('Meltdown- Three Mile Island'); a show's existing season folder is reused, else its siblings' padding
style; the extension is kept; sidecars follow their video; nothing is ever overwritten; there are no
bulk renames (only files Luma imports get the new names).
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

STYLES = ('extend', 'duplicate', 'repeat', 'scene', 'range', 'prefixed_range')
COLONS = ('delete', 'dash', 'space_dash', 'space_dash_space', 'smart')
FORMATS = ('standard', 'daily', 'anime', 'series_folder', 'season_folder', 'specials_folder')
DEFAULTS: Dict[str, Any] = {        # Taylor's Sonarr NamingConfig (colon: what the library shows, Dash)
    'rename_episodes': True,
    'standard': '{Series Title} - S{season:00}E{episode:00} - {Episode Title}',
    'daily': '{Series Title} - {Air-Date} - {Episode Title} {Quality Full}',
    'anime': '{Series Title} - S{season:00}E{episode:00} - {Episode Title} {Quality Full}',
    'series_folder': '{Series Title}',
    'season_folder': 'Season {season}',
    'specials_folder': 'Specials',
    'multi_episode_style': 'prefixed_range',
    'colon_replacement': 'dash',
    'replace_illegal': True,
}
TOKENS = {
    'series title', 'series titleyear', 'series cleantitle', 'series year', 'season', 'episode', 'absolute',
    'episode title', 'episode cleantitle', 'air-date', 'air date', 'quality full', 'quality title', 'release group',
}
ALLOWED = {
    'standard': TOKENS - {'absolute'},
    'daily': TOKENS - {'absolute'},
    'anime': TOKENS,
    'series_folder': {'series title', 'series titleyear', 'series cleantitle', 'series year'},
    'season_folder': {'season'},
    'specials_folder': {'season'},
}
REQUIRED = {'standard': [{'season'}, {'episode'}], 'daily': [{'air-date', 'air date'}],
            'anime': [{'episode', 'absolute'}], 'series_folder': [{'series title', 'series titleyear', 'series cleantitle'}],
            'season_folder': [{'season'}], 'specials_folder': []}
FORMAT_MAX = 255
SIDECAR_EXTS = ('.srt', '.ass', '.ssa', '.sub', '.idx', '.vtt', '.sup', '.smi')
NAME_MAX_BYTES = 240

_TOKEN_RE = re.compile(r'\{([^{}]*)\}')
_SOURCE = {'web': 'WEBDL', 'webrip': 'WEBRip', 'bluray': 'Bluray', 'hdtv': 'HDTV', 'remux': 'Bluray'}
# Sonarr's 'Replace illegal characters' (FileNameBuilder): these become these
_ILLEGAL = {'\\': '+', '/': '+', '<': '', '>': '', '?': '!', '*': '-', '|': '', '"': ''}
_CTRL = re.compile(r'[\x00-\x1f\x7f]')


# ------------------------------------------------------------------ settings

def _key(tok: str) -> Tuple[str, str, str]:
    """(canonical name, separator, case) of a token body like 'Series.Title' or 'season:00'."""
    name, _, _spec = tok.partition(':')
    sep = '.' if '.' in name else '_' if '_' in name else ' '
    canon = re.sub(r'[._]', ' ', name).strip().lower()
    if canon in ('air date', 'air-date') or name.lower() in ('air.date', 'air_date'):
        canon = 'air-date' if '-' in name else 'air date'
    case = 'lower' if name == name.lower() and name != name.upper() else 'upper' if name == name.upper() and name != name.lower() else ''
    if canon in ('season', 'episode', 'absolute'):
        case = ''
    return canon, sep, case


def check_format(key: str, value: Any) -> Optional[str]:
    """None when *value* is a valid *key* format, else the error text."""
    if not isinstance(value, str) or not value.strip() or len(value) > FORMAT_MAX or _CTRL.search(value):
        return 'Enter a format'
    if value.strip().strip('.') == '':
        return 'Enter a format'
    if '/' in value or '\\' in value:
        return "A format can't contain / or \\"
    if value.count('{') != value.count('}') or re.search(r'\{[^}]*\{|\}[^{]*\}', re.sub(r'\{[^{}]*\}', 'x', value)):
        return 'Check the braces'
    used = set()
    for tok in _TOKEN_RE.findall(value):
        canon, _, _ = _key(tok)
        spec = tok.partition(':')[2]
        if canon not in ALLOWED[key]:
            return f'Unknown token {{{tok}}}'
        if spec and (canon not in ('season', 'episode', 'absolute') or not re.fullmatch(r'0{1,4}', spec)):
            return f'Unknown token {{{tok}}}'
        used.add(canon)
    for need in REQUIRED[key]:
        if not used & need:
            return 'Add ' + ' or '.join('{' + _pretty(n) + '}' for n in sorted(need))
    return None


def _pretty(canon: str) -> str:
    return {'season': 'season:00', 'episode': 'episode:00', 'absolute': 'absolute:000', 'air-date': 'Air-Date',
            'air date': 'Air Date'}.get(canon, canon.title().replace('Titleyear', 'TitleYear').replace('Cleantitle', 'CleanTitle'))


def normalize(raw: Any) -> Dict[str, Any]:
    """The stored naming with defaults for anything missing or invalid (an earlier build's
    '{title} ({year})' templates are not Sonarr formats: they start from Taylor's Sonarr ones)."""
    out = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return out
    for k in FORMATS:
        if k in raw and check_format(k, raw[k]) is None:
            out[k] = raw[k]
    for k in ('rename_episodes', 'replace_illegal'):
        if isinstance(raw.get(k), bool):
            out[k] = raw[k]
    if raw.get('multi_episode_style') in STYLES:
        out['multi_episode_style'] = raw['multi_episode_style']
    if raw.get('colon_replacement') in COLONS:
        out['colon_replacement'] = raw['colon_replacement']
    return out


def apply(current: Dict[str, Any], body: Any) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """(new naming, {field: error}) for a PUT {'naming': body}."""
    errors: Dict[str, str] = {}
    new = dict(normalize(current))
    if not isinstance(body, dict) or not body or set(body) - set(DEFAULTS):
        return new, {'naming': 'Invalid'}
    for k, v in body.items():
        if k in FORMATS:
            e = check_format(k, v)
            if e:
                errors[f'naming.{k}'] = e
            else:
                new[k] = v.strip()
        elif k in ('rename_episodes', 'replace_illegal'):
            if isinstance(v, bool):
                new[k] = v
            else:
                errors[f'naming.{k}'] = 'Invalid'
        elif k == 'multi_episode_style':
            if v in STYLES:
                new[k] = v
            else:
                errors[f'naming.{k}'] = 'Invalid'
        elif k == 'colon_replacement':
            if v in COLONS:
                new[k] = v
            else:
                errors[f'naming.{k}'] = 'Invalid'
    return new, errors


# ------------------------------------------------------------------ text rules

def colon(s: str, mode: str) -> str:
    if mode == 'delete':
        s = s.replace(':', '')
    elif mode == 'dash':
        s = s.replace(':', '-')
    elif mode == 'space_dash':
        s = re.sub(r'\s*:', ' -', s)
    elif mode == 'space_dash_space':
        s = re.sub(r'\s*:\s*', ' - ', s)
    else:                                       # smart: 'A: B' -> 'A - B', 'A:B' -> 'A-B'
        s = re.sub(r'\s*:\s+', ' - ', s).replace(':', '-')
    return re.sub(r'  +', ' ', s)


def clean_text(s: Any, cfg: Dict[str, Any]) -> str:
    s = _CTRL.sub('', s if isinstance(s, str) else '')
    s = colon(s, cfg.get('colon_replacement', 'dash'))
    if cfg.get('replace_illegal', True):
        for a, b in _ILLEGAL.items():
            s = s.replace(a, b)
    else:
        s = re.sub(r'[\\/<>?*|"]', '', s)
    return s


def clean_title(s: str) -> str:
    """Sonarr's CleanTitle: no punctuation but '&' becomes 'and'."""
    s = s.replace('&', 'and')
    s = re.sub(r"[^\w\s-]", '', s)
    return re.sub(r'\s+', ' ', s).strip()


def quality_full(quality: Optional[str], proper: bool = False, title: str = '') -> str:
    """'1080p-web' -> 'WEBDL-1080p' (a WEBRip release: 'WEBRip-1080p'), Sonarr's quality names."""
    if not quality or '-' not in quality:
        return ''
    res, src = quality.split('-', 1)
    if src == 'web' and re.search(r'web-?rip', title or '', re.I):
        src = 'webrip'
    word = _SOURCE.get(src, src.upper())
    res = 'SDTV' if res == 'sd' and src == 'hdtv' else 'DVD' if res == 'sd' else res
    if res in ('SDTV', 'DVD'):
        q = res if src in ('hdtv', 'bluray') else f'{word}-480p'
    else:
        q = f'{word}-{res}' + (' Remux' if src == 'remux' else '')
    return q + (' Proper' if proper else '')


def series_title(show: Dict[str, Any]) -> str:
    """The title as the library names it: the show's folder when it is the title plus a
    disambiguator ('Time (2021)', 'Your Honor (US)'), else the title."""
    title = str(show.get('title') or '').strip()
    folder = str(show.get('folder') or '').strip()
    if folder and title:
        base = lambda x: re.sub(r'[^a-z0-9]', '', x.lower())  # noqa: E731
        if base(folder).startswith(base(title)) and re.fullmatch(r'.*\((?:\d{4}|[A-Z]{2,3})\)', folder):
            return folder
    return title


def _episode_title(titles: List[str]) -> str:
    titles = [t for t in titles if t]
    if not titles:
        return ''
    bases = {re.sub(r'\s*(?:\(\d+\)|,?\s*Part\s+\d+)$', '', t, flags=re.I) for t in titles}
    if len(titles) > 1 and len(bases) == 1:
        return bases.pop()
    out: List[str] = []
    for t in titles:
        if t not in out:
            out.append(t)
    return ' + '.join(out)


# ------------------------------------------------------------------ rendering

def _cased(v: str, case: str, sep: str) -> str:
    if sep != ' ':                              # 'Meltdown- Three' -> 'Meltdown.Three' (no '-.' left behind)
        v = re.sub(r'\s+', sep, re.sub(r'\s*-\s+|\s+-\s*', ' ', v).strip())
    return v.lower() if case == 'lower' else v.upper() if case == 'upper' else v


def _num(n: Optional[int], spec: str) -> str:
    if n is None:
        return ''
    return str(n).zfill(len(spec)) if spec else str(n)


def _multi(nums: List[int], spec: str, style: str, prefix: str) -> str:
    """The episode token's value for a multi-episode file (the literal before it, e.g. 'E', is *prefix*)."""
    f = [_num(n, spec) for n in nums]
    if len(f) == 1:
        return f[0]
    if style == 'extend':
        return '-'.join(f)
    if style == 'repeat':
        return f[0] + ''.join(prefix + x for x in f[1:])
    if style == 'scene':
        return f[0] + ''.join('-' + prefix + x for x in f[1:])
    if style == 'range':
        return f'{f[0]}-{f[-1]}'
    return f'{f[0]}-{prefix}{f[-1]}'           # prefixed_range


def _tidy(s: str) -> str:
    """Empty tokens leave separators behind: 'A -  - B' -> 'A - B', trailing ' - ' or spaces go."""
    s = re.sub(r'\(\s*\)|\[\s*\]', '', s)
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'(?:\s-\s*){2,}', ' - ', s)
    s = re.sub(r'^[\s\-._]+|[\s\-_]+$', '', s)
    return s.strip().rstrip('.').strip()


def render(fmt: str, ctx: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    """One format with *ctx*: series, year, season, episodes (numbers), absolutes, titles, air_date,
    quality, proper, group, release (the release name, for WEBRip)."""
    style = cfg.get('multi_episode_style', 'prefixed_range')
    eps: List[int] = list(ctx.get('episodes') or [])
    if style == 'duplicate' and len(eps) > 1:
        m = re.search(r'S?\{season[^}]*\}[^{}]*\{episode[^}]*\}|\{episode[^}]*\}', fmt, re.I)
        if m:
            seg = m.group(0)
            parts = [render(seg, {**ctx, 'episodes': [n]}, {**cfg, 'multi_episode_style': 'extend'}) for n in eps]
            fmt = fmt[:m.start()] + '\x00' + fmt[m.end():]
            out = render(fmt, {**ctx, 'episodes': eps[:1]}, cfg)
            return out.replace('\x00', ' - '.join(parts))

    def value(m: re.Match) -> str:
        tok = m.group(1)
        canon, sep, case = _key(tok)
        spec = tok.partition(':')[2]
        title = clean_text(series_title(ctx), cfg) if canon.startswith('series') else ''
        if canon == 'series title':
            v = title
        elif canon == 'series titleyear':
            y = ctx.get('year')
            v = title if not y or re.search(r'\(\d{4}\)$', title) else f'{title} ({y})'
        elif canon == 'series cleantitle':
            v = clean_title(title)
        elif canon == 'series year':
            v = str(ctx.get('year') or '')
        elif canon == 'season':
            v = _num(ctx.get('season'), spec)
        elif canon == 'episode':
            prefix = fmt[m.start() - 1] if m.start() > 0 and fmt[m.start() - 1].isalpha() else ''
            v = _multi(eps, spec, style, prefix) if eps else ''
        elif canon == 'absolute':
            ab = [a for a in (ctx.get('absolutes') or []) if a]
            v = _multi(ab, spec, style, '') if ab else ''
        elif canon == 'episode title':
            v = clean_text(_episode_title(ctx.get('titles') or []), cfg)
        elif canon == 'episode cleantitle':
            v = clean_title(clean_text(_episode_title(ctx.get('titles') or []), cfg))
        elif canon in ('air-date', 'air date'):
            d = str(ctx.get('air_date') or '')
            v = d if canon == 'air-date' else d.replace('-', ' ')
        elif canon == 'quality full':
            v = quality_full(ctx.get('quality'), bool(ctx.get('proper')), str(ctx.get('release') or ''))
        elif canon == 'quality title':
            v = quality_full(ctx.get('quality'), False, str(ctx.get('release') or ''))
        elif canon == 'release group':
            v = clean_text(str(ctx.get('group') or ''), cfg)
        else:
            v = ''
        return _cased(v, case, sep)

    return _tidy(_TOKEN_RE.sub(value, fmt))


def _fit(stem: str, ext: str) -> str:
    while len((stem + ext).encode('utf-8')) > NAME_MAX_BYTES and len(stem) > 1:
        stem = stem[:-1].rstrip()
    return stem.rstrip(' .') + ext


def episode_name(ctx: Dict[str, Any], ext: str, series_type: str, cfg: Dict[str, Any]) -> Optional[str]:
    """The new file name (the original extension kept), or None when it can't be named (the
    caller keeps the release's own name)."""
    kind = series_type if series_type in ('daily', 'anime') else 'standard'
    if kind == 'daily' and not ctx.get('air_date'):
        kind = 'standard'
    stem = render(cfg[kind], ctx, cfg)
    if not stem or stem in ('.', '..') or '/' in stem:
        return None
    return _fit(stem, ext)


_SEASON_DIR = re.compile(r'^(?P<pre>(?:season|series|s)\s*)(?P<n>\d+)$', re.I)
_SPECIALS = re.compile(r'^(?:specials?|season\s*0+|s0+)$', re.I)


def season_folder(season: int, existing: Iterable[str], cfg: Dict[str, Any]) -> str:
    """The season's folder. v185 hotfix (Taylor): the show's existing folder for this season is reused
    as it is ('Season 8' or 'Season 08', 'S08', 'Series 8'; specials: 'Specials', 'Season 00'); with
    none, a new folder follows the Season (Specials) folder format exactly, never its siblings' style.
    Nothing is ever renamed. Two folders for the same season: the one the format names, else the first."""
    names = sorted(n for n in existing if n and not n.startswith('.'))
    if season == 0:
        want = clean_text(render(cfg['specials_folder'], {'season': 0}, cfg), cfg) or 'Specials'
        same = [n for n in names if _SPECIALS.match(n) or n == want]
    else:
        want = clean_text(render(cfg['season_folder'], {'season': season}, cfg), cfg) or f'Season {season}'
        same = [n for n in names if n == want or ((m := _SEASON_DIR.match(n)) and int(m.group('n')) == season)]
    if not same:
        return want
    return want if want in same else same[0]


def series_folder(show: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    from app.file_management import safe_component
    return safe_component(render(cfg['series_folder'], {'title': show.get('title'), 'year': show.get('year')}, cfg),
                          fallback='Series')


def sidecars(video: str) -> List[Tuple[str, str]]:
    """[(path, suffix)] subtitle files next to *video* named after it ('X.en.srt' -> '.en.srt'), and,
    for a lone video, those in a 'Subs' folder beside it ('Subs/2_English.srt' -> '.en.srt')."""
    d, base = os.path.split(video)
    stem = os.path.splitext(base)[0]
    out: List[Tuple[str, str]] = []
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return out
    for n in names:
        p = os.path.join(d, n)
        if n == base or not n.startswith(stem + '.') or not n.lower().endswith(SIDECAR_EXTS) or not os.path.isfile(p):
            continue
        out.append((p, n[len(stem):]))
    videos = [n for n in names if n.lower().endswith(('.mkv', '.mp4', '.avi', '.m4v', '.ts', '.wmv', '.mov'))]
    subs = next((os.path.join(d, n) for n in names if n.lower() in ('subs', 'subtitles') and os.path.isdir(os.path.join(d, n))), None)
    if subs and len(videos) == 1 and not out:
        seen = set()
        for n in sorted(os.listdir(subs)):
            p = os.path.join(subs, n)
            if not n.lower().endswith(SIDECAR_EXTS) or not os.path.isfile(p):
                continue
            lang = _lang(os.path.splitext(n)[0])
            ext = os.path.splitext(n)[1].lower()
            suffix = f'.{lang}{ext}' if lang else ext
            k, i = suffix, 2
            while k in seen:
                k = f'.{lang}.{i}{ext}' if lang else f'.{i}{ext}'
                i += 1
            seen.add(k)
            out.append((p, k))
    return out


_LANGS = {'english': 'en', 'eng': 'en', 'en': 'en', 'spanish': 'es', 'spa': 'es', 'french': 'fr', 'fre': 'fr', 'german': 'de',
          'ger': 'de', 'italian': 'it', 'portuguese': 'pt', 'dutch': 'nl', 'japanese': 'ja', 'korean': 'ko', 'chinese': 'zh'}


def _lang(stem: str) -> str:
    words = re.split(r'[\s._\-]+', stem.lower())
    forced = 'forced' in words or 'sdh' in words
    for w in reversed(words):
        if w in _LANGS:
            return _LANGS[w] + ('.forced' if 'forced' in words else '.sdh' if forced else '')
    return ''


# ------------------------------------------------------------------ the preview (Settings)

SAMPLES = {
    'standard': {'title': 'Meltdown: Three Mile Island', 'year': 2022, 'season': 1, 'episodes': [1], 'titles': ['The Accident'],
                 'air_date': '2022-05-04', 'quality': '1080p-web', 'group': 'NTb'},
    'daily': {'title': 'Dateline NBC', 'year': 1992, 'season': 34, 'episodes': [1], 'titles': ['The Phantom'],
              'air_date': '2025-09-26', 'quality': '1080p-web', 'group': 'NTb'},
    'anime': {'title': "Frieren: Beyond Journey's End", 'year': 2023, 'season': 1, 'episodes': [1], 'absolutes': [1],
              'titles': ["The Journey's End"], 'air_date': '2023-09-29', 'quality': '1080p-web', 'group': 'SubsPlease'},
    'multi': {'title': 'The IT Crowd', 'year': 2006, 'season': 1, 'episodes': [1, 2, 3],
              'titles': ["Yesterday's Jam", 'Calamity Jen', 'Fifty-Fifty'], 'quality': '720p-hdtv'},
}


def preview(cfg: Dict[str, Any]) -> Dict[str, str]:
    """One example per format field (and the multi-episode style), for the settings page."""
    c = dict(DEFAULTS)
    c.update({k: v for k, v in cfg.items() if k in DEFAULTS})
    out: Dict[str, str] = {}
    for k in ('standard', 'daily', 'anime'):
        out[k] = (episode_name(SAMPLES[k], '.mkv', k, c) or '') if check_format(k, c[k]) is None else ''
    out['multi_episode_style'] = (episode_name(SAMPLES['multi'], '.mkv', 'standard', c) or '') if check_format('standard', c['standard']) is None else ''
    out['series_folder'] = series_folder(SAMPLES['standard'], c) if check_format('series_folder', c['series_folder']) is None else ''
    out['season_folder'] = render(c['season_folder'], {'season': 1}, c) if check_format('season_folder', c['season_folder']) is None else ''
    out['specials_folder'] = render(c['specials_folder'], {'season': 0}, c) if check_format('specials_folder', c['specials_folder']) is None else ''
    return out
