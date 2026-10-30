"""TV library scan (Phase 1): read the TV library paths, match each top-level
folder to a TMDB show, and record the shows, episodes and files in tv.db.

STRICTLY READ-ONLY on library files. This module only lists folders
(os.scandir) and stats files; it never opens, writes, renames, moves,
deletes, chmods or creates anything inside a library path (no sidecars,
.nfo or image files, no temp files). Everything it learns goes to tv.db in
the data dir. tests/test_tv_scan_readonly.py holds it to that, both by
running a scan over a fixture tree and by reading this file's calls.

* A top-level folder is a show candidate, whatever its naming
  (app/tv_match decides the show). Imported shows are not monitored.
* Files: video files anywhere under the show folder; "Season NN" folders and
  names are read with tv_release.parse_file. The file's own S/E wins; the
  folder only fills in a season the name lacks. Each file is
    linked        at least one of its episodes exists for the show (TMDB's
                  numbering, or the show's episode group when the files are
                  numbered that way; Specials by title; absolute numbers by
                  episode order),
    mismatch      it names an episode, but the numbering doesn't fit,
    unrecognized  no episode could be read from it.
  Files that don't fit are counted per show; they never block it.
* Import: new folders are matched by 8 workers sharing TMDB's rate budget
  (tv_tmdb.wait_turn); "id" and "high" tiers are imported, "likely" and
  "none" wait in review with the best guess (tv_match). TMDB is read in the
  workers, tv.db is written in folder order afterwards.
* Two folders with the same show: the better one keeps it (folder_rank:
  exact title and year, then the most linked files, then naming); the other
  is unmatched ('duplicate') with the show's real TMDB title as its
  candidate. "Match" or "Wrong show?" on either moves the show there.
* A folder or file that is gone loses its rows (rows only). A missing or
  unreadable library path is skipped as a whole, so an unmounted share never
  empties the list; so is an empty one that had shows (M34: an unmounted
  share's mount point), until its folders are back.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import unicodedata
import threading
from contextlib import nullcontext
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from app import tv_match, tv_release, tv_store, tv_tmdb

logger = logging.getLogger(__name__)

MAX_DEPTH = 4                    # show folder / Season / extras / ...
MAX_FILES_PER_SHOW = 5000
REFRESH_EPISODES_S = 24 * 3600
LAST_SCAN_KEY = 'last_scan'
MISSING = 'Folder not found'

WORKERS = 8
GROUPS_TRIED = 3                 # episode groups fetched per show whose files don't fit TMDB's numbering

_Thread = threading.Thread          # the import pool's threads (tests may stub threading.Thread for start())
_scan_lock = threading.Lock()
# scan_path starts (v158c, Reviewer): a running number, and per path the last start's (number,
# folder scanned). detach_path's deferred run uses them to tell whether the repointed folder was
# scanned meanwhile.
_scan_seq = [0]
_path_scans: Dict[str, Tuple[int, str]] = {}
_state: Dict[str, Any] = {'running': False, 'error': None, 'summary': None}
# What the Import progress card shows (status(), polled): folders decided so far.
_progress: Dict[str, Any] = {'total': 0, 'done': 0, 'matched': 0, 'review': 0, 'none': 0, 'ready': True}
_progress_lock = threading.Lock()
# Scheduled scans back off while TMDB fails: 10 min, 20, 40, ... up to the
# rescan interval. Any scan that reaches TMDB resets it; a manual Scan ignores it.
_backoff: Dict[str, float] = {'fails': 0, 'until': 0.0}


# --------------------------------------------------------------------------- listing (read-only)

def _real_under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), root]) == root
    except ValueError:
        return False


def show_folders(root_real: str) -> List[str]:
    """Top-level folders of a library path (no hidden ones, none that point
    outside it). A folder reached twice (a symlink to another show folder, or
    a loop back to the library path) counts once: real folders first, then by
    name, the first to reach a (dev, inode) keeps it."""
    entries = []
    with os.scandir(root_real) as it:
        for e in it:
            if e.name.startswith('.') or _system_dir(e.name):     # v177: volume/NAS folders are never shows
                continue
            try:
                if not e.is_dir(follow_symlinks=True):
                    continue
                if e.is_symlink() and not _real_under(e.path, root_real):
                    continue
                st = e.stat(follow_symlinks=True)
            except OSError:
                continue
            entries.append((e.is_symlink(), e.name.casefold(), e.name, (st.st_dev, st.st_ino)))
    root_st = os.stat(root_real)
    seen = {(root_st.st_dev, root_st.st_ino)}
    out = []
    for _link, _key, name, ident in sorted(entries):
        if ident in seen:
            continue
        seen.add(ident)
        out.append(name)
    return sorted(out, key=str.casefold)


def video_files(show_dir: str, root_real: str) -> Iterator[Tuple[str, os.stat_result]]:
    """(path relative to the show folder, stat) for every video file under it.
    Each folder and file is read once by (dev, inode), so a symlink loop or a
    link to a sibling folder never counts a file twice."""
    n = 0
    stack: List[Tuple[str, str, int]] = [(show_dir, '', 0)]
    dirs_seen: set = set()
    files_seen: set = set()
    while stack:
        d, rel, depth = stack.pop()
        try:
            dst = os.stat(d)
        except OSError:
            continue
        if (dst.st_dev, dst.st_ino) in dirs_seen:
            continue
        dirs_seen.add((dst.st_dev, dst.st_ino))
        try:
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            if e.name.startswith('.'):
                continue
            sub = f'{rel}/{e.name}' if rel else e.name
            try:
                if e.is_symlink() and not _real_under(e.path, root_real):
                    continue
                if e.is_dir(follow_symlinks=True):
                    if depth + 1 < MAX_DEPTH:
                        stack.append((e.path, sub, depth + 1))
                    continue
                if not e.is_file(follow_symlinks=True):
                    continue
                if os.path.splitext(e.name)[1].lower() not in tv_release.VIDEO_EXTS:
                    continue
                st = e.stat(follow_symlinks=True)
            except OSError:
                continue
            if (st.st_dev, st.st_ino) in files_seen:
                continue
            files_seen.add((st.st_dev, st.st_ino))
            yield sub, st
            n += 1
            if n >= MAX_FILES_PER_SHOW:
                return


# --------------------------------------------------------------------------- tv.db

def _series_row(conn, path_id: str, folder: str):
    return conn.execute('SELECT * FROM series WHERE path_id = ? AND folder = ?', (path_id, folder)).fetchone()


def fetch_show(tmdb_id: int, details: Optional[Dict[str, Any]] = None, hints: Optional[tv_match.Hints] = None,
               group_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """What storing a show needs from TMDB (reads only): its details (the
    ones match() already fetched are reused), its seasons (up to 20 a call),
    and the episode group the files are numbered by, if any."""
    data = details if details and isinstance(details.get('seasons'), list) else tv_tmdb.show(tmdb_id)
    if not data:
        return None
    seasons = tv_tmdb.seasons(tmdb_id, tv_match.season_numbers(data))
    group = tv_tmdb.episode_group(group_id) if group_id else pick_group(tmdb_id, seasons, hints)
    return {'details': data, 'seasons': seasons, 'group': group}


def _group_map(group: Optional[Dict[str, Any]]) -> Dict[Tuple[int, int], int]:
    """{(season, episode): TMDB episode id} in a group's numbering."""
    out: Dict[Tuple[int, int], int] = {}
    for g in (group or {}).get('groups') or []:
        n = g.get('order') if isinstance(g, dict) else None
        if not isinstance(n, int):
            continue
        for ep in g.get('episodes') or []:
            if isinstance(ep, dict) and isinstance(ep.get('id'), int) and isinstance(ep.get('order'), int):
                out[(n, ep['order'] + 1)] = ep['id']
    return out


def _fits(numbered: List[Tuple[int, List[int]]], have: set) -> int:
    return sum(1 for s, eps in numbered if any((s, e) in have for e in eps))


def pick_group(tmdb_id: int, seasons: Dict[int, Dict[str, Any]], hints: Optional[tv_match.Hints]) -> Optional[Dict[str, Any]]:
    """The episode group (TVDB, aired, DVD order...) that links the most
    files, when some files don't fit TMDB's own numbering and the group links
    more of them. None otherwise."""
    if not hints or not hints.numbered:
        return None
    default = {(n, ep.get('episode_number')) for n, se in seasons.items() for ep in (se or {}).get('episodes') or []
               if isinstance(ep, dict)}
    base = _fits(hints.numbered, default)
    if base == len(hints.numbered):
        return None
    groups = tv_tmdb.episode_groups(tmdb_id)
    pref = sorted(groups, key=lambda g: (0 if 'tvdb' in str(g.get('name', '')).lower() else 1,
                                         {1: 0, 3: 1, 6: 2, 7: 3, 4: 4, 2: 5}.get(g.get('type'), 9)))
    best, best_n = None, base
    for g in pref[:GROUPS_TRIED]:
        data = tv_tmdb.episode_group(g['id'])
        n = _fits(hints.numbered, set(_group_map(data)))
        if n > best_n:
            best, best_n = data, n
    return best


def store_show(path_id: str, folder: str, tmdb_id: int, now: float, *, via: str,
               event: str = 'matched', series_id: Optional[int] = None, data: Optional[Dict[str, Any]] = None,
               tier: Optional[str] = None, monitor: Optional[str] = None, profile_id: Optional[int] = None) -> Optional[int]:
    """Insert (or repoint *series_id* to) the show *tmdb_id* for a folder,
    with its seasons and episodes (*data* from fetch_show, else fetched now).
    Returns the series id, or None when that show already belongs to another
    folder."""
    data = data or fetch_show(tmdb_id)
    if not data:
        return None
    fields, aliases = tv_match.show_fields(data['details'])
    seasons = data['seasons']
    old = None
    with tv_store.write() as conn:
        other = conn.execute('SELECT id FROM series WHERE tmdb_id = ?', (tmdb_id,)).fetchone()
        if other and other['id'] != series_id:
            return None
        cols = dict(fields, tmdb_id=tmdb_id, aliases=json.dumps(aliases), tmdb_refreshed_at=now, stills_at=now,
                    match_tier=tier or via)
        if series_id is None:
            # v158a: monitored by default (a library folder: Future episodes; Add from Search: the
            # owner's pick, All episodes by default); new seasons on; the default quality profile
            cols.update(path_id=path_id, folder=folder, monitored=0, monitor_new_seasons=1, added_at=now,
                        profile_id=profile_id if profile_id is not None else _default_profile(conn))
            keys = ', '.join(cols)
            cur = conn.execute(f'INSERT INTO series ({keys}) VALUES ({", ".join("?" * len(cols))})', tuple(cols.values()))
            sid = int(cur.lastrowid)
        else:
            old = conn.execute('SELECT tmdb_id, title, monitor FROM series WHERE id = ?', (series_id,)).fetchone()
            conn.execute('DELETE FROM episodes WHERE series_id = ?', (series_id,))
            conn.execute('DELETE FROM seasons WHERE series_id = ?', (series_id,))
            conn.execute(f'UPDATE series SET {", ".join(f"{k} = ?" for k in cols)} WHERE id = ?',
                         (*cols.values(), series_id))
            sid = series_id
        _store_episodes(conn, sid, seasons)
        _store_group(conn, sid, data.get('group'))
        from app import tv_monitor
        if series_id is None:
            tv_monitor.apply(conn, sid, monitor or (tv_monitor.ADD_DEFAULT if event == 'added' else tv_monitor.IMPORT_DEFAULT))
        else:                                   # a rematch: the new show's episodes follow the series' option
            tv_monitor.apply(conn, sid, monitor or (old['monitor'] if old and old['monitor'] else tv_monitor.IMPORT_DEFAULT))
        detail = {'via': via, 'folder': folder, 'path_id': path_id, 'tmdb_id': tmdb_id, 'title': fields['title']}
        if event == 'rematched' and old:
            detail.update(from_tmdb_id=old['tmdb_id'], from_title=old['title'])
        conn.execute('INSERT INTO history (ts, series_id, episode_ids, event, detail) VALUES (?, ?, ?, ?, ?)',
                     (now, sid, '[]', event, json.dumps(detail)))
        conn.execute('DELETE FROM unmatched WHERE path_id = ? AND folder = ?', (path_id, folder))
    return sid


def _default_profile(conn) -> Optional[int]:
    r = conn.execute('SELECT id FROM quality_profiles WHERE is_default = 1').fetchone()
    return r['id'] if r else None


def _store_group(conn, sid: int, group: Optional[Dict[str, Any]]) -> None:
    """The show's files follow *group*'s numbering (None: TMDB's own)."""
    conn.execute('DELETE FROM episode_numbering WHERE series_id = ?', (sid,))
    if not group:
        conn.execute("UPDATE series SET episode_order = 'tmdb', episode_group_id = NULL WHERE id = ?", (sid,))
        return
    ids = {r['tmdb_episode_id']: r['id'] for r in conn.execute(
        'SELECT id, tmdb_episode_id FROM episodes WHERE series_id = ? AND tmdb_episode_id IS NOT NULL', (sid,))}
    for (n, e), tid in _group_map(group).items():
        if tid in ids:
            conn.execute('INSERT OR REPLACE INTO episode_numbering (series_id, season_number, episode_number, episode_id)'
                         ' VALUES (?, ?, ?, ?)', (sid, n, e, ids[tid]))
    conn.execute("UPDATE series SET episode_order = 'group', episode_group_id = ? WHERE id = ?",
                 (str(group.get('id') or '')[:64] or None, sid))


def _store_episodes(conn, sid: int, seasons: Dict[int, Dict[str, Any]]) -> None:
    for n, s in seasons.items():
        conn.execute('INSERT INTO seasons (series_id, season_number, monitored, name, poster_path) VALUES (?, ?, 0, ?, ?)'
                     ' ON CONFLICT(series_id, season_number) DO UPDATE SET name = excluded.name,'
                     ' poster_path = excluded.poster_path',
                     (sid, n, str(s.get('name') or '') or None,
                      s.get('poster_path') if isinstance(s.get('poster_path'), str) else None))
        for ep in s.get('episodes') or []:
            if not isinstance(ep, dict):
                continue
            num = ep.get('episode_number')
            if not isinstance(num, int) or isinstance(num, bool) or num < 0:
                continue
            conn.execute(
                'INSERT INTO episodes (series_id, season_number, episode_number, tmdb_episode_id, title, overview,'
                ' air_date, runtime, still_path, monitored) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)'
                ' ON CONFLICT(series_id, season_number, episode_number) DO UPDATE SET'
                ' tmdb_episode_id = excluded.tmdb_episode_id, title = excluded.title, overview = excluded.overview,'
                ' air_date = excluded.air_date, runtime = excluded.runtime, still_path = excluded.still_path',
                (sid, n, num, ep.get('id') if isinstance(ep.get('id'), int) else None,
                 str(ep.get('name') or '')[:300] or None, str(ep.get('overview') or '')[:2000] or None,
                 ep.get('air_date') if isinstance(ep.get('air_date'), str) else None,
                 ep.get('runtime') if isinstance(ep.get('runtime'), int) else None,
                 _image_path(ep.get('still_path'))))


def _image_path(v: Any) -> Optional[str]:
    """A TMDB image path ('/abc.jpg'), else None."""
    if isinstance(v, str) and v.startswith('/') and len(v) <= 200 and v.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
        return v
    return None


def fetch_for_refresh(sid: int, tmdb_id: int) -> Optional[Dict[str, Any]]:
    """The TMDB half of refresh_episodes (network, no tv.db writes): run it before taking _scan_lock."""
    with tv_store.connect() as conn:
        row = conn.execute('SELECT episode_group_id FROM series WHERE id = ?', (sid,)).fetchone()
    return fetch_show(tmdb_id, group_id=row['episode_group_id'] if row else None)


def refresh_episodes(sid: int, tmdb_id: int, now: float, data: Optional[Dict[str, Any]] = None) -> None:
    if data is None:
        data = fetch_for_refresh(sid, tmdb_id)
    if not data:
        return
    with tv_store.write() as conn:
        # a rematch mid-refresh may have given this row another show (or removed it): the episodes read
        # for *tmdb_id* belong to that show only, so re-check id AND tmdb_id in this transaction
        if not conn.execute('SELECT 1 FROM series WHERE id = ? AND tmdb_id = ?', (sid, tmdb_id)).fetchone():
            return
        from app import tv_monitor
        before = tv_monitor.known(conn, sid)
        _store_episodes(conn, sid, data['seasons'])
        tv_monitor.new_episodes(conn, sid, before)     # episodes TMDB added since inherit the series' rule
        if data.get('group') is not None:
            _store_group(conn, sid, data['group'])
        conn.execute('UPDATE series SET tmdb_refreshed_at = ?, stills_at = ? WHERE id = ?', (now, now, sid))


def _skip_owned(pick: Optional[int], cands: List[Dict[str, Any]], owned: set) -> Tuple[Optional[int], List[Dict[str, Any]]]:
    """A guess another folder already owns isn't this folder's show (Tester on 0a022bf: The Office
    -> 2001 UK, The Staircase -> 2004): the next candidate of the same title leads instead, else no
    pick (unchecked, the owned guess shown first)."""
    lead = pick if pick is not None else (cands[0]['tmdb_id'] if cands else None)
    if lead is None or lead not in owned:
        return pick, cands
    name = tv_match.normalize(next((c.get('title') for c in cands if c.get('tmdb_id') == lead), '') or '')
    alt = next((c for c in cands if c.get('tmdb_id') not in owned and name
                and tv_match.normalize(c.get('title') or '') == name), None)
    if alt is None:
        return None, cands
    return (alt['tmdb_id'] if pick is not None else None), [alt] + [c for c in cands if c is not alt]


def _store_unmatched(path_id: str, folder: str, m: Dict[str, Any], now: float, reason: Optional[str] = None) -> None:
    with tv_store.write() as conn:
        _store_unmatched_conn(conn, path_id, folder, m, now, reason)


def _store_unmatched_conn(conn, path_id: str, folder: str, m: Dict[str, Any], now: float,
                          reason: Optional[str] = None) -> None:
    reason = reason or m.get('reason') or 'not_exact'
    cands = m.get('candidates') or []
    if reason == 'duplicate':
        tier, pick = 'duplicate', (cands[0]['tmdb_id'] if cands else None)
    else:
        tier = 'likely' if m.get('tier') == 'likely' and m.get('pick') else 'none'
        pick = m.get('pick') if tier == 'likely' else None
        if tier == 'likely' and reason == 'unclear':
            # nothing backs the guess: no pick (Import all can't import it unseen); it leads the candidates
            cands = sorted(cands, key=lambda c: c.get('tmdb_id') != pick)
            pick = None
    conn.execute('INSERT INTO unmatched (path_id, folder, guess_title, guess_year, candidates, reason, ts, tier, pick)'
                 ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(path_id, folder) DO UPDATE SET'
                 ' guess_title = excluded.guess_title, guess_year = excluded.guess_year,'
                 ' candidates = excluded.candidates, reason = excluded.reason, ts = excluded.ts,'
                 ' tier = excluded.tier, pick = excluded.pick',
                 (path_id, folder, m['guess']['title'], m['guess']['year'],
                  json.dumps(cands, separators=(',', ':')), reason, now, tier, pick))


# --------------------------------------------------------------------------- files

def _episode(conn, sid: int, season: int, episode: int, grouped: bool) -> Optional[int]:
    if grouped:
        row = conn.execute('SELECT episode_id FROM episode_numbering WHERE series_id = ? AND season_number = ?'
                           ' AND episode_number = ?', (sid, season, episode)).fetchone()
        if row:
            return row['episode_id']
    row = conn.execute('SELECT id FROM episodes WHERE series_id = ? AND season_number = ? AND episode_number = ?',
                       (sid, season, episode)).fetchone()
    return row['id'] if row else None


def _special_by_title(conn, sid: int, name: str) -> Optional[int]:
    """A Specials file whose number doesn't fit: the season-0 episode whose
    title is in the file name (like Sonarr's special-title parse)."""
    stem = tv_match.normalize(os.path.splitext(name)[0].rsplit(' - ', 1)[-1] if ' - ' in name
                              else os.path.splitext(name)[0])
    if len(stem) < 4:
        return None
    hits = [r['id'] for r in conn.execute('SELECT id, title FROM episodes WHERE series_id = ? AND season_number = 0',
                                          (sid,))
            if (t := tv_match.normalize(r['title'])) and len(t) >= 4 and (t == stem or t in stem or stem in t)]
    return hits[0] if len(hits) == 1 else None


def _absolute(conn, sid: int, numbers: List[int]) -> List[int]:
    """Episode ids for absolute numbers: the episodes' own absolute number,
    else their order across the regular seasons."""
    out = []
    order = None
    for a in numbers:
        row = conn.execute('SELECT id FROM episodes WHERE series_id = ? AND absolute_number = ?', (sid, a)).fetchone()
        if row:
            out.append(row['id'])
            continue
        if order is None:
            order = [r['id'] for r in conn.execute('SELECT id FROM episodes WHERE series_id = ? AND season_number > 0'
                                                   ' AND season_number < 1000 ORDER BY season_number, episode_number',
                                                   (sid,))]
        if 0 < a <= len(order):
            out.append(order[a - 1])
    return out


def classify(conn, sid: int, rel_in_show: str) -> Tuple[str, tv_release.Release, List[int]]:
    """(state, parsed, episode ids) for one file of show *sid*. The file's
    own season and episode win over its Season folder."""
    name = rel_in_show.rsplit('/', 1)[-1]
    r = tv_release.parse_file(name, rel_in_show)
    grouped = bool(conn.execute("SELECT 1 FROM series WHERE id = ? AND episode_order = 'group'", (sid,)).fetchone())
    if r.kind in ('episode', 'multi') and r.season is not None and r.episodes:
        ids = [i for i in (_episode(conn, sid, r.season, e, grouped) for e in r.episodes) if i is not None]
        if not ids and r.season == 0:
            sp = _special_by_title(conn, sid, name)
            ids = [sp] if sp else []
        return ('linked', r, ids) if ids else ('mismatch', r, [])
    if r.kind == 'absolute' and r.absolute:
        ids = _absolute(conn, sid, r.absolute)
        return ('linked', r, ids) if ids else ('mismatch', r, [])
    if r.kind == 'daily' and r.air_date:
        row = conn.execute('SELECT id FROM episodes WHERE series_id = ? AND air_date = ?', (sid, r.air_date)).fetchone()
        return ('linked', r, [row['id']]) if row else ('mismatch', r, [])
    if tv_release.season_from_folder(rel_in_show) == 0:
        sp = _special_by_title(conn, sid, name)
        if sp:
            return 'linked', r, [sp]
    return 'unrecognized', r, []


def list_files(folder: str, root_real: str) -> List[Tuple[str, os.stat_result]]:
    """The walk half of scan_files (filesystem only, no tv.db): run it before taking _scan_lock."""
    return list(video_files(os.path.join(root_real, folder), root_real))


def scan_files(sid: int, path_id: str, folder: str, root_real: str, now: float,
               found: Optional[List[Tuple[str, os.stat_result]]] = None) -> Dict[str, int]:
    """Link one show's files. *found* is a list_files() walk done earlier (outside the scan lock);
    without it the folder is walked here."""
    if found is None:
        found = list_files(folder, root_real)
    counts = {'linked': 0, 'mismatch': 0, 'unrecognized': 0}
    with tv_store.write() as conn:
        keep = set()
        for rel, st in found:
            rel_path = f'{folder}/{rel}'
            keep.add(rel_path)
            state, r, ids = classify(conn, sid, rel)
            counts[state] += 1
            parsed = json.dumps({'summary': r.summary(), 'kind': r.kind, 'season': r.season, 'episodes': r.episodes,
                                 'air_date': r.air_date})
            conn.execute(
                'INSERT INTO episode_files (series_id, path_id, rel_path, size, mtime, dev, ino, resolution,'
                ' release_group, proper, origin, added_at, state, parsed)'
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scan', ?, ?, ?)"
                ' ON CONFLICT(path_id, rel_path) DO UPDATE SET series_id = excluded.series_id, size = excluded.size,'
                ' mtime = excluded.mtime, dev = excluded.dev, ino = excluded.ino,'
                ' resolution = COALESCE(excluded.resolution, episode_files.resolution),'
                ' release_group = excluded.release_group, proper = excluded.proper, state = excluded.state,'
                ' parsed = excluded.parsed',
                (sid, path_id, rel_path, st.st_size, st.st_mtime, st.st_dev, st.st_ino, r.resolution or None,
                 r.group or None, int(r.proper or 0), now, state, parsed))
            fid = conn.execute('SELECT id FROM episode_files WHERE path_id = ? AND rel_path = ?',
                               (path_id, rel_path)).fetchone()['id']
            conn.execute('DELETE FROM episode_file_links WHERE file_id = ?', (fid,))
            for eid in ids:
                conn.execute('INSERT OR IGNORE INTO episode_file_links (file_id, episode_id) VALUES (?, ?)', (fid, eid))
            if ids:
                fill_quality_from_grab(conn, fid, ids)
        # the show folder itself unreachable (a share dropped): not a deletion, keep the rows for the next pass
        reachable = os.path.isdir(os.path.join(root_real, folder))
        for row in conn.execute('SELECT id, rel_path, state, size FROM episode_files WHERE series_id = ?', (sid,)).fetchall():
            if row['rel_path'] not in keep and reachable:
                gone = [r['episode_id'] for r in conn.execute('SELECT episode_id FROM episode_file_links WHERE file_id = ?', (row['id'],))]
                conn.execute('DELETE FROM episode_files WHERE id = ?', (row['id'],))
                if row['state'] == 'linked' and gone:
                    # v184 (Ops): a file the last pass had is gone from disk (deleted outside Luma): say so
                    conn.execute('INSERT INTO history (ts, series_id, episode_ids, event, detail) VALUES (?, ?, ?, ?, ?)',
                                 (now, sid, json.dumps(sorted(gone)), 'deleted',
                                  json.dumps({'rel_path': row['rel_path'], 'size': row['size'], 'via': 'link pass'})))
                    logger.info('TV link: a file of show %s is gone from disk (%s)', sid, row['rel_path'][-120:])
        conn.execute('UPDATE series SET last_scan_at = ? WHERE id = ?', (now, sid))
    return counts


def fill_quality_from_grab(conn, fid: int, episode_ids) -> None:
    """v211 (PM B): a file Luma imported keeps its quality on its episode_files row, from the newest
    imported/partial/importing grab covering its episodes (grabs.quality, else read off release_title), so the
    library never depends on the file name. Fills empty fields only."""
    from app import tv_monitor
    ids = [int(i) for i in episode_ids]
    if not ids:
        return
    row = conn.execute('SELECT resolution, source FROM episode_files WHERE id = ?', (fid,)).fetchone()
    if row is None or (row['resolution'] and row['source']):
        return
    g = conn.execute(
        'SELECT g.quality, g.release_title FROM grabs g WHERE g.state IN (\'imported\', \'partial\', \'importing\', \'downloading\')'
        f" AND EXISTS (SELECT 1 FROM json_each(g.episode_ids) j WHERE CAST(j.value AS INTEGER) IN ({','.join('?' * len(ids))}))"
        ' ORDER BY COALESCE(g.finished_at, g.grabbed_at) DESC LIMIT 1', tuple(ids)).fetchone()
    if g is None:
        return
    q = g['quality'] if g['quality'] in tv_monitor.QUALITY_IDS else tv_monitor.quality_of(str(g['release_title'] or ''))
    if not q:
        return
    res, _, src = q.partition('-')
    conn.execute('UPDATE episode_files SET resolution = COALESCE(resolution, ?), source = COALESCE(source, ?),'
                 ' release_name = COALESCE(release_name, ?) WHERE id = ?', (res or None, src or None, g['release_title'], fid))


# --------------------------------------------------------------------------- duplicates

def _show_brief(row) -> Dict[str, Any]:
    """The show a series row holds, as a picker candidate (its real TMDB title)."""
    return {'tmdb_id': row['tmdb_id'], 'title': row['title'] or '', 'original_title': row['original_title'] or '',
            'year': row['year'], 'poster_path': row['poster_path'], 'overview': (row['overview'] or '')[:500]}


def _linkable(conn, sid: int, folder: str, root_real: str) -> int:
    """How many video files in *folder* would link to show *sid*'s episodes (reads only)."""
    return sum(1 for rel, _ in video_files(os.path.join(root_real, folder), root_real)
               if classify(conn, sid, rel)[0] == 'linked')


def folder_rank(folder: str, show, linked: int) -> Tuple[int, int, int, int]:
    """Which of two folders holding the same show keeps it (higher wins):
    1. match quality: the folder's title and year are the show's (2), its
       title only (1), anything else, e.g. an ID-tagged release name (0);
    2. the most files linked to the show's episodes;
    3. naming: no id tag, then the shorter name."""
    g = tv_match.parse_folder(folder)
    try:
        aliases = json.loads(show['aliases'] or '[]')
    except (ValueError, TypeError):
        aliases = []
    names = {tv_match.normalize(t) for t in [show['title'], show['original_title'], *aliases] if t}
    title_ok = bool(g['title']) and tv_match.normalize(g['title']) in names
    quality = 2 if title_ok and g['year'] is not None and g['year'] == show['year'] else 1 if title_ok and g['year'] is None else 0
    return quality, linked, int(not g['ids']), -len(folder)


def _demote(conn, row, now: float) -> None:
    """Record a show's old folder as an unmatched duplicate, its candidate the show itself."""
    g = tv_match.parse_folder(row['folder'])
    _store_unmatched_conn(conn, row['path_id'], row['folder'],
                          {'guess': {'title': g['title'], 'year': g['year']}, 'candidates': [_show_brief(row)]},
                          now, 'duplicate')


def detach(conn, sid: int, now: float, reason: str) -> None:
    """Take show *sid* off its folder without losing it: the series row, its
    episodes, settings and history stay in tv.db, hidden (no folder). Its file
    rows go (rows only). A folder that matches the show later takes it back,
    history and all (_resolve_duplicate, _apply)."""
    row = conn.execute('SELECT path_id, folder FROM series WHERE id = ?', (sid,)).fetchone()
    if row is None or row['path_id'] is None:
        return
    conn.execute('UPDATE series SET path_id = NULL, folder = NULL WHERE id = ?', (sid,))
    conn.execute('DELETE FROM episode_files WHERE series_id = ?', (sid,))
    conn.execute('INSERT INTO history (ts, series_id, episode_ids, event, detail) VALUES (?, ?, ?, ?, ?)',
                 (now, sid, '[]', 'detached', json.dumps({'reason': reason, 'from_folder': row['folder'],
                                                          'from_path_id': row['path_id']})))


def detach_path(path_id: str, now: Optional[float] = None, wait: Optional[float] = None) -> int:
    """A Library path now points at another folder: its shows are hidden with
    their history kept (detach), its unmatched rows go. The next scan matches
    the new folder's shows, taking back any it already knew. Under _scan_lock (Reviewer on
    49cb7f2): a scan, link or boot re-run never writes rows of the old folder meanwhile. A busy
    lock (*wait*, default LOCK_WAIT_S) hands the detach to a thread that waits its turn; returns
    -1 then.

    The deferred run (Reviewer on 8b55bb8) touches only rows still at the old folder: it does
    nothing when a scan of the repointed folder has started since the request (or was the one
    running then; that scan already dropped the old folder's rows), and it leaves a show that was
    matched by hand into this path after the request."""
    now = time.time() if now is None else now

    def run(deferred: bool = False) -> int:
        with tv_store.write() as conn:
            ids = [r['id'] for r in conn.execute('SELECT id FROM series WHERE path_id = ?', (path_id,))]
            if deferred:
                ids = [i for i in ids if not _matched_since(conn, i, now)]
            for sid in ids:
                detach(conn, sid, now, 'repointed')
            conn.execute('DELETE FROM unmatched WHERE path_id = ?', (path_id,))
        return len(ids)
    if _scan_lock.acquire(timeout=LOCK_WAIT_S if wait is None else wait):
        try:
            return run()
        finally:
            _scan_lock.release()
    # The scan holding the lock now counts as "since": it may already be reading the new folder.
    last = _path_scans.get(path_id)
    since = last[0] if last else _scan_seq[0] + 1

    def later():
        with _scan_lock:
            new = _path_of(path_id)
            new_real = os.path.realpath(new) if new else None
            got = _path_scans.get(path_id)
            if got and got[0] >= since and got[1] == new_real:
                logger.info('Library path %s repointed: its new folder was already scanned, nothing to hide', path_id)
                return
            n = run(deferred=True)
        logger.info('Library path %s repointed: %d TV shows hidden (after the running scan)', path_id, n)
    _Thread(target=later, daemon=True, name='tv-detach').start()
    return -1


def _matched_since(conn, sid: int, ts: float) -> bool:
    return conn.execute("SELECT 1 FROM history WHERE series_id = ? AND event IN ('matched', 'rematched') AND ts >= ?",
                        (sid, ts)).fetchone() is not None


def _manual_here(conn, owner) -> bool:
    """The owner put this show in its current folder by hand (Match or Wrong
    show?): scans leave it there."""
    for r in conn.execute("SELECT detail FROM history WHERE series_id = ? AND event IN ('matched', 'rematched')"
                          ' ORDER BY ts DESC, id DESC LIMIT 1', (owner['id'],)):
        try:
            d = json.loads(r['detail'] or '{}')
        except ValueError:
            return False
        return d.get('via') == 'manual' and d.get('folder') == owner['folder'] and d.get('path_id') == owner['path_id']
    return False


def take_over(owner_id: int, path_id: str, folder: str, now: float, *, via: str, event: str,
              drop_series_id: Optional[int] = None) -> int:
    """Move show *owner_id* (its episodes, settings and history) to *folder*;
    its old folder becomes an unmatched duplicate. *drop_series_id* is the
    folder's own series row (Wrong show?): it is detached, never deleted, so
    its history stays. Rows only."""
    with tv_store.write() as conn:
        old = conn.execute('SELECT * FROM series WHERE id = ?', (owner_id,)).fetchone()
        if drop_series_id is not None and drop_series_id != owner_id:
            prev = conn.execute('SELECT tmdb_id, title FROM series WHERE id = ?', (drop_series_id,)).fetchone()
            detach(conn, drop_series_id, now, 'rematched')
        else:
            prev = None
        conn.execute('UPDATE series SET path_id = ?, folder = ? WHERE id = ?', (path_id, folder, owner_id))
        conn.execute('DELETE FROM unmatched WHERE path_id = ? AND folder = ?', (path_id, folder))
        if old['path_id'] is not None and old['folder'] is not None:
            _demote(conn, old, now)
        detail = {'via': via, 'folder': folder, 'path_id': path_id, 'tmdb_id': old['tmdb_id'], 'title': old['title'],
                  'from_folder': old['folder'], 'from_path_id': old['path_id']}
        if prev is not None:
            detail.update(from_tmdb_id=prev['tmdb_id'], from_title=prev['title'])
        conn.execute('INSERT INTO history (ts, series_id, episode_ids, event, detail) VALUES (?, ?, ?, ?, ?)',
                     (now, owner_id, '[]', event, json.dumps(detail)))
    return owner_id


def _resolve_duplicate(owner, pid: str, folder: str, root_real: str, now: float, via: str) -> Optional[int]:
    """A scan found *folder* holding the show *owner* already has. The better
    folder (folder_rank) keeps it; the other is an unmatched duplicate whose
    candidate is the show's real TMDB title. The show id when *folder* won.
    A show whose Library path was removed moves here outright, keeping its
    tv.db row; its file rows from that library are dropped (rows only).
    M33 (v167): a show whose library is only deselected (still a Library path)
    stays where it is: this folder is an unmatched duplicate, so reselecting
    the library brings the show back with its folder, files and history."""
    if owner['path_id'] is not None and owner['folder'] is not None and _path_of(owner['path_id']) is None \
            and _is_library_path(owner['path_id']):
        g = tv_match.parse_folder(folder)
        _store_unmatched(pid, folder, {'guess': {'title': g['title'], 'year': g['year']},
                                       'candidates': [_show_brief(owner)]}, now, reason='duplicate')
        return None
    if owner['path_id'] is None or owner['folder'] is None or _path_of(owner['path_id']) is None:
        # hidden, added from Search (no folder yet), or its Library path is gone: the folder takes it
        sid = take_over(owner['id'], pid, folder, now, via=via, event='matched')
        with tv_store.write() as conn:
            conn.execute('DELETE FROM episode_files WHERE series_id = ? AND path_id IS NOT ?', (sid, pid))
        return sid
    with tv_store.connect() as conn:
        manual = _manual_here(conn, owner)
    if manual:                                 # the owner's pick stays (Tester M18)
        g = tv_match.parse_folder(folder)
        _store_unmatched(pid, folder, {'guess': {'title': g['title'], 'year': g['year']},
                                       'candidates': [_show_brief(owner)]}, now, reason='duplicate')
        return None
    owner_root = os.path.realpath(_path_of(owner['path_id']) or '')
    with tv_store.connect() as conn:
        mine = folder_rank(folder, owner, _linkable(conn, owner['id'], folder, root_real))
        theirs_linked = conn.execute("SELECT COUNT(*) AS n FROM episode_files WHERE series_id = ? AND state = 'linked'",
                                     (owner['id'],)).fetchone()['n']
        if not theirs_linked and owner_root and os.path.isdir(os.path.join(owner_root, owner['folder'])):
            theirs_linked = _linkable(conn, owner['id'], owner['folder'], owner_root)
    theirs = folder_rank(owner['folder'], owner, theirs_linked)
    if mine > theirs:
        return take_over(owner['id'], pid, folder, now, via=via, event='matched')
    g = tv_match.parse_folder(folder)
    _store_unmatched(pid, folder, {'guess': {'title': g['title'], 'year': g['year']},
                                   'candidates': [_show_brief(owner)]}, now, reason='duplicate')
    return None


def _is_library_path(path_id: str) -> bool:
    """*path_id* is still one of the Library paths (selected for TV or not)."""
    from app import file_management as fm
    try:
        return any(p.get('id') == path_id for p in fm.load().get('paths') or [])
    except Exception:                                        # noqa: BLE001 - unreadable: keep the show where it is
        return True


def _path_of(path_id: str) -> Optional[str]:
    from app import tv_settings
    return next((p['path'] for p in tv_settings.libraries() if p['id'] == path_id), None)


# --------------------------------------------------------------------------- scan

def _hints(root_real: str, folder: str) -> tv_match.Hints:
    return tv_match.file_hints(rel for rel, _ in video_files(os.path.join(root_real, folder), root_real))


# Taylor via PM (v157): Luma imports its top TMDB candidate for every folder with any result; only
# no-result folders and duplicates (the show is another folder's) stay in review.
AUTO_IMPORT = True
AUTO_REASONS = ('not_exact',)          # 'unclear' (a true tie, or a partial title the files don't back) stays in review


def auto_pick(m: Dict[str, Any]) -> Dict[str, Any]:
    """An unmatched result with a top candidate becomes an import of it (tier 'auto')."""
    if not AUTO_IMPORT or m.get('status') != 'unmatched' or m.get('reason') not in AUTO_REASONS:
        return m
    pick = m.get('pick')                      # Luma's ranked pick (prepick / brand), never TMDB's raw first
    if not pick:
        return m
    details = m.get('details') if m.get('pick') == pick else None
    return {**m, 'status': 'matched', 'tmdb_id': pick, 'pick': pick, 'details': details, 'tier': 'auto',
            'via': 'auto', 'auto_reason': m.get('reason')}


def _tick(tier: Optional[str]) -> None:
    with _progress_lock:
        _progress['done'] += 1
        key = {'id': 'matched', 'high': 'matched', 'known': 'matched', 'auto': 'matched', 'likely': 'review',
               'duplicate': 'review'}.get(tier or '', 'none')
        _progress[key] += 1


def _move(src: str, dst: str) -> None:
    """A folder counted in one chip turned out another at commit (a duplicate)."""
    with _progress_lock:
        _progress[src] -= 1
        _progress[dst] += 1


def _work(job: Tuple[str, str, Optional[Dict[str, Any]]], root_real: str) -> Dict[str, Any]:
    """One folder's TMDB reads (in a worker): match a new folder and fetch
    the show it imports, or refresh a known show's episodes. Reads only."""
    kind, folder, row = job
    try:
        if kind == 'match':
            hints = _hints(root_real, folder)
            m = auto_pick(tv_match.match(folder, hints))
            data = None
            if m['status'] == 'matched':
                data = fetch_show(m['tmdb_id'], m.get('details'), hints)
            _tick(m.get('tier') if m['status'] == 'matched' or m.get('tier') != 'none' else 'none')
            return {'m': m, 'data': data}
        if kind == 'refresh':
            with tv_tmdb.fresh():          # the daily refresh never reads yesterday's answer back
                data = fetch_show(row['tmdb_id'], None, _hints(root_real, folder), row.get('episode_group_id'))
            _tick('known')
            return {'data': data}
        _tick('known' if kind == 'known' else 'none')
        return {}
    except tv_tmdb.TmdbError as e:
        return {'error': e}


def run_pool(jobs: List[Any], fn: Callable[[Any], Dict[str, Any]]) -> List[Dict[str, Any]]:
    """fn(job) for every job on WORKERS threads (TMDB's rate budget is shared
    in tv_tmdb), results in job order. After a TmdbError the jobs not yet
    started are skipped ({'skipped': True})."""
    stop = threading.Event()

    def one(job):
        if stop.is_set():
            return {'skipped': True}
        out = fn(job)
        if 'error' in out:
            stop.set()
        return out
    if len(jobs) <= 1:
        return [one(j) for j in jobs]
    out: List[Optional[Dict[str, Any]]] = [None] * len(jobs)
    nxt = iter(range(len(jobs)))
    lock = threading.Lock()

    def worker():
        while True:
            with lock:
                i = next(nxt, None)
            if i is None:
                return
            try:
                out[i] = one(jobs[i])
            except Exception as e:          # anything else: the scan fails, as a serial one would
                out[i] = {'crash': e}
                stop.set()
    threads = [_Thread(target=worker, daemon=True, name=f'tv-import-{n}') for n in range(min(WORKERS, len(jobs)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    crash = next((r['crash'] for r in out if r and 'crash' in r), None)
    if crash is not None:
        raise crash
    return [r if r is not None else {'skipped': True} for r in out]


EMPTY = 'Folder is empty (not mounted?)'
_empty_logged: Dict[str, str] = {}
# v169 (Reviewer): folders a bare mount point or a NAS volume has with no shows on it. They don't make
# a library path "not empty" (.Trash-* is hidden, so show_folders never lists it; it is named anyway).
# v177 (Reviewer, REQUIRED): the usual Windows, macOS and NAS (Synology, QNAP, NetApp, ZFS) volume folders
# too, casefolded. Never a plain word a show could be named ('temp', 'snapshot', 'recycle'). Hidden
# (dot) folders never count as shows either: show_folders skips them; the dotted names here are for
# _system_dir callers that see raw names.
SYSTEM_DIRS = frozenset((
    'lost+found', '@eadir', '#recycle',
    'system volume information', '$recycle.bin', 'recycler', '$recycler', 'found.000',
    '#snapshot', '@recycle', '@recently-snapshot', '@sharebin', '@tmp', '@__thumb', '.@__thumb',
    '.snapshot', '.snapshots', '.zfs', '.trashes', '.trash', '.appledouble', '.ds_store',
    '.spotlight-v100', '.fseventsd', '.temporaryitems', '.recycle', '.@__qini',
))
MOUNT_META = 'tv_mount:'                     # + path id: the mount point the path was on at its last good scan
SKIPPED_META = 'tv_skipped:'                 # + path id: v170, the last scan skipped it (empty or system folders only)
ROOT_META = 'tv_root:'                       # + path id: v171, the folder its last good scan read ('-' once repointed)
REPOINTED = '-'
DEV_META = 'tv_dev:'                         # + path id: v174, the device id (st_dev) of its folder at its last good scan
COVERAGE_META = 'tv_coverage:'               # + path id: v173, the folder set the coverage guard keeps seeing
# v173 (PM): the coverage guard releases once the same folder set (mount up) has been seen for
# COVERAGE_RELEASE_SCANS scans spanning COVERAGE_RELEASE_SPAN_S, or for COVERAGE_RELEASE_AGE_S
COVERAGE_RELEASE_SCANS = 3
COVERAGE_RELEASE_SPAN_S = 3600
COVERAGE_RELEASE_AGE_S = 86400


def _system_dir(name: str) -> bool:
    """A volume or OS folder, never a show: SYSTEM_DIRS (casefolded), .Trash-<uid> and any hidden folder."""
    n = name.casefold()
    return n in SYSTEM_DIRS or n.startswith('.')


def _mount_of(path: str) -> str:
    """The mount point *path* is on (itself when it is one)."""
    p = os.path.realpath(path)
    while not os.path.ismount(p):
        up = os.path.dirname(p)
        if up == p:
            break
        p = up
    return p


def _dropped_mount(pid: str, root_real: str) -> bool:
    """The library path was on a mount between it and '/' at its last good scan, and that mount is
    gone now (os.path.ismount says so): a dropped NAS mount, whatever the bare folder underneath holds."""
    try:
        was = tv_store.get_meta(MOUNT_META + pid)
    except Exception:                        # noqa: BLE001 - no record: no signal
        return False
    if not was:
        return False
    now = _mount_of(root_real)
    if was == now:
        return False
    under = root_real == was or root_real.startswith(was.rstrip('/') + '/')
    return under and (now == '/' or was.startswith(now.rstrip('/') + '/')) and not os.path.ismount(was)


def forget_mount(pid: str) -> None:
    """v170 (Reviewer): drop the recorded mount of library path *pid* (edited to another folder or
    removed), so an old mount can never make its next folder look dropped."""
    try:
        tv_store.set_meta(MOUNT_META + pid, None)
        tv_store.set_meta(DEV_META + pid, None)          # v174: and its device id
        tv_store.set_meta(SKIPPED_META + pid, None)
        tv_store.set_meta(ROOT_META + pid, REPOINTED)      # its rows are the old folder's: no coverage guard
    except Exception as e:                   # noqa: BLE001 - only a signal
        logger.warning('TV scan: mount record of %s not cleared (%s)', pid, type(e).__name__)


def _path_of(pid: str) -> Optional[str]:
    try:
        from app import file_management as fm
        rec = next((p for p in fm.load().get('paths', []) if p.get('id') == pid), None)
        return rec.get('path') if rec else None
    except Exception:                        # noqa: BLE001 - unknown path: no folder to check
        return None


def rebaseline(pid: str) -> bool:
    """v175 (PM): the owner saved library path *pid* again with the same folder. Its recorded mount,
    device id and coverage streak are cleared and its rows marked as not yet seen in this folder, so the
    next scan takes the folders there now as the baseline: the coverage and mount guards don't hold it
    (an empty folder still does), shows that are gone detach (history kept), new ones are found, and the
    mount and device are recorded again. Logged at INFO.

    v176 (Reviewer, REQUIRED): the edit form always sends the path, so Save on an Unavailable path came
    here too and cleared the records of a mount that is DOWN, letting the next scan take a stray folder on
    the bare mount point as the library. Now, when the recorded mount is down (_mount_down, which includes
    _dropped_mount), the records are kept and nothing is re-baselined: the save itself still succeeds and
    the path stays Unavailable until it really remounts. PM refinement: it also needs real show folders
    there at save time (an empty or bare mount point keeps the guard). Returns True if it re-baselined."""
    try:
        path = _path_of(pid)
        root_real = os.path.realpath(path) if path else None
        # v176 (PM): only when the path passes the mount-up check at save time (a live mount or the
        # expected device) AND real show folders are there. A down mount or a bare mount point keeps
        # the guard: nothing is cleared and the path stays Unavailable.
        why = None
        if not root_real or not os.path.isdir(root_real):
            why = 'its folder is missing'
        elif _dropped_mount(pid, root_real) or _mount_down(pid, root_real):
            why = 'its recorded mount is down'
        else:
            try:
                shows = [f for f in show_folders(root_real) if not _system_dir(f)]
            except OSError:
                shows = []
            if not shows:
                why = 'it has no show folders (a bare mount point?)'
        if why:
            logger.info('TV library path %s saved again with the same folder but %s: mount and device records '
                        'kept, no new baseline; it stays Unavailable until it really remounts', pid, why)
            return False
        tv_store.set_meta(MOUNT_META + pid, None)
        tv_store.set_meta(DEV_META + pid, None)
        tv_store.set_meta(COVERAGE_META + pid, None)
        tv_store.set_meta(ROOT_META + pid, REPOINTED)     # the next scan's folders are the baseline
        _empty_logged.pop(pid, None)
        logger.info('TV library path %s saved again with the same folder: recorded mount, device id and '
                    'coverage streak cleared; the next scan takes its current folders as the baseline', pid)
        return True
    except Exception as e:                   # noqa: BLE001 - only a signal
        logger.warning('TV scan: library path %s not re-baselined (%s)', pid, type(e).__name__)
        return False


def skipped(pid: str) -> bool:
    """v170 (PM): the last scan skipped library path *pid* as empty (Settings shows 'Unavailable')."""
    try:
        return bool(tv_store.get_meta(SKIPPED_META + pid))
    except Exception:                        # noqa: BLE001
        return False


def _set_skipped(pid: str, on: bool) -> None:
    try:
        if skipped(pid) != on:
            tv_store.set_meta(SKIPPED_META + pid, '1' if on else None)
    except Exception:                        # noqa: BLE001 - only a status
        pass


class EmptyRoot(FileNotFoundError):
    """A library path with no show folders where tv.db has rows for it: skipped, nothing hidden."""


def _has_rows(pid: str) -> bool:
    """tv.db knows folders under library path *pid* (shows or review rows)."""
    with tv_store.connect() as conn:
        return bool(conn.execute('SELECT 1 FROM series WHERE path_id = ? AND folder IS NOT NULL LIMIT 1', (pid,)).fetchone()
                    or conn.execute('SELECT 1 FROM unmatched WHERE path_id = ? LIMIT 1', (pid,)).fetchone())


def _prior_folders(pid: str) -> set:
    """Folders under library path *pid* that tv.db has series or review (unmatched) rows for."""
    with tv_store.connect() as conn:
        return ({r['folder'] for r in conn.execute(
                    'SELECT folder FROM series WHERE path_id = ? AND folder IS NOT NULL', (pid,))}
                | {r['folder'] for r in conn.execute('SELECT folder FROM unmatched WHERE path_id = ?', (pid,))})


def _unavailable(pid: str, root_real: str, shows: List[str], now: Optional[float] = None) -> Optional[str]:
    """Why library path *pid* must be skipped as Unavailable (rows kept, nothing detached, nothing
    matched), or None to scan it.

    * no show folders where tv.db has rows: an unmounted share's bare mount point (M34, v167);
    * v171 (Reviewer): the mount the path was on is gone and none of the folders tv.db knows are
      there: a stray folder on the bare mount point (a 'Leftover Show (2001)') is no reason to scan,
      or it would detach the library and take the stray in;
    * v171 (PM coverage guard): fewer than half of the folders tv.db knows are present: a half-synced
      or wrong share, never a mass detach.
    """
    prior = _prior_folders(pid)
    if not prior:
        return None                                   # a new path: nothing to lose
    if not shows:
        return 'empty'
    present = prior & set(shows)
    if not present and _dropped_mount(pid, root_real):
        return 'dropped mount'
    try:
        last_root = tv_store.get_meta(ROOT_META + pid)
    except Exception:                        # noqa: BLE001 - no record: guard on
        last_root = None
    # The rows are another folder's (the path was edited to point elsewhere): the repoint's own detach
    # handles them, so coverage says nothing. No record yet (an upgrade) keeps the guard on.
    if last_root not in (None, root_real):
        return None
    if len(present) * 2 < len(prior):
        why = f'coverage {len(present)}/{len(prior)}'
        # v174 (Reviewer on c669f76, REQUIRED): the exit is only for a mounted library. With the mount
        # dropped (or the recorded mount not mounted now) the streak resets and nothing is released.
        if _mount_down(pid, root_real):
            if not _live_on_new_device(pid, root_real):
                _clear_coverage(pid)
                return why
            # v175 (PM): a live mount (mounted, not dropped) with real show folders whose device id
            # changed (a remount, a replaced share) restarts the release streak on the new device rather
            # than blocking forever. The bare mount point on its parent's device never counts.
            _restart_on_new_device(pid, root_real)
        return None if _coverage_released(pid, shows, why, now) else why
    return None


def _dev_of(path: str) -> Optional[int]:
    try:
        return os.stat(path).st_dev
    except OSError:
        return None


def _mount_down(pid: str, root_real: str) -> bool:
    """v174 (PM's device-id rule; Reviewer and Tester FF-9 on c669f76): the mount is up only when it is
    the same mount the path's last good scan saw. Down when the mount is dropped (_dropped_mount), the
    recorded mount point is not mounted now, or the path's folder is on another device (st_dev) than at
    that scan (e.g. retired from the NAS to the local disk at the same path). No record (a new path, or
    a path last scanned before v174 for the device id) is no signal; the next good scan records it.
    Editing the path to another folder or removing it clears the records (forget_mount).
    v175 (PM): a device change on a live mount restarts the release streak (_live_on_new_device)."""
    if _dropped_mount(pid, root_real):
        return True
    try:
        was = tv_store.get_meta(MOUNT_META + pid)
        dev = tv_store.get_meta(DEV_META + pid)
    except Exception:                        # noqa: BLE001 - no record: no signal
        return False
    if was and not os.path.ismount(was):
        return True
    if dev and str(_dev_of(root_real)) != str(dev):
        return True
    return False


def _live_on_new_device(pid: str, root_real: str) -> bool:
    """v175 (PM): the path is a live mount on a device other than its last good scan's: not dropped, its
    recorded mount (if any) still mounted, the mount it is on now is a real mount point (not '/'), and
    the folder is on another device than that mount point's parent (a bare mount point sits on its
    parent's device: never live). Only then may a changed device id start a new release streak."""
    if _dropped_mount(pid, root_real):
        return False
    try:
        was = tv_store.get_meta(MOUNT_META + pid)
        dev = tv_store.get_meta(DEV_META + pid)
    except Exception:                        # noqa: BLE001 - no record: not this case
        return False
    if was and not os.path.ismount(was):
        return False
    now_dev = _dev_of(root_real)
    if not dev or now_dev is None or str(now_dev) == str(dev):
        return False
    mount = _mount_of(root_real)
    if mount == '/' or not os.path.ismount(mount):
        return False
    parent_dev = _dev_of(os.path.dirname(mount.rstrip('/')) or '/')
    return parent_dev is not None and now_dev != parent_dev


def _restart_on_new_device(pid: str, root_real: str) -> None:
    """Record the new device id and start the coverage streak again from this scan (INFO, once per
    device change: the next scan sees the recorded device and counts on as usual)."""
    try:
        old = tv_store.get_meta(DEV_META + pid)
        new = _dev_of(root_real)
        tv_store.set_meta(DEV_META + pid, str(new))
        _clear_coverage(pid)
        logger.info('TV scan: library path %s is a live mount on a new device (%s -> %s); '
                    'the release streak starts again on the new device', pid, old, new)
    except Exception as e:                   # noqa: BLE001 - only a signal
        logger.warning('TV scan: library path %s new device not recorded (%s)', pid, type(e).__name__)


def _coverage_released(pid: str, shows: List[str], why: str, now: Optional[float]) -> bool:
    """v173 (PM, Reviewer on 17ecf51): the coverage guard's exit. Folders deleted outside Luma (or a
    single show renamed or replaced) would otherwise keep the path Unavailable forever. The same
    folder set, seen with the mount up for 3 scans spanning an hour, or for 24 h, is the library now:
    the guard lets that scan through (the gone shows detach, new ones are found). Any change in the
    folder set starts the count again."""
    import hashlib
    now = time.time() if now is None else now
    sig = hashlib.sha256('\x00'.join(sorted(shows)).encode('utf-8', 'surrogatepass')).hexdigest()[:32]
    try:
        st = json.loads(tv_store.get_meta(COVERAGE_META + pid) or '{}')
    except Exception:                        # noqa: BLE001 - unreadable: start again
        st = {}
    if not isinstance(st, dict) or st.get('sig') != sig:
        st = {'sig': sig, 'first': now, 'scans': 0}
    try:
        bad = isinstance(st.get('first'), bool) or not float(st.get('first')) <= now
    except (TypeError, ValueError):
        bad = True
    if bad:                                  # v174 (Reviewer): first in the future (a clock step back) or unreadable: start now
        st = {'sig': sig, 'first': now, 'scans': 0}
    st['scans'] = int(st.get('scans') or 0) + 1
    first = float(st.get('first') or now)
    if ((st['scans'] >= COVERAGE_RELEASE_SCANS and now - first >= COVERAGE_RELEASE_SPAN_S)
            or now - first >= COVERAGE_RELEASE_AGE_S):
        logger.warning('TV scan: library path %s: the same folders for %d scans over %.1f h (%s); '
                       'accepting them, the missing shows detach', pid, st['scans'], (now - first) / 3600, why)
        _clear_coverage(pid)
        return True
    try:
        tv_store.set_meta(COVERAGE_META + pid, json.dumps(st))
    except Exception:                        # noqa: BLE001 - only the release clock
        pass
    return False


def _clear_coverage(pid: str) -> None:
    try:
        if tv_store.get_meta(COVERAGE_META + pid) is not None:
            tv_store.set_meta(COVERAGE_META + pid, None)
    except Exception:                        # noqa: BLE001
        pass


def scan_path(rec: Dict[str, Any], now: float, retry_unmatched: bool = True) -> Dict[str, int]:
    from app import file_management as fm
    root_real = os.path.realpath(rec['path'])
    if fm.blocked(root_real) or not os.path.isdir(root_real):
        raise FileNotFoundError(MISSING)
    pid = rec['id']
    folders = show_folders(root_real)
    shows = [f for f in folders if not _system_dir(f)]
    # v170 (Reviewer): a mount the path was on that is gone now is not a skip on its own: with the
    # path's known show folders present the mount record was simply retired (re-recorded below and
    # scanned). v171: see _unavailable for when it is a skip.
    why = _unavailable(pid, root_real, shows, now)
    if why is None or not why.startswith('coverage'):
        _clear_coverage(pid)                 # v173: only consecutive coverage skips count toward release
    if why:
        # M34 (v167): an empty library path that had shows is an unmounted share's mount point (or a
        # share that's still coming up), not a library whose every show was deleted: skip it like a
        # missing path, so every row stays as last seen until its folders are back. v169 (Reviewer):
        # lost+found, @eaDir, #recycle and .Trash-* don't count as shows (v177: nor any SYSTEM_DIRS folder).
        if _empty_logged.get(pid) != f'{root_real}|{why}':
            _empty_logged[pid] = f'{root_real}|{why}'
            gone = why != 'dropped mount' and _dropped_mount(pid, root_real)
            if why == 'empty':
                logger.warning('TV scan: library path %s is empty but had shows; skipped (%s), shows kept',
                               pid, 'its mount is gone' if gone else 'not mounted?')
            else:
                logger.warning('TV scan: library path %s skipped as unavailable (%s%s); shows kept, nothing detached',
                               pid, why, ', its mount is gone' if gone else '')
        _set_skipped(pid, True)
        raise EmptyRoot(EMPTY)
    _empty_logged.pop(pid, None)
    _set_skipped(pid, False)
    if shows:
        try:
            mount = _mount_of(root_real)
            if tv_store.get_meta(MOUNT_META + pid) != mount:
                tv_store.set_meta(MOUNT_META + pid, mount)
            if tv_store.get_meta(ROOT_META + pid) != root_real:
                tv_store.set_meta(ROOT_META + pid, root_real)
            dev = _dev_of(root_real)
            if dev is not None and tv_store.get_meta(DEV_META + pid) != str(dev):
                tv_store.set_meta(DEV_META + pid, str(dev))
        except Exception:                    # noqa: BLE001 - only a signal; never stops a scan
            pass
    _scan_seq[0] += 1
    _path_scans[pid] = (_scan_seq[0], root_real)
    out = {'shows': 0, 'unmatched': 0, 'linked': 0, 'mismatch': 0, 'unrecognized': 0}
    with tv_store.connect() as conn:
        known = {r['folder']: dict(r) for r in conn.execute(
            'SELECT id, folder, tmdb_id, tmdb_refreshed_at, episode_group_id FROM series WHERE path_id = ?'
            ' AND folder IS NOT NULL', (pid,))}       # shows added from Search have no folder to lose
        unmatched = {r['folder'] for r in conn.execute('SELECT folder FROM unmatched WHERE path_id = ?', (pid,))}
    with tv_store.write() as conn:            # folders that are gone: rows only, history kept
        for f in sorted(set(known) - set(folders)):          # v166: sorted, never hash order
            detach(conn, known[f]['id'], now, 'folder gone')
        for f in sorted(unmatched - set(folders)):
            conn.execute('DELETE FROM unmatched WHERE path_id = ? AND folder = ?', (pid, f))
    # Luma's own earlier mapping is never guessed again ("id"); a scheduled
    # rescan leaves review rows alone.
    jobs = []
    for folder in folders:
        row = known.get(folder)
        if row is not None:
            stale = now - (row['tmdb_refreshed_at'] or 0) >= REFRESH_EPISODES_S
            jobs.append(('refresh' if stale else 'known', folder, row))
        elif folder in unmatched and not retry_unmatched:
            jobs.append(('keep', folder, None))
        else:
            jobs.append(('match', folder, None))
    with _progress_lock:
        _progress['total'] += len(jobs)
    results = run_pool(jobs, lambda j: _work(j, root_real))
    error = next((r['error'] for r in results if 'error' in r), None)
    demoted, done = set(), set()
    for (kind, folder, row), res in zip(jobs, results):
        if 'error' in res or res.get('skipped'):
            continue
        done.add(folder)
        if folder in demoted:                 # lost its show to a better folder in this scan
            out['unmatched'] += 1
            continue
        with tv_store.connect() as conn:      # fresh: a duplicate may have moved a show this scan
            r = conn.execute('SELECT id, folder, tmdb_id FROM series WHERE path_id = ? AND folder = ?',
                             (pid, folder)).fetchone()
        cur = dict(r) if r else None
        if kind == 'keep' or (cur is None and kind != 'match'):
            out['unmatched'] += 1
            continue
        if cur is None:
            m = res['m']
            sid, dup = None, False
            if m['status'] == 'matched':
                with tv_store.connect() as conn:
                    owner = conn.execute('SELECT * FROM series WHERE tmdb_id = ?', (m['tmdb_id'],)).fetchone()
                if owner is not None and m.get('tier') == 'auto':
                    # a guess never takes a show from the folder that has it: a duplicate, in review
                    _store_unmatched(pid, folder, {'guess': m['guess'], 'candidates': [_show_brief(owner)]},
                                     now, reason='duplicate')
                    owner = None
                    dup = True
                elif owner is not None:
                    sid = _resolve_duplicate(owner, pid, folder, root_real, now, via=m['via'])
                    if sid is not None and owner['path_id'] == pid:
                        demoted.add(owner['folder'])
                        _move('matched', 'review')
                        if owner['folder'] in done:   # already counted as a show
                            out['shows'] -= 1
                            out['unmatched'] += 1
                elif res.get('data'):
                    sid = store_show(pid, folder, m['tmdb_id'], now, via=m['via'], data=res['data'], tier=m['tier'])
                if sid is None and owner is None and not dup:     # TMDB lost the show between the two reads
                    _store_unmatched(pid, folder, dict(m, candidates=[], tier='none'), now, reason='not_exact')
            else:
                _store_unmatched(pid, folder, m, now)
            if sid is None:
                if m['status'] == 'matched':
                    _move('matched', 'review')
                out['unmatched'] += 1
                continue
        else:
            sid = cur['id']
            if kind == 'refresh' and res.get('data'):
                refresh_episodes(sid, cur['tmdb_id'], now, res['data'])
        out['shows'] += 1
        for k, v in scan_files(sid, pid, folder, root_real, now).items():
            out[k] += v
    if error is not None:
        raise error
    return out


def scan_all(now: Optional[float] = None, retry_unmatched: bool = True) -> Dict[str, Any]:
    """Scan every TV library path. Raises TmdbError when TMDB can't be asked
    (nothing is half-recorded for the folder in hand)."""
    from app import tv_settings
    now = time.time() if now is None else now
    total = {'paths': 0, 'shows': 0, 'unmatched': 0, 'linked': 0, 'mismatch': 0, 'unrecognized': 0, 'missing': []}
    for rec in tv_settings.libraries():
        try:
            counts = scan_path(rec, now, retry_unmatched)
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            total['missing'].append(rec['id'])
            continue
        total['paths'] += 1
        for k, v in counts.items():
            total[k] += v
    total['refreshed'] = refresh_added(now)
    if total['paths']:
        tv_store.set_meta(LAST_SCAN_KEY, repr(now))
    # else every path was missing or unreadable (an unmounted share): no
    # last-scan time, so the next scheduler tick tries again.
    return total


def refresh_added(now: Optional[float] = None, folders: bool = False, first: Optional[set] = None,
                  background: bool = False, repair: Optional[set] = None, only: Optional[set] = None,
                  stale_s: Optional[float] = None) -> int:
    """Shows added from Search (a library, no folder) keep their episodes
    current like folder shows: once REFRESH_EPISODES_S has passed, on the next
    scan (Import) or Calendar open (folders=True: every show of the selected
    libraries). TMDB reads on the workers through the shared rate budget; a
    TMDB error leaves the rest for next time. *background* (the Calendar-open
    refresh): paced at tv_tmdb.BACKGROUND_RATE_PER_S. *repair*: shows read again
    whatever their age. *only*: just these shows (the visible-week
    pass). Returns how many were refreshed."""
    from app import tv_settings
    now = time.time() if now is None else now
    pids = [p['id'] for p in tv_settings.libraries()]
    if not pids:
        return 0
    with tv_store.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            'SELECT id, tmdb_id, episode_group_id, tmdb_refreshed_at, stills_at FROM series WHERE'
            + ('' if folders else ' folder IS NULL AND') + ' tmdb_id IS NOT NULL AND path_id IN'
            f' ({",".join("?" * len(pids))})', pids)
            # stale (24h), or asked for by *repair*. v178 (Taylor): the calendar cards are text only, so a
            # Calendar open no longer reads shows again for episode stills (never read or still missing)
            if now - (r['tmdb_refreshed_at'] or 0) >= (REFRESH_EPISODES_S if stale_s is None else stale_s)
            or r['id'] in (repair or ())]
    if only is not None:
        rows = [r for r in rows if r['id'] in only]
    first = first or set()
    rows.sort(key=lambda r: r['id'] not in first)          # the shows in the visible week first

    def work(r):
        try:
            with tv_tmdb.fresh(), (tv_tmdb.background() if background else nullcontext()):
                return {'data': fetch_show(r['tmdb_id'], None, None, r['episode_group_id'])}
        except tv_tmdb.TmdbError as e:
            return {'error': e}
    n = 0
    results = run_pool(rows, work)
    for r, res in zip(rows, results):
        if res.get('data'):
            refresh_episodes(r['id'], r['tmdb_id'], now, res['data'])
            n += 1
    if folders and rows:
        ids = [r['id'] for r in rows]
        with tv_store.connect() as conn:
            q = f'SELECT COUNT(*) AS n FROM episodes WHERE series_id IN ({",".join("?" * len(ids))})'
            stills = conn.execute(q + ' AND still_path IS NOT NULL', ids).fetchone()['n']
            tvdb = conn.execute(q + ' AND tvdb_image IS NOT NULL', ids).fetchone()['n']
        logger.info('TV episode refresh%s: %d of %d shows read (%d for missing images, %d never read),'
                    ' %d with episode stills, %d with TVDB images; %s',
                    ' (this week)' if only is not None else '', n, len(rows),
                    sum(1 for r in rows if r['id'] in (repair or ())), sum(1 for r in rows if r['stills_at'] is None),
                    stills, tvdb, 'stopped: ' + type(next(x['error'] for x in results if 'error' in x)).__name__
                    if any('error' in x for x in results) else 'done')
    return n


_refresh_lock = threading.Lock()
_priority_lock = threading.Lock()
_RefreshThread = threading.Thread    # the Calendar-open refresh (tests run it inline)


def _refresh_pass(only: Optional[set], first: set, repair: set) -> None:
    try:
        refresh_added(folders=True, first=first, background=True, repair=repair, only=only)
    except Exception:                  # noqa: BLE001 - a failed refresh waits for the next open
        logger.exception('TV calendar refresh failed')
    try:
        from app import tv_airtimes      # air times + TVDB images (TVDB, else TVmaze), paced, after TMDB
        tv_airtimes.refresh(first=first, repair=repair, only=only)
    except Exception:                  # noqa: BLE001
        logger.exception('TV air times refresh failed')
    from app import tv_banners            # shows scanned in without a banner: the backfill runs soon
    tv_banners.poke()


def refresh_on_open(first: Optional[set] = None, start: Optional[dt.date] = None, end: Optional[dt.date] = None) -> bool:
    """Calendar open: in the background, never during an Import (which refreshes
    them itself). First the visible range's shows (*first*: stale ones; v178: never
    for episode images, the cards are text only), then the rest of the library. When a refresh is already running, the visible range still gets its
    own pass at once. The open answers from tv.db right away; the page asks
    again when 'refreshing'. False when nothing started."""
    if _scan_lock.locked():
        return False
    first = set(first or ())
    repair: set = set()                # v178: no still repair reads (text-only calendar cards)
    if _refresh_lock.acquire(blocking=False):
        def work():
            try:
                try:
                    from app import tv_plex, tv_watch  # files that landed since the last poll, and Plex, first
                    tv_watch.poll_soon()
                    tv_plex.rebuild()
                except Exception:                  # noqa: BLE001
                    logger.exception('TV link on open failed')
                if first:
                    _refresh_pass(first, first, repair)       # this week first, before the rest
                _refresh_pass(None, first, repair - first)    # the rest (this week's are fresh now)
            finally:
                _refresh_lock.release()
        lock = _refresh_lock
    elif first and _priority_lock.acquire(blocking=False):
        def work():
            try:
                _refresh_pass(first, first, repair)           # the full pass is busy elsewhere: this week now
            finally:
                _priority_lock.release()
        lock = _priority_lock
    else:
        return False
    try:
        _RefreshThread(target=work, daemon=True, name='tv-calendar-refresh').start()
    except Exception:
        lock.release()
        raise
    return True


def last_scan() -> Optional[float]:
    try:
        v = tv_store.get_meta(LAST_SCAN_KEY)
        return float(v) if v else None
    except (ValueError, TypeError):
        return None


def run(retry_unmatched: bool = True, now: Optional[float] = None) -> bool:
    """One scan, unless one is running (False then). Records the outcome for
    the Settings view."""
    if not _scan_lock.acquire(blocking=False):
        return False
    _state.update(running=True, error=None)
    with _progress_lock:
        _progress.update(total=0, done=0, matched=0, review=0, none=0, ready=False)
    tv_tmdb.prune_cache()
    started = time.time() if now is None else now
    try:
        _state['summary'] = scan_all(now, retry_unmatched)
        _backoff.update(fails=0, until=0.0)
    except tv_tmdb.TmdbError as e:
        _state['error'] = str(e)
        _backoff['fails'] += 1
        _backoff['until'] = started + backoff_s(int(_backoff['fails']))
        logger.warning('TV scan stopped: %s (next scheduled try in %d min)', e,
                       backoff_s(int(_backoff['fails'])) // 60)
    except Exception as e:
        _state['error'] = 'Scan failed'
        logger.error('TV scan failed: %s', type(e).__name__, exc_info=True)
    finally:
        _state['running'] = False
        with _progress_lock:
            _progress['ready'] = True
        _scan_lock.release()
    return True


def start(retry_unmatched: bool = True) -> bool:
    """Scan in the background; False when one is already running."""
    if _scan_lock.locked():
        return False
    threading.Thread(target=run, kwargs={'retry_unmatched': retry_unmatched}, daemon=True,
                     name='tv-scan').start()
    return True


def status() -> Dict[str, Any]:
    return {'running': bool(_state['running'] or _scan_lock.locked()), 'error': _state['error'],
            'last_scan': last_scan(), 'summary': _state['summary']}


def progress() -> Dict[str, Any]:
    """The small Import status the UI polls: {running, done, total, matched,
    review, none, error, last_scan} (counts of folders decided so far)."""
    with _progress_lock:
        p = dict(_progress)
    p.pop('ready', None)
    return {'running': bool(_state['running'] or _scan_lock.locked()), 'error': _state['error'],
            'last_scan': last_scan(), **p}


# --------------------------------------------------------------------------- scheduled rescan

TICK_S = 300                    # how often the scheduler looks; scans run every rescan_interval_h
_sched_stop = threading.Event()
_sched_thread: Optional[threading.Thread] = None


def backoff_s(fails: int) -> int:
    """Wait after *fails* TMDB failures in a row: 2 ticks, then doubling, at
    most the rescan interval."""
    from app import tv_settings
    cap = int(tv_settings.load()['rescan_interval_h'] * 3600)
    return min(TICK_S * 2 ** max(fails, 1), cap)


def due(now: float) -> bool:
    """A scheduled scan is due: TV libraries are selected and the last scan is older
    than rescan_interval_h (or there never was one)."""
    from app import tv_settings
    st = tv_settings.load()
    if not tv_settings.libraries():
        return False
    last = last_scan()
    return last is None or now - last >= st['rescan_interval_h'] * 3600


def tick(now: Optional[float] = None) -> bool:
    """One scheduler step: scan when due. Scheduled scans keep unmatched
    folders as they are (a manual Scan asks TMDB about them again)."""
    now = time.time() if now is None else now
    if now < _backoff['until']:
        return False                  # TMDB failed recently: wait (backoff_s)
    try:
        if not due(now):
            return False
    except Exception as e:
        logger.warning('TV rescan check failed: %s', type(e).__name__)
        return False
    return run(retry_unmatched=False, now=now)


def _loop() -> None:
    while not _sched_stop.wait(TICK_S):
        tick()


def start_scheduler() -> bool:
    """Start the rescan thread once (main.start_services, minimal builds).
    Idle until TV libraries are selected."""
    global _sched_thread
    if _sched_thread is not None and _sched_thread.is_alive():
        return False
    _sched_stop.clear()
    _sched_thread = threading.Thread(target=_loop, daemon=True, name='tv-rescan')
    _sched_thread.start()
    try:
        from app import tv_banners        # series banners backfill (paced, background)
        tv_banners.start()
    except Exception as e:               # noqa: BLE001
        logger.error('Failed to start the series banner backfill: %s', type(e).__name__)
    return True


def stop_scheduler() -> None:
    _sched_stop.set()


# --------------------------------------------------------------------------- fix-match

NOT_FOUND = 'Not found'
SHOW_NOT_FOUND = 'Show not found on TMDB'
TAKEN = 'That show is already matched to another folder'
BUSY = 'A scan is already running'
LOCK_WAIT_S = 30


def _root_for(path_id: str) -> Optional[str]:
    from app import file_management as fm, tv_settings
    for rec in tv_settings.libraries():
        if rec['id'] == path_id:
            root = os.path.realpath(rec['path'])
            return root if not fm.blocked(root) and os.path.isdir(root) else None
    return None


def _apply(path_id: str, folder: str, tmdb_id: int, now: float, *, via: str, event: str,
           series_id: Optional[int], data: Optional[Dict[str, Any]] = None,
           found: Optional[List[Tuple[str, os.stat_result]]] = None) -> Dict[str, Any]:
    with tv_store.connect() as conn:
        other = conn.execute('SELECT id FROM series WHERE tmdb_id = ?', (tmdb_id,)).fetchone()
    if other and other['id'] != series_id:
        # The show is in another folder (a duplicate): the owner's pick moves
        # it here and the other folder becomes the unmatched duplicate.
        sid = take_over(other['id'], path_id, folder, now, via=via, event=event, drop_series_id=series_id)
        root = _root_for(path_id)
        if root is not None and folder:
            scan_files(sid, path_id, folder, root, now, found)
        return {'ok': True, 'series_id': sid}
    if data is None:                           # one details call, the group picked from the folder's files
        root = _root_for(path_id)
        data = fetch_show(tmdb_id, None, _hints(root, folder) if root and folder else None)
    if not data:
        return {'ok': False, 'error': SHOW_NOT_FOUND, 'status': 404}
    sid = store_show(path_id, folder, tmdb_id, now, via=via, event=event, series_id=series_id, data=data)
    if sid is None:
        return {'ok': False, 'error': TAKEN, 'status': 409}
    root = _root_for(path_id)
    if root is not None and folder:
        scan_files(sid, path_id, folder, root, now, found)
    return {'ok': True, 'series_id': sid}


def _locked(fn):
    def wrapped(*a, **k):
        if not _scan_lock.acquire(timeout=LOCK_WAIT_S):
            return {'ok': False, 'error': BUSY, 'status': 409}
        try:
            return fn(*a, **k)
        finally:
            _scan_lock.release()
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


@_locked
def rematch(series_id: int, tmdb_id: int, now: Optional[float] = None) -> Dict[str, Any]:
    """"Wrong show?": point a matched folder at another show (history: rematched)."""
    now = time.time() if now is None else now
    with tv_store.connect() as conn:
        row = conn.execute('SELECT path_id, folder, tmdb_id FROM series WHERE id = ? AND path_id IS NOT NULL',
                           (series_id,)).fetchone()
    if not row:
        return {'ok': False, 'error': NOT_FOUND, 'status': 404}
    return _apply(row['path_id'], row['folder'], tmdb_id, now, via='manual', event='rematched',
                  series_id=series_id)


@_locked
def match_unmatched(unmatched_id: int, tmdb_id: int, now: Optional[float] = None) -> Dict[str, Any]:
    """"Match": give an unmatched folder its show (history: matched, via manual)."""
    now = time.time() if now is None else now
    with tv_store.connect() as conn:
        row = conn.execute('SELECT path_id, folder FROM unmatched WHERE id = ?', (unmatched_id,)).fetchone()
    if not row:
        return {'ok': False, 'error': NOT_FOUND, 'status': 404}
    return _apply(row['path_id'], row['folder'], tmdb_id, now, via='manual', event='matched', series_id=None)


IMPORT_ALL_MAX = 1000


@_locked
def import_all(items: List[Tuple[int, int]], now: Optional[float] = None) -> Dict[str, Any]:
    """Import all: give review rows (unmatched id, tmdb id) their shows, as
    the owner's picks (via manual). Shows are fetched on the workers, then
    stored one by one. A show picked twice goes to the first row only.
    {'ok': True, 'imported': [ids], 'failed': [{id, error}]}."""
    return _import_rows(items, now, via='manual')


def _owner_elsewhere(row: Dict[str, Any], tid: int):
    with tv_store.connect() as conn:
        return conn.execute('SELECT * FROM series WHERE tmdb_id = ? AND path_id IS NOT NULL AND folder IS NOT NULL'
                            ' AND NOT (path_id = ? AND folder = ?)', (tid, row['path_id'], row['folder'])).fetchone()


def _import_rows(items: List[Tuple[int, int]], now: Optional[float], via: str, lock: bool = False) -> Dict[str, Any]:
    """Reads, the folder walks and the TMDB fetches run first; then the rows are stored one by one.
    With *lock* (the boot re-run, which isn't @_locked) _scan_lock is taken only around those writes,
    never across the walk or the fetch (Reviewer on v158a)."""
    now = time.time() if now is None else now
    with tv_store.connect() as conn:
        rows = {r['id']: dict(r) for r in conn.execute('SELECT id, path_id, folder FROM unmatched')}
    jobs, owned, failed, seen = [], [], [], set()
    for uid, tid in items:
        row = rows.get(uid)
        if row is None:
            failed.append({'id': uid, 'error': NOT_FOUND})
        elif tid in seen:
            failed.append({'id': uid, 'error': TAKEN})
        elif _owner_elsewhere(row, tid) is not None:
            owned.append((row, tid))                             # another folder has it: a duplicate, unchecked
        else:
            seen.add(tid)
            jobs.append((row, tid))

    def fetch(job):
        row, tid = job
        root = _root_for(row['path_id'])
        try:
            return {'data': fetch_show(tid, None, _hints(root, row['folder']) if root else None),
                    'found': list_files(row['folder'], root) if root and row['folder'] else None}
        except tv_tmdb.TmdbError as e:
            return {'error': e}
    fetched = list(zip(jobs, run_pool(jobs, fetch)))
    if lock and not _scan_lock.acquire(timeout=LOCK_WAIT_S):
        return {'ok': False, 'error': BUSY, 'status': 409, 'imported': [], 'failed': failed}
    try:
        imported = []
        for row, tid in owned:
            if _owned_elsewhere(row, tid, now):                  # re-checked under the lock, then stored
                failed.append({'id': row['id'], 'error': TAKEN})
        if lock:                                                 # a scan may have run while we fetched
            with tv_store.connect() as conn:
                live = {(r['id'], r['path_id'], r['folder']) for r in conn.execute('SELECT id, path_id, folder FROM unmatched')}
        for (row, tid), res in fetched:
            if lock and (row['id'], row['path_id'], row['folder']) not in live:
                continue                                         # gone or changed: nothing to import
            if 'error' in res or res.get('skipped'):
                failed.append({'id': row['id'], 'error': str(res.get('error') or tv_tmdb.BUSY)})
                continue
            if not res.get('data'):
                failed.append({'id': row['id'], 'error': SHOW_NOT_FOUND})
                continue
            out = _apply(row['path_id'], row['folder'], tid, now, via=via, event='matched', series_id=None,
                         data=res['data'], found=res.get('found'))
            if out['ok']:
                imported.append(row['id'])
            else:
                failed.append({'id': row['id'], 'error': out['error']})
    finally:
        if lock:
            _scan_lock.release()
    return {'ok': True, 'imported': imported, 'failed': failed}


# The boot re-run (v157 rule, widened in v161a). Every review row that isn't a duplicate is
# unresolved; whatever schema or rule wrote it, it is eligible:
#   * a row carrying Luma's ranked pick (tier 'likely', reason 'not_exact') imports that pick;
#   * every other row is matched again with today's matcher (tier 'none' from schema v3's default
#     or the old rule, pick NULL, reasons 'not_exact', 'unclear', 'no_results', 'no_title', or any
#     value an older build wrote). A match imports it as a scan would; otherwise the row is
#     rewritten with today's answer (review).
# Never touched: 'duplicate' rows (another folder has the show; the owner's own moves leave
# these) and folders that are gone. Shows already imported aren't review rows, so never touched.
# The marker (meta RERUN_META) records the rule and when it first ran here; rows written before
# that are re-matched once, rows written after it were written by this rule. A run cut short
# (TMDB down) leaves the unprocessed rows for the next boot.
# v166: the rule is tv_match.RULE_VERSION plus a fingerprint of the matcher (tv_match's source and
# the auto-pick reasons here), so a matcher change re-runs review rows on the next boot even if
# nobody bumped the name. :5002 kept 'v161a' after the v165 similar-name rule, so X-Men never re-ran.
RERUN_META = 'tv_import_rerun'
KEEP_REASONS = ('duplicate',)


def _rule_id() -> str:
    import hashlib
    import inspect
    try:                                         # v169 (Reviewer): import time, so never raise
        src = inspect.getsource(tv_match)
        h = hashlib.sha256(src.encode('utf-8'))
        h.update(repr(sorted(AUTO_REASONS)).encode('utf-8'))
        return f'{tv_match.RULE_VERSION}+{h.hexdigest()[:10]}'
    except Exception:                            # noqa: BLE001 - no source shipped, odd loader: the name alone
        return tv_match.RULE_VERSION


RERUN_RULE = _rule_id()


def _rerun_since(now: float) -> float:
    try:
        cur = json.loads(tv_store.get_meta(RERUN_META) or '{}')
    except ValueError:
        cur = {}
    if isinstance(cur, dict) and cur.get('rule') == RERUN_RULE and isinstance(cur.get('since'), (int, float)):
        return float(cur['since'])
    tv_store.set_meta(RERUN_META, json.dumps({'rule': RERUN_RULE, 'since': now}))
    return now


def _rematch_rows(rows: List[Dict[str, Any]], now: float) -> Dict[str, Any]:
    """Match old review rows again (TMDB reads and folder walks unlocked), then store each under the
    scan lock: an import as a scan would, else the row rewritten with today's answer."""
    jobs, gone = [], []
    for r in rows:
        root = _root_for(r['path_id'])
        if not root or not r['folder'] or not os.path.isdir(os.path.join(root, r['folder'])):
            gone.append(r['id'])
            continue
        jobs.append((r, root))

    def work(job):
        r, root = job
        try:
            hints = _hints(root, r['folder'])
            m = auto_pick(tv_match.match(r['folder'], hints))
            data = fetch_show(m['tmdb_id'], m.get('details'), hints) if m['status'] == 'matched' else None
            return {'m': m, 'data': data, 'found': list_files(r['folder'], root) if m['status'] == 'matched' else None}
        except tv_tmdb.TmdbError as e:
            return {'error': e}
    results = list(zip(jobs, run_pool(jobs, work)))
    out = {'imported': [], 'kept': [], 'duplicates': [], 'failed': [], 'gone': gone, 'error': None}
    if not _scan_lock.acquire(timeout=LOCK_WAIT_S):
        out['error'] = BUSY
        return out
    try:
        with tv_store.connect() as conn:
            live = {r['id']: dict(r) for r in conn.execute('SELECT id, path_id, folder, ts FROM unmatched')}
        for (r, _root), res in results:
            cur = live.get(r['id'])
            if cur is None or (cur['path_id'], cur['folder'], cur['ts']) != (r['path_id'], r['folder'], r['ts']):
                continue                                 # a scan or the owner changed it meanwhile
            if 'error' in res or res.get('skipped'):
                out['failed'].append(r['id'])
                out['error'] = out['error'] or str(res.get('error') or tv_tmdb.BUSY)
                continue
            m = res['m']
            if m['status'] == 'matched' and res.get('data'):
                if _owned_elsewhere(r, m['tmdb_id'], now):
                    out['duplicates'].append(r['id'])
                    continue
                o = _apply(r['path_id'], r['folder'], m['tmdb_id'], now, via='auto', event='matched', series_id=None,
                           data=res['data'], found=res.get('found'))
                if o['ok']:
                    out['imported'].append(r['id'])
                    continue
                out['failed'].append(r['id'])
                continue
            _store_unmatched(r['path_id'], r['folder'], m if m['status'] == 'unmatched' else
                             {**m, 'status': 'unmatched', 'reason': m.get('auto_reason') or 'not_exact'}, now)
            out['kept'].append(r['id'])
    finally:
        _scan_lock.release()
    return out


def auto_import_pending(now: Optional[float] = None) -> Dict[str, Any]:
    """Boot: import every unresolved review row (see the rule above). tv.db only, plus TMDB reads.
    Always logs one summary line. {'ok', 'eligible', 'imported', 'failed', 'kept', 'duplicates', ...}."""
    now = time.time() if now is None else now
    out: Dict[str, Any] = {'ok': True, 'eligible': 0, 'direct': 0, 'rematched': 0, 'imported': [], 'failed': [],
                           'kept': 0, 'duplicates': [], 'gone': 0, 'skipped': 0, 'total': 0, 'note': None,
                           'error': None}

    def summary(o):
        logger.info('TV import: boot re-run (rule %s): %d review row(s), %d eligible (%d with a pick, %d matched again), '
                    '%d imported, %d kept in review, %d now duplicates, %d failed, %d folders gone, %d skipped (duplicates)%s',
                    RERUN_RULE, o['total'], o['eligible'], o['direct'], o['rematched'], len(o['imported']), o['kept'],
                    len(o['duplicates']), len(o['failed']), o['gone'], o['skipped'], f"; {o['note']}" if o['note'] else '')
        return o
    if not AUTO_IMPORT:
        out['note'] = 'auto import off'
        return summary(out)
    with tv_store.connect() as conn:
        rows = [dict(r) for r in conn.execute('SELECT id, path_id, folder, reason, tier, pick, ts FROM unmatched ORDER BY id')]
    out['total'] = len(rows)
    since = _rerun_since(now) if rows else now
    open_rows = [r for r in rows if r['reason'] not in KEEP_REASONS and r['tier'] != 'duplicate']
    out['skipped'] = len(rows) - len(open_rows)
    direct, bad = [], []
    for r in open_rows:
        if r['pick'] and r['tier'] == 'likely' and r['reason'] in AUTO_REASONS:
            try:
                r['pick'] = int(r['pick'])
                direct.append(r)
            except (TypeError, ValueError):
                bad.append(r)                    # one bad row never stops the run (Reviewer on 49cb7f2)
    if bad:
        logger.warning('TV import: boot re-run skipped %d review row(s) with an unreadable pick (ids %s)',
                       len(bad), ', '.join(str(r['id']) for r in bad[:20]))
    bad_ids = {r['id'] for r in bad}
    open_rows = [r for r in open_rows if r['id'] not in bad_ids]
    ids = {r['id'] for r in direct}
    again = [r for r in open_rows if r['id'] not in ids and (r['ts'] is None or r['ts'] < since)]
    out.update(direct=len(direct), rematched=len(again), eligible=len(direct) + len(again))
    with tv_tmdb.background():
        if direct:
            d = _import_rows([(r['id'], int(r['pick'])) for r in direct], now, via='auto', lock=True)
            if not d['ok']:
                out.update(ok=False, error=d['error'], note=d['error'], kept=len(open_rows))
                return summary(out)
            out['imported'] += d['imported']
            out['duplicates'] += [f['id'] for f in d['failed'] if f['error'] == TAKEN]
            out['failed'] += [f for f in d['failed'] if f['error'] != TAKEN]
        if again:
            g = _rematch_rows(again, now)
            out['imported'] += g['imported']
            out['duplicates'] += g['duplicates']
            out['failed'] += [{'id': i, 'error': g['error']} for i in g['failed']]
            out['gone'] = len(g['gone'])
            if g['error']:
                out.update(note=g['error'], error=g['error'], ok=g['error'] != BUSY)
    # what is still in review: every open row not imported and not turned into a duplicate
    out['kept'] = len(open_rows) - len(out['imported']) - len(out['duplicates'])
    return summary(out)


def auto_import_on_start() -> bool:
    """A background thread at boot (after the migration); never fails boot. Logs why when it doesn't run."""
    import os as _os
    if _os.environ.get('LUMA_SKIP_BACKGROUND_THREADS'):
        logger.info('TV import: boot re-run off (LUMA_SKIP_BACKGROUND_THREADS)')
        return False

    def run():
        try:
            auto_import_pending()
        except Exception:                    # noqa: BLE001
            logger.exception('TV import: boot re-run of pending rows failed')
    _Thread(target=run, daemon=True, name='tv-auto-import').start()
    return True


def _owned_elsewhere(row: Dict[str, Any], tid: int, now: float) -> bool:
    """Import all never moves a show another folder already has (The Office (US),
    SmackDown): the row becomes a duplicate in review, its candidate that show."""
    owner = _owner_elsewhere(row, tid)
    if owner is None:
        return False
    g = tv_match.parse_folder(row['folder'])
    _store_unmatched(row['path_id'], row['folder'], {'guess': {'title': g['title'], 'year': g['year']},
                                                     'candidates': [_show_brief(owner)]}, now, reason='duplicate')
    return True


def lookup(q: str) -> Optional[List[Dict[str, Any]]]:
    """Picker search by id: 'tmdb:1396', 'tvdb:81189', 'imdb:tt0903747'.
    None when *q* isn't an id query (search by name then)."""
    m = re.fullmatch(r'\s*(tmdb|tvdb|imdb)\s*:\s*(\S+)\s*', q, re.I)
    if not m:
        return None
    src, val = m.group(1).lower(), m.group(2)
    if src == 'imdb':
        return tv_tmdb.find(val.lower(), 'imdb_id') if re.fullmatch(r'tt\d{1,10}', val, re.I) else []
    if not val.isdigit() or not 0 < int(val) < 10 ** 9:
        return []
    if src == 'tvdb':
        return tv_tmdb.find(str(int(val)), 'tvdb_id')
    d = tv_tmdb.show(int(val))
    b = tv_tmdb.brief(d) if d else None
    return [b] if b else []


# --------------------------------------------------------------------------- view

REVIEW_MAX = 20
_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def _ep_code(season: int, episodes: List[int]) -> str:
    code = f'S{season:02d}E{episodes[0]:02d}'
    return code + (f'\u2013E{episodes[-1]:02d}' if len(episodes) > 1 else '')


def _day(air_date: str) -> str:
    try:
        y, m, d = (int(x) for x in air_date.split('-'))
        return f'{_MONTHS[m - 1]} {d}, {y}'
    except (ValueError, IndexError):
        return air_date


def review_lines(conn, sid: int) -> List[str]:
    """What needs review in a show, in words: one line per file whose numbering
    doesn't fit ("S02E40 isn't an episode of this show"), then one line for
    files with no episode in the name."""
    lines: List[Tuple[Tuple, str]] = []
    unrecognized = 0
    for r in conn.execute("SELECT rel_path, state, parsed FROM episode_files WHERE series_id = ?"
                          " AND state IN ('mismatch', 'unrecognized') ORDER BY rel_path", (sid,)):
        if r['state'] == 'unrecognized':
            unrecognized += 1
            continue
        try:
            p = json.loads(r['parsed'] or '{}')
        except ValueError:
            p = {}
        season, eps = p.get('season'), [e for e in p.get('episodes') or [] if isinstance(e, int)]
        if isinstance(season, int) and eps:
            code = _ep_code(season, eps)            # the file's own S/E decides, never its folder
            text = f"{code} isn't an episode of this show"
            lines.append(((0, season, eps[0], text), text))
        elif isinstance(p.get('air_date'), str):
            text = f"{_day(p['air_date'])} isn't an episode of this show"
            lines.append(((1, 0, 0, p['air_date']), text))
        else:
            unrecognized += 1
    out = [t for _, t in sorted(lines)]
    if len(out) > REVIEW_MAX:
        out = out[:REVIEW_MAX - 1] + [f'{len(out) - REVIEW_MAX + 1} more files don\u2019t fit an episode']
    if unrecognized:
        out.append(f"{unrecognized} {'file' if unrecognized == 1 else 'files'} not recognized")
    return out


CONTINUING = ('Returning Series', 'In Production', 'Planned', 'Pilot')


_SORT_DROP = re.compile(r"[.'\u2019\u2018`]")          # S.W.A.T. -> SWAT, Grey's -> Greys
_SORT_SPACE = re.compile(r'[^0-9a-z]+')
_SORT_ARTICLE = re.compile(r'^(?:the|a|an) (?=\S)')


def sort_title(title: Optional[str]) -> str:
    """The A-Z key for show names (Series grid, Calendar): accents folded,
    periods and apostrophes dropped, other symbols as spaces, a leading
    The/A/An ignored. The frontend's sortKey mirrors it."""
    t = unicodedata.normalize('NFKD', title or '')
    t = ''.join(c for c in t if not unicodedata.combining(c)).casefold()
    t = _SORT_SPACE.sub(' ', _SORT_DROP.sub('', t)).strip()
    return _SORT_ARTICLE.sub('', t) or t


def library_view() -> Dict[str, Any]:
    from app import tv_settings
    # Only the selected libraries' shows; a deselected or removed one's rows
    # stay in tv.db and show again when it is selected again.
    names = {p['id']: p['name'] for p in tv_settings.libraries()}
    with tv_store.connect() as conn:
        shows = []
        for r in conn.execute(
                'SELECT s.id, s.tmdb_id, s.title, s.year, s.path_id, s.folder, s.poster_path, s.monitored, s.status,'
                ' s.added_at, s.monitor, s.monitor_new_seasons, s.profile_id, s.series_type, s.season_folder,'
                # Series grid: aired episodes (not specials) and those with a linked file
                ' (SELECT COUNT(*) FROM episodes e WHERE e.series_id = s.id AND e.season_number > 0'
                '  AND e.air_date IS NOT NULL AND e.air_date <= :today) AS aired,'
                ' (SELECT COUNT(DISTINCT l.episode_id) FROM episode_file_links l JOIN episode_files lf ON lf.id = l.file_id'
                "  JOIN episodes e ON e.id = l.episode_id WHERE lf.series_id = s.id AND lf.state = 'linked'"
                '  AND e.season_number > 0 AND e.air_date IS NOT NULL AND e.air_date <= :today) AS have,'
                " SUM(f.state = 'linked') AS linked, SUM(f.state = 'mismatch') AS mismatch,"
                " SUM(f.state = 'unrecognized') AS unrecognized, COUNT(f.id) AS files,"
                ' (SELECT COUNT(DISTINCT l.episode_id) FROM episode_file_links l'
                '  JOIN episode_files lf ON lf.id = l.file_id WHERE lf.series_id = s.id) AS episodes'
                ' FROM series s LEFT JOIN episode_files f ON f.series_id = s.id'
                ' WHERE s.path_id IS NOT NULL GROUP BY s.id ORDER BY s.title COLLATE NOCASE',
                {'today': dt.date.today().isoformat()}):
            if r['path_id'] not in names:
                continue
            shows.append({'id': r['id'], 'tmdb_id': r['tmdb_id'], 'title': r['title'], 'year': r['year'],
                          'path_id': r['path_id'], 'path_name': names.get(r['path_id'], ''), 'folder': r['folder'],
                          'poster_path': r['poster_path'], 'monitored': bool(r['monitored']),
                          'files': r['files'] or 0, 'episodes': r['episodes'] or 0,
                          'linked': r['linked'] or 0, 'mismatch': r['mismatch'] or 0,
                          'unrecognized': r['unrecognized'] or 0,
                          'aired': r['aired'] or 0, 'have': r['have'] or 0, 'status': r['status'],
                          'continuing': (r['status'] or '') in CONTINUING, 'added_at': r['added_at'],
                          # v181: the Series list view (bulk editor) columns
                          'monitor': r['monitor'], 'monitor_new_seasons': 'all' if r['monitor_new_seasons'] else 'none',
                          'profile_id': r['profile_id'], 'series_type': r['series_type'],
                          'season_folder': bool(r['season_folder']),
                          'review': review_lines(conn, r['id']) if (r['mismatch'] or r['unrecognized']) else []})
        unmatched = []
        owned = {r['tmdb_id'] for r in conn.execute('SELECT tmdb_id FROM series WHERE tmdb_id IS NOT NULL')}
        for r in conn.execute('SELECT * FROM unmatched ORDER BY folder COLLATE NOCASE'):
            if r['path_id'] not in names:
                continue
            try:
                cands = json.loads(r['candidates'] or '[]')
            except ValueError:
                cands = []
            tier = 'duplicate' if r['reason'] == 'duplicate' else (r['tier'] or 'none')
            pick = r['pick'] if tier == 'likely' else (cands[0]['tmdb_id'] if tier == 'duplicate' and cands else None)
            if tier == 'likely':
                pick, cands = _skip_owned(pick, cands, owned)
            # 'pick' is what Import all would import as it stands (pre-checked); a guess nothing backs
            # (reason 'unclear') is only 'guess_pick', shown first in an unchecked row
            guess = pick if pick is not None else (cands[0]['tmdb_id'] if tier == 'likely' and cands else None)
            unmatched.append({'id': r['id'], 'path_id': r['path_id'], 'path_name': names.get(r['path_id'], ''),
                              'folder': r['folder'], 'guess_title': r['guess_title'], 'guess_year': r['guess_year'],
                              'candidates': cands[:tv_match.TOP_N], 'reason': r['reason'], 'tier': tier,
                              'pick': pick, 'guess_pick': guess})
    shows.sort(key=lambda x: (sort_title(x['title']), x['year'] or 0, x['id']))
    # Review order: no match, then duplicates, then likely (best guess picked)
    unmatched.sort(key=lambda u: ({'none': 0, 'duplicate': 1, 'likely': 2}.get(u['tier'], 0), u['folder'].lower()))
    return {'shows': shows, 'unmatched': unmatched, 'scan': status()}
