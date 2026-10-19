"""TV searching (v158b, v158c): the recent-releases feed, searches after an episode airs, on Add,
manual episode/season searches, and the 12-hour series refresh.

Trackers: only the owner's own, the ones on for TV in Settings > TV > Trackers
(section_trackers.ordered_ids('tv')). Nothing is preconfigured; with none on, nothing is searched.

Budget (v158c, PM): the cap is not raised. A capped tracker (a site tracker, tv_budget.capped) has
8 slots a day, one every 3 h (tv_budget.SLOT_S), spread evenly: the feed polls it in a free slot,
and an automatic search (after air, on Add) may take a free slot instead, which moves the
next poll one slot on. Those requests are purpose 'tv' (tv_budget's caps on top of the tracker
guard's limits). An indexer with no cap (generic Torznab/Newznab) is polled every 15 min (FEED_S)
and searched as needed, held to the guard's own limits only. Manual searches are interactive: the
guard's interactive budget, never the TV one.

Automatic searching runs only when TV settings' automation is on (enabled; the feed also needs
feed_enabled; v177: no backlog, see tv_safety for the floor, the no-upgrade rule and the daily
ceiling), and only where a grab could follow
(tv_grab.allowed(): on test the 'tv_grab' job, off by default), so the shared tracker accounts
aren't spent on answers nobody can use. Manual searches always search and show what they'd pick;
the grab itself is refused while grabs are off.

Which episodes: monitored show, monitored episode, aired, no linked file, not already grabbed
(grabs downloading/importing/imported). A release must name the show (title or alias, year when
it has one), cover wanted episodes (episode, multi-episode, a season pack when it covers every
wanted episode of that season and at least two, or a daily date), and pass the show's quality
profile (allowed qualities in the owner's order, must/must-not words, seeders, MB per minute).
Blocklisted releases never.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app import tv_grab, tv_match, tv_monitor, tv_release, tv_safety, tv_settings, tv_store

logger = logging.getLogger(__name__)

TV_CATEGORIES = [5000, 5030, 5040, 5045, 5060]
TICK_S = 60
FEED_S = 15 * 60
SLOT_BUSY = 'tv slot'
REFRESH_S = 12 * 3600
BACKOFF_H = (3, 6, 12, 24, 48, 96, 168)
MAX_QUERIES = 4                          # per show per search
LIMIT = 100
DEFAULT_RUNTIME = 45
ACTIVE_GRABS = ('downloading', 'importing', 'imported')
FALLBACK_ORDER = ('1080p-web', '1080p-bluray', '720p-web', '1080p-hdtv', '720p-bluray', '720p-hdtv',
                  '2160p-web', '2160p-bluray', 'sd-web', 'sd-bluray', 'sd-hdtv')
NO_TRACKERS = 'No TV trackers are on'

_lock = threading.Lock()


# ------------------------------------------------------------------ trackers

def trackers() -> List[Dict[str, Any]]:
    """The owner's trackers that are on for TV, in the TV order (never any other)."""
    from app import section_trackers
    from app.tracker_search import get_search
    ids = section_trackers.ordered_ids('tv')
    if not ids:
        return []
    by_id = {c['id']: c for c in get_search().store.enabled_configs()}
    return [by_id[i] for i in ids if i in by_id]


def _log(cfg, purpose: str, sid: Optional[int], query: str, n: int, outcome: str, now: float) -> None:
    from app.tracker_guard import guard_key
    with tv_store.write() as conn:
        conn.execute('INSERT INTO search_log (ts, section, tracker_key, purpose, series_id, query, results, outcome)'
                     " VALUES (?, 'tv', ?, ?, ?, ?, ?, ?)", (now, guard_key(cfg), purpose, sid, query, n, outcome))


def query_trackers(query: str, purpose: str, sid: Optional[int], interactive: bool, now: float,
                   cfgs: Optional[List[Dict[str, Any]]] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Search each TV tracker once; (items, statuses). Items are search results with a release
    link (tracker_search.to_item). Automatic searches are charged to the TV budget."""
    from app.tracker_search import TrackerError, TrackerSkipped, _match_categories, _secret_values, get_search, search_status
    from app import tv_budget
    engine = get_search()
    items: List[Dict[str, Any]] = []
    statuses: List[Dict[str, Any]] = []
    for cfg in trackers() if cfgs is None else cfgs:
        cats = _match_categories(cfg, TV_CATEGORIES)
        if not cats:
            continue
        capped = tv_budget.capped(cfg)
        if not interactive and capped and not tv_budget.slot_free(cfg, now):
            statuses.append({'id': cfg['id'], 'name': cfg['name'], 'status': SLOT_BUSY})
            continue                                   # no free slot: not sent, not logged
        try:
            raw = engine.search_one(cfg, query, cats, LIMIT, interactive,
                                    purpose='tv' if not interactive and capped else None)
        except TrackerSkipped as e:
            statuses.append({'id': cfg['id'], 'name': cfg['name'], 'status': search_status(e)})
            _log(cfg, purpose, sid, query, 0, 'skipped', now)
            continue
        except TrackerError as e:
            statuses.append({'id': cfg['id'], 'name': cfg['name'], 'status': search_status(e)})
            _log(cfg, purpose, sid, query, 0, 'failed', now)
            continue
        sv = _secret_values(cfg)
        got = [it for it in (engine.to_item(cfg, r, sv) for r in raw) if it]
        items += got
        statuses.append({'id': cfg['id'], 'name': cfg['name'], 'status': 'ok'})
        _log(cfg, purpose, sid, query, len(got), 'ok', now)
    engine.tokens.flush()
    return items, statuses


# ------------------------------------------------------------------ what is wanted

def _show(conn, sid: int) -> Optional[Dict[str, Any]]:
    r = conn.execute('SELECT id, title, year, aliases, series_type, profile_id, monitored FROM series WHERE id = ?',
                     (sid,)).fetchone()
    if not r:
        return None
    s = dict(r)
    names = {tv_match.normalize(s['title'])}
    try:
        names |= {tv_match.normalize(a) for a in json.loads(s['aliases'] or '[]') if isinstance(a, str)}
    except ValueError:
        pass
    s['names'] = {n for n in names if n}
    pid = s['profile_id'] or tv_monitor.default_profile_id()
    p = conn.execute('SELECT * FROM quality_profiles WHERE id = ?', (pid,)).fetchone() if pid else None
    s['profile'] = dict(p) if p else None
    return s


def _grabbed(conn, sid: int) -> set:
    out = set()
    for r in conn.execute(f'SELECT episode_ids FROM grabs WHERE series_id = ? AND state IN ({",".join("?" * len(ACTIVE_GRABS))})',
                          (sid, *ACTIVE_GRABS)):
        try:
            out |= {int(x) for x in json.loads(r['episode_ids'] or '[]')}
        except (ValueError, TypeError):
            pass
    return out


def wanted(conn, sid: int, today: Optional[str] = None, ids: Optional[Iterable[int]] = None) -> List[Dict[str, Any]]:
    """Monitored, aired, no linked file, not already grabbed (optionally only *ids*)."""
    today = today or dt.date.today().isoformat()
    rows = [dict(r) for r in conn.execute(
        'SELECT e.id, e.season_number, e.episode_number, e.air_date, e.airstamp, e.runtime, e.search_attempts'
        ' FROM episodes e JOIN series s ON s.id = e.series_id'
        ' WHERE s.id = ? AND s.monitored = 1 AND e.monitored = 1 AND e.air_date IS NOT NULL AND e.air_date <= ?'
        f' AND NOT {tv_monitor._LINKED}', (sid, today))]
    got = _grabbed(conn, sid)
    keep = None if ids is None else {int(i) for i in ids}
    return [e for e in rows if e['id'] not in got and (keep is None or e['id'] in keep)]


def aired_at(e: Dict[str, Any]) -> Optional[float]:
    if e.get('airstamp'):
        try:
            return dt.datetime.fromisoformat(str(e['airstamp']).replace('Z', '+00:00')).timestamp() \
                + 60 * int(e.get('runtime') or 0)
        except ValueError:
            pass
    if e.get('air_date'):
        try:
            d = dt.date.fromisoformat(str(e['air_date'])) + dt.timedelta(days=1)    # its local day is over
            return dt.datetime(d.year, d.month, d.day).timestamp()
        except ValueError:
            return None
    return None


# ------------------------------------------------------------------ choosing a release

def _words(raw: Any) -> List[str]:
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return [str(w).casefold() for w in v or [] if str(w).strip()]


def _rank(profile: Optional[Dict[str, Any]], q: Optional[str]) -> Optional[int]:
    """Lower is better; None: the profile doesn't allow it."""
    if not profile:                                  # no profile at all: sensible TV sizes first, never remux
        return FALLBACK_ORDER.index(q) if q in FALLBACK_ORDER else None
    try:
        items = json.loads(profile['items'] or '[]')
    except ValueError:
        items = []
    allowed = [i['id'] for i in items if isinstance(i, dict) and i.get('allowed')]
    if q in allowed:
        return allowed.index(q)
    return None


def candidates(items: List[Dict[str, Any]], show: Dict[str, Any], want: List[Dict[str, Any]],
               blocked: Optional[set] = None) -> List[Dict[str, Any]]:
    """Releases for *show* covering *want*, each {item, covers (episode ids), rank, why}."""
    by_se = {(e['season_number'], e['episode_number']): e for e in want}
    by_date = {e['air_date']: e for e in want if e.get('air_date')}
    seasons: Dict[int, List[int]] = {}
    for e in want:
        seasons.setdefault(e['season_number'], []).append(e['id'])
    p = show.get('profile')
    must, never = (_words(p['must_contain']), _words(p['must_not_contain'])) if p else ([], [])
    min_seeders = int(p['min_seeders']) if p else 1
    prefer_packs = bool(p['prefer_season_packs']) if p else True
    out = []
    for it in items:
        title = str(it.get('title') or '')
        if not title or title in (blocked or set()):
            continue
        rel = tv_release.parse(title)
        if tv_match.normalize(rel.title or '') not in show['names']:
            continue
        if rel.year and show.get('year') and rel.year != show['year']:
            continue
        covers: List[int] = []
        if rel.kind in ('episode', 'multi') and rel.season is not None:
            covers = [by_se[(rel.season, n)]['id'] for n in rel.episodes if (rel.season, n) in by_se]
        elif rel.kind == 'season' and rel.season in seasons and len(seasons[rel.season]) >= 2:
            covers = list(seasons[rel.season])
        elif rel.kind == 'daily' and rel.air_date in by_date:
            covers = [by_date[rel.air_date]['id']]
        if not covers:
            continue
        low = title.casefold()
        if any(w not in low for w in must) or any(w in low for w in never):
            continue
        if int(it.get('seeders') or 0) < min_seeders:
            continue
        q = tv_monitor.quality_of(title)
        rank = _rank(p, q)
        if rank is None:
            continue
        if p and (p['min_mb_per_min'] or p['max_mb_per_min']):
            mins = sum(int(next((e['runtime'] for e in want if e['id'] == i), 0) or DEFAULT_RUNTIME) for i in covers)
            mb = (int(it.get('size') or 0) / 1048576) / max(1, mins)
            if (p['min_mb_per_min'] and mb < p['min_mb_per_min']) or (p['max_mb_per_min'] and mb > p['max_mb_per_min']):
                continue
        out.append({'item': {**it, 'quality': q}, 'covers': covers, 'rank': rank, 'proper': rel.proper,
                    'pack': rel.kind == 'season', 'prefer_packs': prefer_packs})
    return out


def choose(cands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The fewest best releases covering the most wanted episodes: quality first (profile order),
    then a season pack when the profile prefers them, PROPER/REPACK, freeleech, seeders."""
    def key(c):
        it = c['item']
        return (c['rank'], -(len(c['covers']) if c['prefer_packs'] else 0), -c['proper'],
                -int(bool(it.get('freeleech'))), -int(it.get('seeders') or 0))
    left = sorted(cands, key=key)
    picked, done = [], set()
    for c in left:
        if set(c['covers']) & done:
            continue
        picked.append(c)
        done |= set(c['covers'])
    return picked


def _queries(show: Dict[str, Any], want: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """(purpose, query) for the wanted episodes: a season query when a season has 2+ wanted,
    else one per episode (daily shows by date). At most MAX_QUERIES."""
    title = show['title']
    out: List[Tuple[str, str]] = []
    by_season: Dict[int, List[Dict[str, Any]]] = {}
    for e in sorted(want, key=lambda e: (e['season_number'], e['episode_number'])):
        by_season.setdefault(e['season_number'], []).append(e)
    for s, eps in by_season.items():
        if show.get('series_type') == 'daily':
            out += [('episode', f"{title} {str(e['air_date']).replace('-', '.')}") for e in eps]
        elif len(eps) >= 2:
            out.append(('season', f'{title} S{s:02d}'))
        else:
            out.append(('episode', f"{title} S{s:02d}E{eps[0]['episode_number']:02d}"))
    return out[:MAX_QUERIES]


# ------------------------------------------------------------------ one search

def search_show(sid: int, episode_ids: Optional[Iterable[int]] = None, *, origin: str = 'auto',
                purpose: Optional[str] = None, interactive: bool = False, now: Optional[float] = None,
                do_grab: bool = True) -> Dict[str, Any]:
    """Search the show's wanted episodes (or *episode_ids* of them); grab the picks when allowed.
    {'ok', 'searched', 'results', 'picks': [{title, quality, episodes, grabbed, error}], 'statuses', 'error'}."""
    now = time.time() if now is None else now
    with tv_store.connect() as conn:
        show = _show(conn, sid)
        if show is None:
            return {'ok': False, 'error': 'Not found'}
        want = wanted(conn, sid, ids=episode_ids)
        if origin != 'manual':               # v177: an automatic search never asks for anything past the floor
            want = [e for e in want if tv_safety.in_floor(e, now)]
        blocked = {r['release_title'] for r in conn.execute('SELECT release_title FROM blocklist WHERE series_id = ?', (sid,))}
    out: Dict[str, Any] = {'ok': True, 'searched': 0, 'results': 0, 'picks': [], 'statuses': [], 'error': None,
                           'wanted': len(want)}
    if not want:
        return out
    cfgs = trackers()
    if not cfgs:
        out.update(ok=False, error=NO_TRACKERS)
        return out
    items: List[Dict[str, Any]] = []
    answered = False
    for p, q in _queries(show, want):
        got, st = query_trackers(q, purpose or p, sid, interactive, now, cfgs)
        out['searched'] += 1
        out['statuses'] += st
        answered |= any(s['status'] == 'ok' for s in st)
        items += got
    out['results'] = len(items)
    out['answered'] = answered
    picks = choose(candidates(items, show, want, blocked))
    for c in picks:
        it = c['item']
        row = {'title': it['title'], 'quality': it.get('quality'), 'episodes': sorted(c['covers']),
               'tracker': it.get('indexer'), 'size': it.get('size'), 'grabbed': False, 'error': None}
        if do_grab:
            season = next((e['season_number'] for e in want if e['id'] == c['covers'][0]), None)
            g = tv_grab.grab(it, sid, season, c['covers'], origin, now)
            row['grabbed'], row['error'] = bool(g.get('ok')), g.get('error')
        out['picks'].append(row)
    _after(sid, want, out, now, automatic=not interactive)
    return out


def _after(sid: int, want: List[Dict[str, Any]], out: Dict[str, Any], now: float, automatic: bool) -> None:
    """Episode search state: grabbed ones stop; the rest back off (only when a tracker answered)."""
    grabbed = {i for p in out['picks'] if p['grabbed'] for i in p['episodes']}
    with tv_store.write() as conn:
        for e in want:
            if e['id'] in grabbed:
                conn.execute('UPDATE episodes SET last_search_at = ?, next_search_at = NULL WHERE id = ?', (now, e['id']))
            elif out.get('answered'):
                n = int(e['search_attempts'] or 0)
                nxt = now + 3600 * BACKOFF_H[min(n, len(BACKOFF_H) - 1)] if automatic else None
                conn.execute('UPDATE episodes SET last_search_at = ?, search_attempts = search_attempts + 1,'
                             ' next_search_at = COALESCE(?, next_search_at) WHERE id = ?', (now, nxt, e['id']))


def manual(sid: int, season: Optional[int] = None, episode_id: Optional[int] = None,
           now: Optional[float] = None) -> Dict[str, Any]:
    """The owner's Search for an episode or a season (interactive budget); grabs what it picks."""
    with tv_store.connect() as conn:
        if season is not None:
            ids = [r['id'] for r in conn.execute('SELECT id FROM episodes WHERE series_id = ? AND season_number = ?',
                                                 (sid, season))]
        else:
            ids = [r['id'] for r in conn.execute('SELECT id FROM episodes WHERE series_id = ? AND id = ?', (sid, episode_id))]
    if not ids:
        return {'ok': False, 'error': 'Not found'}
    out = search_show(sid, ids, origin='manual', purpose='manual', interactive=True, now=now)
    out['grabs_allowed'] = tv_grab.allowed()
    return out


# ------------------------------------------------------------------ v182: interactive search

WRONG_SHOW = 'Not this show'
WRONG_YEAR = 'Wrong year'
NOT_TARGET = 'Not the episodes searched for'
HAS_FILE = 'Already has a file'
GRABBED = 'Already grabbed'
BLOCKLISTED = 'Blocklisted'
NOT_IN_PROFILE = 'Quality not in the profile'
FEW_SEEDERS = 'Too few seeders'
MISSING_WORD = 'Missing a required word'
BLOCKED_WORD = 'Has a blocked word'
BAD_SIZE = 'Size outside the profile'
#: the reasons a release can't be grabbed at all (rule 2, the blocklist, not the show); the profile's own
#: rejections are shown but the owner may still grab it (Sonarr's interactive search does the same)
HARD = (WRONG_SHOW, WRONG_YEAR, NOT_TARGET, HAS_FILE, BLOCKLISTED)


def _episodes(conn, sid: int) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        f'SELECT e.id, e.season_number, e.episode_number, e.air_date, e.airstamp, e.runtime, {tv_monitor._LINKED} AS has_file'
        ' FROM episodes e WHERE e.series_id = ? ORDER BY e.season_number, e.episode_number', (sid,))]


def covers(title: str, show: Dict[str, Any], eps: List[Dict[str, Any]]) -> Tuple[Optional[str], List[int], Optional[int]]:
    """What a release title covers among the show's episodes: (why not, episode ids, season). A season pack
    covers every episode of that season (with a file or not: rule 2 then refuses it)."""
    rel = tv_release.parse(title)
    if tv_match.normalize(rel.title or '') not in show['names']:
        return WRONG_SHOW, [], None
    if rel.year and show.get('year') and rel.year != show['year']:
        return WRONG_YEAR, [], None
    ids: List[int] = []
    if rel.kind in ('episode', 'multi') and rel.season is not None:
        ids = [e['id'] for e in eps if e['season_number'] == rel.season and e['episode_number'] in set(rel.episodes)]
    elif rel.kind == 'season' and rel.season is not None:
        ids = [e['id'] for e in eps if e['season_number'] == rel.season]
    elif rel.kind == 'daily' and rel.air_date:
        ids = [e['id'] for e in eps if e['air_date'] == rel.air_date]
    if not ids:
        return NOT_TARGET, [], None
    season = next(e['season_number'] for e in eps if e['id'] == ids[0])
    return None, ids, season


def judge(items: List[Dict[str, Any]], show: Dict[str, Any], eps: List[Dict[str, Any]], targets: Iterable[int],
          blocked: set, grabbed: set) -> List[Dict[str, Any]]:
    """Every release with why it would be rejected (none: the best kind). Sorted: grabbable first, then the
    profile's order, then seeders."""
    p = show.get('profile')
    must, never = (_words(p['must_contain']), _words(p['must_not_contain'])) if p else ([], [])
    min_seeders = int(p['min_seeders']) if p else 1
    by_id = {e['id']: e for e in eps}
    want = set(targets)
    out = []
    for it in items:
        title = str(it.get('title') or '')
        if not title:
            continue
        why, ids, season = covers(title, show, eps)
        rej: List[str] = [why] if why else []
        if ids and not set(ids) & want:
            rej.append(NOT_TARGET)
        if any(by_id[i]['has_file'] for i in ids):
            rej.append(HAS_FILE)
        if title in blocked:
            rej.append(BLOCKLISTED)
        if ids and set(ids) <= grabbed:
            rej.append(GRABBED)
        q = tv_monitor.quality_of(title)
        rank = _rank(p, q)
        if rank is None:
            rej.append(NOT_IN_PROFILE)
        if int(it.get('seeders') or 0) < min_seeders:
            rej.append(FEW_SEEDERS)
        low = title.casefold()
        if any(w not in low for w in must):
            rej.append(MISSING_WORD)
        if any(w in low for w in never):
            rej.append(BLOCKED_WORD)
        if p and ids and (p['min_mb_per_min'] or p['max_mb_per_min']):
            mins = sum(int(by_id[i].get('runtime') or DEFAULT_RUNTIME) for i in ids)
            mb = (int(it.get('size') or 0) / 1048576) / max(1, mins)
            if (p['min_mb_per_min'] and mb < p['min_mb_per_min']) or (p['max_mb_per_min'] and mb > p['max_mb_per_min']):
                rej.append(BAD_SIZE)
        out.append({'title': title, 'tracker': it.get('indexer'), 'size': int(it.get('size') or 0),
                    'seeders': int(it.get('seeders') or 0), 'leechers': int(it.get('leechers') or 0),
                    'quality': q, 'published': it.get('publishDate') or None, 'freeleech': bool(it.get('freeleech')),
                    'download_url': it.get('downloadUrl'), 'season': season,
                    'episodes': [{'id': i, 'season': by_id[i]['season_number'], 'episode': by_id[i]['episode_number']} for i in ids],
                    'pack': tv_release.parse(title).kind == 'season',
                    'rejections': rej, 'grabbable': not any(r in HARD for r in rej) and bool(it.get('downloadUrl')),
                    '_rank': 999 if rank is None else rank})
    out.sort(key=lambda r: (not r['grabbable'], bool(r['rejections']), r['_rank'], -r['seeders']))
    for r in out:
        r.pop('_rank')
    return out


def interactive(sid: int, season: Optional[int] = None, episode_id: Optional[int] = None,
                now: Optional[float] = None) -> Dict[str, Any]:
    """The owner's Interactive search for a season or an episode: searches (interactive budget), grabs
    nothing, and lists every release with its rejections so the owner picks one (grab_release)."""
    now = time.time() if now is None else now
    with tv_store.connect() as conn:
        show = _show(conn, sid)
        if show is None:
            return {'ok': False, 'error': 'Not found'}
        eps = _episodes(conn, sid)
        targets = [e for e in eps if (e['season_number'] == season if season is not None else e['id'] == episode_id)]
        if not targets:
            return {'ok': False, 'error': 'Not found'}
        blocked = {r['release_title'] for r in conn.execute('SELECT release_title FROM blocklist WHERE series_id = ?', (sid,))}
        got = _grabbed(conn, sid)
    out: Dict[str, Any] = {'ok': True, 'searched': 0, 'releases': [], 'statuses': [], 'error': None,
                           'grabs_allowed': tv_grab.allowed()}
    cfgs = trackers()
    if not cfgs:
        out.update(ok=False, error=NO_TRACKERS)
        return out
    if season is not None and show.get('series_type') != 'daily':
        queries = [f"{show['title']} S{season:02d}"]
    else:
        queries = [q for _, q in _queries(show, targets)]
    items: List[Dict[str, Any]] = []
    for q in queries[:MAX_QUERIES]:
        found, st = query_trackers(q, 'manual', sid, True, now, cfgs)
        out['searched'] += 1
        out['statuses'] += st
        items += found
    seen, uniq = set(), []
    for it in items:
        k = (it.get('indexerId'), it.get('title'), it.get('size'))
        if k not in seen:
            seen.add(k)
            uniq.append(it)
    out['releases'] = judge(uniq, show, eps, [e['id'] for e in targets], blocked, got)
    return out


def grab_release(sid: int, item: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    """v182: grab the release the owner picked in Interactive search. Owner-initiated, so origin 'manual':
    the 24 h floor doesn't apply (tv_safety rule 1 is for automatic grabs only); rule 2 still does (an
    episode with a file is refused, so is a pack that covers one), and the grab goes through tv_grab (the TV
    category and tag, the scoped add). What it covers is worked out here from the title, never taken
    from the request."""
    now = time.time() if now is None else now
    with tv_store.connect() as conn:
        show = _show(conn, sid)
        if show is None:
            return {'ok': False, 'error': 'Not found', 'status': 404}
        eps = _episodes(conn, sid)
    why, ids, season = covers(str(item.get('title') or ''), show, eps)
    if why:
        return {'ok': False, 'error': why, 'status': 400}
    g = tv_grab.grab(item, sid, season, ids, 'manual', now, tokens_only=True)
    if g.get('ok'):
        with tv_store.write() as conn:
            conn.executemany('UPDATE episodes SET last_search_at = ?, next_search_at = NULL WHERE id = ?',
                             [(now, i) for i in ids])
    return {**g, 'episodes': ids}


# ------------------------------------------------------------------ automatic

def automation() -> Dict[str, Any]:
    return tv_settings.load()['automation']


def auto_on() -> bool:
    return bool(automation().get('enabled')) and tv_grab.allowed()


def on_added(sid: int, now: Optional[float] = None) -> int:
    """A show added from Search: its wanted episodes (from its Monitor option) are searched on the
    next pass. Returns how many were queued."""
    now = time.time() if now is None else now
    if not auto_on():
        return 0
    with tv_store.write() as conn:
        ids = [e['id'] for e in wanted(conn, sid) if tv_safety.in_floor(e, now)]   # v177: inside the floor only
        for i in ids:
            conn.execute('UPDATE episodes SET next_search_at = ? WHERE id = ?', (now, i))
    if ids:
        logger.info('TV search: %d episode(s) of a new show queued', len(ids))
    return len(ids)


def schedule(now: float) -> int:
    """After an episode airs: its search is set for first_search_delay_h after it aired. v177 (Taylor):
    only episodes inside the air-date floor (tv_safety); backlog_enabled is ignored, older missing episodes
    are never searched automatically. Episodes already scheduled or searched are kept."""
    auto = automation()
    delay = 3600 * int(auto.get('first_search_delay_h') or 0)
    n = 0
    with tv_store.write() as conn:
        rows = [dict(r) for r in conn.execute(
            'SELECT e.id, e.air_date, e.airstamp, e.runtime FROM episodes e JOIN series s ON s.id = e.series_id'
            ' WHERE s.monitored = 1 AND e.monitored = 1 AND e.next_search_at IS NULL AND e.last_search_at IS NULL'
            f' AND e.air_date IS NOT NULL AND NOT {tv_monitor._LINKED}')]
        for e in rows:
            at = aired_at(e)
            if at is None or not tv_safety.in_floor(e, now):
                continue                     # v177 (Taylor): no Missing / backlog searches for TV, ever
            conn.execute('UPDATE episodes SET next_search_at = ? WHERE id = ?', (at + delay, e['id']))
            n += 1
    return n


def run_due(now: float, max_shows: int = 3) -> Dict[str, int]:
    """Search the shows with episodes due (next_search_at passed), a few per pass."""
    with tv_store.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            'SELECT e.series_id AS sid, e.id, e.air_date FROM episodes e WHERE e.next_search_at IS NOT NULL'
            ' AND e.next_search_at <= ? ORDER BY e.next_search_at', (now,))]
    by: Dict[int, List[int]] = {}
    stale = []
    with tv_store.connect() as conn:
        floor = {r['id']: tv_safety.in_floor(dict(r), now) for r in conn.execute(
            'SELECT id, air_date, airstamp FROM episodes WHERE next_search_at IS NOT NULL AND next_search_at <= ?', (now,))}
    for r in rows:
        if not floor.get(r['id']):
            stale.append(r['id'])            # v177: past the floor (or a backlog entry from before): never searched
            continue
        by.setdefault(r['sid'], []).append(r['id'])
    if stale:
        with tv_store.write() as conn:
            conn.executemany('UPDATE episodes SET next_search_at = NULL WHERE id = ?', [(i,) for i in stale])
        logger.info('TV search: %d due episode(s) past the 24 h air-date floor dropped, not searched', len(stale))
    out = {'shows': 0, 'grabbed': 0}
    for sid, ids in list(by.items())[:max_shows]:
        res = search_show(sid, ids, origin='auto', now=now)
        out['shows'] += 1
        out['grabbed'] += sum(1 for p in res.get('picks', []) if p['grabbed'])
        if res.get('error') == NO_TRACKERS:
            break
    return out


def feed_due(cfg: Dict[str, Any], now: float) -> bool:
    """An indexer with no cap: every FEED_S. A capped tracker: whenever it has a free slot (its
    daily budget spread evenly; a post-air search that took the slot moves the feed to the next)."""
    from app import tv_budget
    if tv_budget.capped(cfg):
        return tv_budget.slot_free(cfg, now)
    return now - float(tv_store.get_meta(f"tv_feed:{cfg['id']}") or 0) >= FEED_S


def feed(now: float) -> Dict[str, int]:
    """The recent-releases feed: each TV tracker's newest TV releases (see feed_due); picks for
    any wanted episode are grabbed."""
    out = {'trackers': 0, 'grabbed': 0}
    for cfg in trackers():
        key = f"tv_feed:{cfg['id']}"
        if not feed_due(cfg, now):
            continue
        items, st = query_trackers('', 'feed', None, False, now, [cfg])
        if not st or st[0]['status'] != 'ok':
            continue
        tv_store.set_meta(key, str(now))
        out['trackers'] += 1
        if not items:
            continue
        with tv_store.connect() as conn:
            sids = [r['id'] for r in conn.execute('SELECT id FROM series WHERE monitored = 1')]
            shows = {sid: _show(conn, sid) for sid in sids}
        names: Dict[str, List[int]] = {}
        for sid, s in shows.items():
            for n in s['names']:
                names.setdefault(n, []).append(sid)
        hits: Dict[int, List[Dict[str, Any]]] = {}
        for it in items:
            t = tv_match.normalize(tv_release.parse(str(it.get('title') or '')).title or '')
            for sid in names.get(t, []):
                hits.setdefault(sid, []).append(it)
        for sid, its in hits.items():
            with tv_store.connect() as conn:
                want = [e for e in wanted(conn, sid) if tv_safety.in_floor(e, now)]   # v177: the floor
                blocked = {r['release_title'] for r in conn.execute('SELECT release_title FROM blocklist WHERE series_id = ?', (sid,))}
            for c in choose(candidates(its, shows[sid], want, blocked)):
                season = next((e['season_number'] for e in want if e['id'] == c['covers'][0]), None)
                g = tv_grab.grab(c['item'], sid, season, c['covers'], 'auto', now)
                if g.get('ok'):
                    out['grabbed'] += 1
                    with tv_store.write() as conn:
                        conn.executemany('UPDATE episodes SET next_search_at = NULL, last_search_at = ? WHERE id = ?',
                                         [(now, i) for i in c['covers']])
    return out


def refresh(now: float) -> int:
    """Every REFRESH_S: every library show's episodes read again from TMDB (new episodes, dates)."""
    from app import tv_scan
    last = float(tv_store.get_meta('tv_series_refresh_at') or 0)
    if now - last < REFRESH_S:
        return 0
    if not tv_scan._refresh_lock.acquire(blocking=False):
        return 0                                     # a Calendar-open refresh is running: next pass
    try:
        tv_store.set_meta('tv_series_refresh_at', str(now))
        n = tv_scan.refresh_added(now, folders=True, background=True, stale_s=REFRESH_S)
    finally:
        tv_scan._refresh_lock.release()
    logger.info('TV series refresh: %d show(s) read again', n)
    return n


def tick(now: Optional[float] = None) -> Dict[str, Any]:
    """One pass: the 12 h refresh, then (automation on and grabs allowed) scheduling, the feed
    every FEED_S, and the due searches."""
    now = time.time() if now is None else now
    out: Dict[str, Any] = {'refreshed': 0, 'scheduled': 0, 'feed': None, 'due': None}
    if not _lock.acquire(blocking=False):
        return out
    try:
        try:
            out['refreshed'] = refresh(now)
        except Exception as e:                       # noqa: BLE001 - TMDB trouble never stops searching
            logger.warning('TV series refresh failed: %s', type(e).__name__)
        if not auto_on():
            return out
        out['scheduled'] = schedule(now)
        if automation().get('feed_enabled') and now - float(tv_store.get_meta('tv_feed_at') or 0) >= FEED_S:
            tv_store.set_meta('tv_feed_at', str(now))
            out['feed'] = feed(now)
        out['due'] = run_due(now)
    finally:
        _lock.release()
    return out


def status(now: Optional[float] = None) -> Dict[str, Any]:
    from app import tv_budget
    now = time.time() if now is None else now
    auto = automation()
    return {'automation': bool(auto.get('enabled')), 'feed': bool(auto.get('feed_enabled')),
            'grabs_allowed': tv_grab.allowed(),
            'trackers': [{'id': c['id'], 'name': c['name'], **tv_budget.usage(c, now), 'next_slot': tv_budget.next_slot(c, now)}
                         for c in trackers()],
            'feed_every_s': FEED_S, 'slot_s': tv_budget.SLOT_S, 'refresh_every_s': REFRESH_S}


_started = {'on': False}


def start() -> bool:
    if _started['on'] or os.environ.get('LUMA_SKIP_BACKGROUND_THREADS'):
        return False
    _started['on'] = True

    def loop():
        while True:
            time.sleep(TICK_S)
            try:
                tick()
            except Exception as e:                   # noqa: BLE001 - never let one pass end the thread
                logger.error('TV search pass failed: %s', type(e).__name__)
    threading.Thread(target=loop, name='tv-search', daemon=True).start()
    a = automation()
    logger.info('TV search started (automation %s, feed %s, grabs %s, %d TV tracker(s))',
                'on' if a.get('enabled') else 'off', 'on' if a.get('feed_enabled') else 'off',
                'allowed' if tv_grab.allowed() else 'off', len(trackers()))
    return True
