"""TV download watch (v158a, v158b): every minute, completed qBittorrent torrents of THIS
instance's TV category are imported into their show through the library mover's transfer
(hardlink, else copy; never a move, never an overwrite, never a delete), then linked in tv.db at
once (tv_watch.episode_imported), so the card turns downloaded on this same pass.

The name comes from the instance (category()): luma-test-tv on LUMA_INSTANCE=test
(LUMA_TEST_TV_TAG), luma-tv on prod (LUMA_TV_CATEGORY). Luma's TV grabs set it as both the
qBittorrent category and tag. A torrent is this instance's when its category or a tag is that
name and it carries nothing of the other instance's name; everything else in a shared
qBittorrent is invisible here, so neither instance ever acts on the other's torrents.

On: test, while the instance allows tv_import (the default there); prod, only when TV settings'
automation.import_enabled is on (off by default). Reads only from qBittorrent (torrents/info,
torrents/files). The destination is the show's folder in its TV library (Season NN when season
folders are on); the library must be writable and have room. No room: failed with the reason,
then the mover's rule (library_mover.space_retry_due): one automatic retry once the free space
grew by the files' size and 15 min passed, else the Retry button (retry()).
Each file must parse to an episode the show has; anything else is left alone.

State per torrent hash is in tv.db meta ('tvdl:<hash>'): {state, at, completed, files, error}.
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from app import file_management as fm
from app import instance, library_mover as mover, qbit_owner, tv_match, tv_monitor, tv_naming, tv_release, tv_settings, tv_store

logger = logging.getLogger(__name__)

TAG_ENV = 'LUMA_TEST_TV_TAG'
DEFAULT_TAG = 'luma-test-tv'
PROD_ENV = 'LUMA_TV_CATEGORY'
PROD_DEFAULT = 'luma-tv'
INTERVAL_S = 60
JOB = 'tv_import'
NOT_WRITABLE = 'library folder not writable'
NO_SHOW = 'no show in the library'
NO_EPISODES = 'no episodes of this show in the download'
#: bumped when episode matching learns a new kind of name: a torrent refused NO_EPISODES under an older
#: matcher is looked at once more (daily: date-named releases refused before air-date matching).
MATCHER = 2
_DONE = ('imported', 'refused')
_lock = threading.Lock()


def _valid(raw: str, other: str) -> bool:
    return bool(qbit_owner.TAG_RE.match(raw)) and raw.casefold() not in qbit_owner._reserved() \
        and raw.casefold() != qbit_owner.tag_name().casefold() and raw.casefold() != other.casefold()


def _names() -> Tuple[str, str]:
    """(test's name, prod's name), never equal."""
    raw_t = (os.environ.get(TAG_ENV) or '').strip()
    raw_p = (os.environ.get(PROD_ENV) or '').strip()
    test = raw_t if raw_t and _valid(raw_t, PROD_DEFAULT if not raw_p else raw_p) else DEFAULT_TAG
    prod = raw_p if raw_p and _valid(raw_p, test) else PROD_DEFAULT
    if prod.casefold() == test.casefold():
        test, prod = DEFAULT_TAG, PROD_DEFAULT
    return test, prod


def category() -> str:
    """This instance's TV category and tag: luma-test-tv on test, luma-tv on prod."""
    test, prod = _names()
    return test if instance.is_test() else prod


def other_category() -> str:
    test, prod = _names()
    return prod if instance.is_test() else test


def tag_name() -> str:                    # the v158a name, kept for callers
    return category()


def is_mine(t: Dict[str, Any]) -> bool:
    """This instance's TV torrent: category or a tag is ours, nothing is the other instance's."""
    mine, other = category().casefold(), other_category().casefold()
    cat = str(t.get('category') or '').strip().casefold()
    tags = {x.casefold() for x in qbit_owner.torrent_tags(t)}
    if cat == other or other in tags:
        return False
    return cat == mine or mine in tags


def watch_enabled() -> bool:
    if not instance.writes_allowed(JOB):
        return False
    if instance.is_test():
        return True
    return bool(tv_settings.load()['automation'].get('import_enabled'))


def _key(h: str) -> str:
    return f'tvdl:{h}'


def status(h: str) -> Optional[Dict[str, Any]]:
    v = tv_store.get_meta(_key(h))
    try:
        return json.loads(v) if v else None
    except ValueError:
        return None


def _save(h: str, **fields) -> None:
    tv_store.set_meta(_key(h), json.dumps({**(status(h) or {}), **fields}))


def _complete(t: Dict[str, Any]) -> bool:
    if mover.is_paused(t):  # never import a paused/stopped torrent
        return False
    try:
        return float(t.get('progress') or 0) >= 1.0
    except (TypeError, ValueError):
        return False


def _series_for(name: str) -> Optional[Dict[str, Any]]:
    """The library show a release name is for (title or alias, year when the name has one)."""
    rel = tv_release.parse(name)
    want = tv_match.normalize(rel.title or '')
    if not want:
        return None
    paths = {r['id']: os.path.realpath(r['path']) for r in tv_settings.libraries()}
    with tv_store.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            'SELECT id, title, year, aliases, path_id, folder, season_folder FROM series WHERE path_id IS NOT NULL')]
    hits = []
    for r in rows:
        if r['path_id'] not in paths:
            continue
        names = {tv_match.normalize(r['title'])}
        try:
            names |= {tv_match.normalize(a) for a in json.loads(r['aliases'] or '[]') if isinstance(a, str)}
        except ValueError:
            pass
        if want in names and (rel.year is None or r['year'] in (None, rel.year)):
            hits.append({**r, 'root': paths[r['path_id']]})
    return hits[0] if len(hits) == 1 else None


def _episodes(sid: int, name: str, rel_path: str) -> Tuple[Optional[int], List[int]]:
    r = tv_release.parse_file(os.path.basename(name), rel_path)
    if r.season is None and not r.episodes and not r.air_date:
        for part in reversed([p for p in (rel_path or '').replace('\\', '/').split('/') if p][:-1]):
            pr = tv_release.parse(part)                   # a daily file inside a dated release folder
            if pr.air_date:
                r = pr
                break
    if r.season is None and r.air_date and not r.episodes:
        return _daily_episodes(sid, r.air_date) if _is_daily(sid) else (None, [])          # daily shows (Sonarr's daily type): match by air date
    if r.season is None or not r.episodes:
        return None, []
    with tv_store.connect() as conn:
        have = {x['episode_number'] for x in conn.execute(
            'SELECT episode_number FROM episodes WHERE series_id = ? AND season_number = ?', (sid, r.season))}
    eps = [e for e in r.episodes if e in have]
    return (r.season, eps) if eps and len(eps) == len(r.episodes) else (None, [])


def _is_daily(sid: Optional[int]) -> bool:
    with tv_store.connect() as conn:
        r = conn.execute('SELECT series_type FROM series WHERE id = ?', (sid,)).fetchone()
    return bool(r) and r['series_type'] == 'daily'


def _daily_episodes(sid: int, air_date: str) -> Tuple[Optional[int], List[int]]:
    """The episode(s) of *sid* that aired on *air_date* (tv.db air_date, exact). Same-day episodes in
    one season are all matched; none, or the date spread over seasons, matches nothing."""
    with tv_store.connect() as conn:
        rows = conn.execute('SELECT season_number, episode_number FROM episodes WHERE series_id = ? AND air_date = ?'
                            ' ORDER BY season_number, episode_number', (sid, air_date)).fetchall()
    seasons = {x['season_number'] for x in rows}
    if len(seasons) != 1:
        return None, []
    return rows[0]['season_number'], [x['episode_number'] for x in rows]


def _show_folder(s: Dict[str, Any]) -> str:
    if s['folder']:
        return s['folder']
    from app import tv_settings
    folder = tv_settings.series_folder(s['title'], s['year'])        # v185: the Series folder format
    if not folder or '/' in folder or folder in ('.', '..'):
        raise mover.Refused('unsafe file name')
    with tv_store.write() as conn:                      # a show added from Search gets its folder now
        taken = conn.execute('SELECT 1 FROM series WHERE path_id IS ? AND folder = ? AND id != ?',
                             (s.get('path_id'), folder, s['id'])).fetchone()
        if taken and s['year']:                         # another show has that folder: never share it
            folder = fm.safe_component(f"{folder} ({s['year']})", fallback='Series')
            taken = conn.execute('SELECT 1 FROM series WHERE path_id IS ? AND folder = ? AND id != ?',
                                 (s.get('path_id'), folder, s['id'])).fetchone()
        if taken:
            raise mover.Refused('another show has this folder')
        conn.execute('UPDATE series SET folder = ? WHERE id = ? AND folder IS NULL', (folder, s['id']))
    return folder


def import_torrent(t: Dict[str, Any], data: Dict[str, Any], now: float,
                   list_files: Optional[Callable] = None) -> Dict[str, Any]:
    """File one completed TV torrent. Returns {state, files, error}. Raises mover.Pending / Refused."""
    from app import tv_watch
    name = str(t.get('name') or '')
    s = _series_for(name)
    if s is None:
        raise mover.Pending(NO_SHOW)
    path_map = data['path_map']
    save_real = os.path.realpath(fm.map_path(str(t.get('save_path') or ''), path_map))
    content = fm.map_path(str(t.get('content_path') or ''), path_map)
    if not content.startswith('/'):
        raise mover.Pending('no download path')
    root = mover._downloads_root()
    if not fm._under(save_real, root):
        raise mover.Pending(mover.NOT_MAPPED)
    lib_root = s['root']
    if fm.blocked(lib_root) or not os.path.isdir(lib_root) or fm.overlaps_downloads(lib_root) \
            or fm._under(root, lib_root) or fm._under(lib_root, root):
        raise mover.Pending('library folder not available')
    if not os.access(lib_root, os.W_OK | os.X_OK):
        raise mover.Pending(NOT_WRITABLE)
    added = float(t.get('added_on') or now)
    if os.path.realpath(content) == save_real:
        listed = list_files(str(t.get('hash') or '')) if list_files else None
        if listed is None:
            raise mover.Pending('waiting for the file list')
        found = mover.find_listed_files(save_real, listed, root, added, now)
    else:
        found = mover.find_video_files(content, root, added, now)
    plan = []
    for f in found:
        rel = os.path.relpath(f.path, os.path.dirname(os.path.realpath(content)))
        season, eps = _episodes(s['id'], f.path, rel)
        if eps:
            plan.append((f, season, eps))
    if not plan:
        raise mover.Refused(NO_EPISODES)
    folder = _show_folder(s)
    owner = mover._owner_of(lib_root)
    mode = 'hardlink' if data.get('transfer') == 'hardlink' else 'copy'
    if mode == 'copy':
        mover._check_space(lib_root, sum(f.size for f, _, _ in plan))
    files = []
    rfd = mover._open_root(lib_root)
    try:
        sfd, _, show_path = mover._ensure_folder(rfd, lib_root, folder, owner)
        try:
            naming = _naming()
            for f, season, nums in plan:
                dfd, dpath, close = sfd, show_path, False
                if s['season_folder']:
                    # v185: the show's own season folder when it has one, else one in its siblings' style
                    sname = tv_naming.season_folder(season, _subdirs(sfd), naming)
                    if not sname or '/' in sname or sname in ('.', '..'):
                        raise mover.Refused('unsafe file name')
                    dfd, _, dpath = mover._ensure_folder(sfd, show_path, sname, owner)
                    close = True
                extra = []
                try:
                    fname = _new_name(s, season, nums, f.path, str(t.get('name') or ''), naming)
                    if not fname or '/' in fname or fname in ('.', '..'):
                        raise mover.Refused('unsafe file name')
                    how = mover.transfer(f.path, dfd, dpath, fname, mode, owner, root, f)
                    if how != 'exists':
                        extra = _sidecars(f, fname, dfd, dpath, mode, owner, root)
                finally:
                    if close:
                        os.close(dfd)
                dest = os.path.join(dpath, fname)
                linked = tv_watch.episode_imported(dest)          # the import hook: the card turns now
                files.append({'path': dest, 'how': how, 'linked': bool(linked), 'episode_ids': _ids(s['id'], season, nums),
                              **({'sidecars': extra} if extra else {})})
        finally:
            os.close(sfd)
    finally:
        os.close(rfd)
    return {'state': 'imported', 'files': files, 'series_id': s['id'],
            'episode_ids': sorted({i for x in files for i in x['episode_ids']})}


def _naming() -> Dict[str, Any]:
    try:
        return tv_settings.load()['naming']
    except Exception:                                   # noqa: BLE001 - unreadable settings: Taylor's Sonarr defaults
        logger.exception('TV import: naming settings unreadable')
        return dict(tv_naming.DEFAULTS)


def _subdirs(fd: int) -> List[str]:
    out = []
    for n in os.listdir(fd):
        try:
            if stat.S_ISDIR(os.stat(n, dir_fd=fd, follow_symlinks=False).st_mode):
                out.append(n)
        except OSError:
            pass
    return out


def _new_name(s: Dict[str, Any], season: int, nums: List[int], path: str, release: str, naming: Dict[str, Any]) -> str:
    """v185: the file's name in the library: the episode format (Rename episodes on), the extension
    kept; the release's own name when renaming is off or the format can't name it."""
    base = os.path.basename(path)
    if not naming.get('rename_episodes'):
        return base
    ext = os.path.splitext(base)[1]
    with tv_store.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT episode_number, title, air_date, absolute_number FROM episodes WHERE series_id = ? AND season_number = ?"
            f" AND episode_number IN ({','.join('?' * len(nums))}) ORDER BY episode_number", (s['id'], season, *nums))]
        st = conn.execute('SELECT series_type FROM series WHERE id = ?', (s['id'],)).fetchone()
    if not rows:
        return base
    rel = tv_release.parse(release or base)
    q = tv_monitor.quality_of(base) or tv_monitor.quality_of(release)
    ctx = {'title': s['title'], 'folder': s.get('folder'), 'year': s.get('year'), 'season': season,
           'episodes': [r['episode_number'] for r in rows], 'absolutes': [r.get('absolute_number') for r in rows],
           'titles': [r['title'] or '' for r in rows], 'air_date': rows[0]['air_date'], 'quality': q,
           'proper': bool(getattr(rel, 'proper', False)), 'group': getattr(rel, 'group', ''), 'release': release or base}
    return tv_naming.episode_name(ctx, ext, (st['series_type'] if st else None) or 'standard', naming) or base


def _sidecars(f, fname: str, dfd: int, dpath: str, mode: str, owner, root: str) -> List[Dict[str, Any]]:
    """v185: subtitles beside the video follow it under its new name (never over an existing file)."""
    out = []
    stem = os.path.splitext(fname)[0]
    for src, suffix in tv_naming.sidecars(f.path):
        if not fm._under(os.path.realpath(src), root):
            continue
        name = tv_naming._fit(stem, suffix)
        try:
            st = os.stat(src, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode):
                continue
            how = mover.transfer(src, dfd, dpath, name, mode, owner, root, mover._found(src, st))
        except (OSError, mover.Refused, mover.Pending, TypeError) as e:
            logger.info('TV import: a sidecar was skipped (%s)', type(e).__name__)
            continue
        out.append({'path': os.path.join(dpath, name), 'how': how})
    return out


def _ids(sid: int, season: int, nums: List[int]) -> List[int]:
    with tv_store.connect() as conn:
        return sorted(r['id'] for r in conn.execute(
            f"SELECT id FROM episodes WHERE series_id = ? AND season_number = ? AND episode_number IN ({','.join('?' * len(nums))})",
            (sid, season, *nums)))


# ---------------------------------------------------------------- the grab row (v184, Tester FF-21)
# A successful import used to stop at the tvdl:<hash> meta: nothing moved the tv.db grabs row on from
# 'downloading' (no state, no finished_at, no 'imported' history event). finish_grab() does it in the
# same pass, in one transaction; reconcile() repairs rows left behind (at the watch's start and on every
# pass), from the import record or, failing that, from the episodes' linked files. Both are idempotent:
# a row only moves out of downloading/importing once, and the history event is written once per hash.

OPEN_GRABS = ('downloading', 'importing')
LINKED = ("EXISTS (SELECT 1 FROM episode_file_links l JOIN episode_files f ON f.id = l.file_id"
          " WHERE l.episode_id = episodes.id AND f.state = 'linked')")


def _want(g) -> List[int]:
    """The row's episode ids: a JSON list of ids (v184), or older shapes (FF-21) (ids as strings, {'id': n}
    items, one bare id)."""
    try:
        raw = json.loads(g['episode_ids'] or '[]') if isinstance(g['episode_ids'], str) else g['episode_ids']
    except (TypeError, ValueError):
        return []
    items = raw if isinstance(raw, list) else [raw]
    out = set()
    for i in items:
        try:
            out.add(int(i['id'] if isinstance(i, dict) else i))
        except (TypeError, ValueError, KeyError):
            pass
    return sorted(out)


def _linked(conn, ids: List[int]) -> set:
    if not ids:
        return set()
    return {r['id'] for r in conn.execute(
        f"SELECT id FROM episodes WHERE id IN ({','.join('?' * len(ids))}) AND {LINKED}", ids)}


def finish_grab(h: str, name: Optional[str], series_id: Optional[int], covered: Optional[List[int]], files: int,
                now: float, via: str = 'import') -> Optional[str]:
    """Mark this torrent's grab row imported (every grabbed episode covered) or partial, with
    finished_at, and write the 'imported' history event. *covered*: the episode ids the import placed
    (None: from the episodes' linked files). The row is found by info hash, else (a grab whose hash
    qBittorrent reports differently) by its exact release name among the open rows. Returns the new
    state, or None when there was no open row (the event is still written once for a Luma import)."""
    h = (h or '').lower()
    with tv_store.write() as conn:
        g = conn.execute('SELECT * FROM grabs WHERE hash = ?', (h,)).fetchone()
        if g is None and name:
            g = conn.execute(f"SELECT * FROM grabs WHERE release_title = ? AND state IN ({','.join('?' * len(OPEN_GRABS))})"
                             ' ORDER BY grabbed_at DESC LIMIT 1', (name, *OPEN_GRABS)).fetchone()
        state = None
        ids = list(covered or [])
        if g is not None and g['state'] in OPEN_GRABS:
            want = _want(g)
            have = set(ids) if covered is not None else _linked(conn, want)
            if covered is None:
                ids = sorted(have)
            state = 'imported' if want and set(want) <= have else 'partial'
            conn.execute(f"UPDATE grabs SET state = ?, finished_at = ?, results = ?, error = NULL WHERE hash = ?"
                         f" AND state IN ({','.join('?' * len(OPEN_GRABS))})",
                         (state, now, json.dumps({'episode_ids': ids, 'files': files, 'via': via}), g['hash'], *OPEN_GRABS))
            series_id = g['series_id'] if g['series_id'] is not None else series_id
            ids = sorted(set(ids) | (set(want) & have))
            from app import tv_scan                       # v211 (PM B): the imported files keep this grab's quality
            for fr in conn.execute(f"SELECT DISTINCT l.file_id FROM episode_file_links l WHERE l.episode_id IN ({','.join('?' * len(ids)) or 'NULL'})",
                                   tuple(ids)).fetchall():
                tv_scan.fill_quality_from_grab(conn, fr['file_id'], ids)
        elif g is not None:
            return None                                       # already finished: nothing to do, no second event
        key = g['hash'] if g is not None else h
        seen = conn.execute("SELECT 1 FROM history WHERE event = 'imported'"
                            " AND (CASE WHEN json_valid(detail) THEN json_extract(detail, '$.hash') END) = ?", (key,)).fetchone()
        if not seen and (g is not None or via == 'import'):
            detail = {'hash': key, 'title': (g['release_title'] if g is not None else None) or name, 'files': files, 'via': via}
            if g is not None:
                detail.update(origin=g['origin'], state=state)
            conn.execute('INSERT INTO history (ts, series_id, episode_ids, event, detail) VALUES (?, ?, ?, ?, ?)',
                         (now, series_id, json.dumps(ids), 'imported', json.dumps(detail)))
    if state:
        logger.info('TV import: grab %s marked %s (%s)', name or h, state, via)
    return state


def reconcile(now: Optional[float] = None) -> int:
    """Self-heal (v184): a grab still downloading/importing whose torrent this watch already imported
    (tvdl:<hash> says imported), or whose every episode now has a linked file, is finished now.
    DB only (never contacts qBittorrent). Returns how many rows moved on."""
    now = time.time() if now is None else now
    with tv_store.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT hash, series_id, episode_ids, release_title FROM grabs WHERE state IN ({','.join('?' * len(OPEN_GRABS))})",
            OPEN_GRABS)]
        linked = {r['hash']: _linked(conn, _want(r)) for r in rows}
    n = 0
    for r in rows:
        want = _want(r)
        st = _import_record(r)
        if st.get('state') == 'imported':
            # FF-21 (v185.2 on v217): an open row whose OWN torrent has an 'imported' record is closed now, with
            # finished_at and one history event, even when the file is gone (deleted on purpose) or was never
            # linked (a pre-v184 record without episode_ids). DB only: the grab row and its event; no file
            # operation, no re-import, no search. (v184 waited for the link, so such rows stayed 'downloading'.)
            cov = _covered(st)
            if cov is None:
                cov = sorted(set(want) | linked[r['hash']]) if want else None
            got = finish_grab(r['hash'], r['release_title'], r['series_id'], cov,
                              len(st.get('files') or []), float(st.get('at') or now), via='reconciled')
        elif want and set(want) <= linked[r['hash']]:
            got = finish_grab(r['hash'], r['release_title'], r['series_id'], None, 0, now, via='reconciled')
        else:
            continue
        n += 1 if got else 0
    return n


def _import_record(r: Dict[str, Any]) -> Dict[str, Any]:
    """This grab row's OWN import record: by its hash (as stored, or lowercased: pre-v184 rows may keep the
    tracker's uppercase hash). v185.2 (Tester BLOCK, PM): never by release name and never across hashes,
    so a new grab of a release imported before under another hash stays open and imports when it finishes."""
    h = str(r.get('hash') or '')
    return status(h) or (status(h.lower()) if h != h.lower() else None) or {}


def _covered(st: Dict[str, Any]) -> Optional[List[int]]:
    """The episode ids an import record says it placed: v184+ 'episode_ids', else (pre-v184) its files'
    ids; None when the record doesn't say."""
    def ints(xs):
        out = set()
        for x in xs if isinstance(xs, list) else []:
            try:
                out.add(int(x['id'] if isinstance(x, dict) else x))
            except (TypeError, ValueError, KeyError):
                pass
        return out
    if isinstance(st.get('episode_ids'), list):
        return sorted(ints(st['episode_ids']))
    ids = set()
    for f in st.get('files') or []:
        if isinstance(f, dict):
            ids |= ints(f.get('episode_ids'))
    return sorted(ids) if ids else None


def _reconcile_safely(now: Optional[float] = None) -> int:
    try:
        return reconcile(now)
    except Exception:                                        # noqa: BLE001 - never end a pass over the repair
        logger.exception('TV import: grab reconcile failed')
        return 0


ALREADY_IMPORTED = 'already imported'
UNMONITORED = 'episode not monitored'


def _no_import(h: str, name: Optional[str]) -> Optional[str]:
    """v184 (PM): why the watcher must not import this torrent: its grab row is already closed as imported
    (by an earlier pass or by hand; the file may have been deleted on purpose since), or every episode it
    was grabbed for is unmonitored now (or the show is). None: import it."""
    with tv_store.connect() as conn:
        g = conn.execute('SELECT * FROM grabs WHERE hash = ?', (h,)).fetchone()
        if g is None and name:
            # v185 (Reviewer b): a closed row is matched by its hash only; the name fallback is for open rows
            g = conn.execute(f"SELECT * FROM grabs WHERE release_title = ? AND state IN ({','.join('?' * len(OPEN_GRABS))})"
                             ' ORDER BY grabbed_at DESC LIMIT 1', (name, *OPEN_GRABS)).fetchone()
        if g is None:
            return None
        if g['state'] in ('imported', 'partial'):
            return ALREADY_IMPORTED
        ids = _want(g)
        if ids:
            on = conn.execute(f"SELECT COUNT(*) AS n FROM episodes e JOIN series s ON s.id = e.series_id"
                              f" WHERE e.id IN ({','.join('?' * len(ids))}) AND e.monitored = 1 AND s.monitored = 1", ids).fetchone()['n']
            if not on:
                return UNMONITORED
    return None


def tick(now: Optional[float] = None, session_factory: Callable = requests.Session) -> Dict[str, int]:
    """One pass. Never contacts qBittorrent without a TV library or a downloads folder."""
    now = time.time() if now is None else now
    out = {'checked': 0, 'imported': 0}
    if not watch_enabled() or not tv_settings.libraries():
        return out
    _reconcile_safely(now)                                    # v184: rows an earlier import left 'downloading'
    try:
        mover._downloads_root()
    except mover.Pending:
        return out
    if not _lock.acquire(blocking=False):
        return out
    try:
        sess, url = mover._qbit_session(session_factory, now)
        if not sess:
            return out
        try:
            name = category()
            torrents, seen = [], set()
            for params in ({'category': name, 'filter': 'completed'}, {'tag': name, 'filter': 'completed'}):
                try:
                    r = sess.get(f'{url}/api/v2/torrents/info', params=params, timeout=10, allow_redirects=False)
                    got = r.json() if r.status_code == 200 else None
                except (requests.RequestException, ValueError):
                    got = None
                if not isinstance(got, list):
                    return out
                for t in got:
                    if isinstance(t, dict) and str(t.get('hash') or '').lower() not in seen:
                        seen.add(str(t.get('hash') or '').lower())
                        torrents.append(t)
            data = fm.load()
            list_files = mover._files_of(sess, url)
            for t in torrents:
                if not is_mine(t) or not _complete(t):
                    continue                                  # only this instance's TV torrents, never others
                h = str(t.get('hash') or '').lower()
                if not mover._HASH_RE.match(h):
                    continue
                st = status(h) or {}
                if st.get('state') in _DONE and not (st.get('state') == 'refused' and st.get('error') == NO_EPISODES
                                                     and int(st.get('matcher') or 1) < MATCHER
                                                     and tv_release.parse(str(t.get('name') or '')).kind == 'daily'
                                                     and _is_daily((_series_for(str(t.get('name') or '')) or {}).get('id'))):
                    continue
                if st.get('state') == 'waiting' and float(st.get('next_try') or 0) > now:
                    continue                                  # hotfix: an OSError backoff is running
                if st.get('state') == 'failed' and str(st.get('error') or '').startswith('file error ('):
                    continue                                  # hotfix: gave up; the Retry button starts over
                if st.get('state') == 'failed' and st.get('error') == mover.INSUFFICIENT_SPACE:
                    grew = mover.space_retry_due(st, now)     # the mover's rule: once, or Retry
                    if grew is None:
                        continue
                    _save(h, auto_retried=True)
                    logger.info('TV import: %s retried once (free space grew by %d bytes)', t.get('name'), grew)
                why = _no_import(h, t.get('name'))
                if why:                                       # v184 (PM): never again, never for an unmonitored episode
                    if why == ALREADY_IMPORTED:
                        _save(h, state='refused', error=why, at=now, name=t.get('name'))
                    elif st.get('error') != why:
                        _save(h, state='waiting', error=why, at=now, name=t.get('name'))
                        logger.info('TV import: %s not imported (%s)', t.get('name'), why)
                    continue
                out['checked'] += 1
                try:
                    res = import_torrent(t, data, now, list_files)
                    _save(h, **res, at=now, completed=t.get('completion_on'), name=t.get('name'), error=None)
                    out['imported'] += 1
                    logger.info('TV import: %s -> %d file(s)', t.get('name'), len(res['files']))
                    try:                                       # v184 (FF-21): the grab row and its history event
                        finish_grab(h, t.get('name'), res.get('series_id'), res.get('episode_ids'), len(res['files']), now)
                    except Exception:                          # noqa: BLE001 - reconcile() repairs it next pass
                        logger.exception('TV import: %s imported, grab row not updated', t.get('name'))
                except mover.InsufficientSpace as e:
                    _save(h, state='failed', error=mover.INSUFFICIENT_SPACE, at=now, finished=now, name=t.get('name'),
                          space={'path': e.path, 'need': e.need, 'free': mover._free_or_none(e.path)})
                except mover.Refused as e:
                    _save(h, state='refused', error=str(e), at=now, name=t.get('name'), matcher=MATCHER)
                except mover.Pending as e:
                    _save(h, state='waiting', error=str(e), at=now, name=t.get('name'))
                except OSError as e:
                    _os_failure(h, t.get('name'), e, st, now)
                except Exception as e:                        # one bad torrent never ends the pass
                    logger.exception('TV import: %s failed', t.get('name'))
                    _save(h, state='waiting', error=type(e).__name__, at=now, name=t.get('name'))
        finally:
            mover._close(sess)
    finally:
        _lock.release()
    return out


# Hotfix (Reviewer, REQUIRED): an import that keeps hitting the same OSError (ENAMETOOLONG, EACCES, EIO,
# a read-only share) is logged once and backed off (5 min doubling to 6 h); after OS_ERROR_MAX tries
# it is 'failed' with a Retry button, never retried every pass forever.
OS_BACKOFF_S = 300
OS_BACKOFF_MAX_S = 6 * 3600
OS_ERROR_MAX = 6


def _os_failure(h: str, name: Optional[str], e: OSError, st: Dict[str, Any], now: float) -> None:
    import errno as _errno
    code = _errno.errorcode.get(e.errno, '') if e.errno else ''
    why = f'file error ({code or type(e).__name__})'
    n = int(st.get('os_errors') or 0) + 1 if st.get('error') == why else 1
    if n == 1:
        logger.warning('TV import: %s hit %s; backing off', name, why)
    if n >= OS_ERROR_MAX:
        _save(h, state='failed', error=why, at=now, finished=now, name=name, os_errors=n, next_try=None)
        logger.warning('TV import: %s gave up after %d tries (%s); Retry to try again', name, n, why)
        return
    _save(h, state='waiting', error=why, at=now, name=name, os_errors=n,
          next_try=now + min(OS_BACKOFF_S * 2 ** (n - 1), OS_BACKOFF_MAX_S))


def retry(h: str) -> bool:
    """The Retry button: a failed or refused torrent is looked at again on the next pass, and a later
    no-space failure gets its one automatic retry again. False when there's nothing to retry."""
    h = (h or '').lower()
    st = status(h)
    if not st or st.get('state') not in ('failed', 'refused', 'waiting'):
        return False
    st.update(state='waiting', error=None, os_errors=0, next_try=None)
    for k in ('auto_retried', 'space', 'finished'):
        st.pop(k, None)
    tv_store.set_meta(_key(h), json.dumps(st))
    return True


GRAB_JOB_DAYS = 7


def failures(now: Optional[float] = None) -> List[Dict[str, Any]]:
    """The TV download jobs that need the owner: torrents the watch couldn't import ({hash, name,
    state 'failed'|'refused', error, at, retry}) and (v158c) grabs that never reached qBittorrent
    (state 'grab_failed', the last GRAB_JOB_DAYS days). retry: the no-space ones (Retry button)."""
    now = time.time() if now is None else now
    out = []
    with tv_store.connect() as conn:
        for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE 'tvdl:%' OR key LIKE 'tvgrab:%'"):
            try:
                v = json.loads(r['value'])
            except ValueError:
                continue
            if not isinstance(v, dict):
                continue
            key = r['key']
            if key.startswith('tvdl:') and v.get('state') in ('failed', 'refused'):
                out.append({'hash': key[5:], 'name': v.get('name'), 'state': v['state'], 'error': v.get('error'),
                            'at': v.get('at'), 'retry': v.get('error') == mover.INSUFFICIENT_SPACE
                            or str(v.get('error') or '').startswith('file error (')})
            elif key.startswith('tvgrab:') and v.get('state') == 'grab_failed' \
                    and now - float(v.get('at') or 0) < GRAB_JOB_DAYS * 86400:
                out.append({'hash': key[7:], 'name': v.get('name'), 'state': 'grab_failed', 'error': v.get('error'),
                            'at': v.get('at'), 'retry': False})
    return sorted(out, key=lambda x: -(x['at'] or 0))


_started = {'on': False}


def start() -> bool:
    if _started['on'] or os.environ.get('LUMA_SKIP_BACKGROUND_THREADS') or not watch_enabled():
        return False
    _started['on'] = True

    def loop():
        _reconcile_safely()                                   # v184: at start, before the first 60s pass
        while True:
            time.sleep(INTERVAL_S)
            try:
                tick()
            except Exception as e:                            # never let one pass end the thread
                logger.error('TV import pass failed: %s', type(e).__name__)
    threading.Thread(target=loop, name='tv-download-watch', daemon=True).start()
    logger.info('TV download watch started (category %s, every %ds)', category(), INTERVAL_S)
    return True
