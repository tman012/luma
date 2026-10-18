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
  empties the list.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
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
            if e.name.startswith('.'):
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
               tier: Optional[str] = None) -> Optional[int]:
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
        cols = dict(fields, tmdb_id=tmdb_id, aliases=json.dumps(aliases), tmdb_refreshed_at=now, match_tier=tier or via)
        if series_id is None:
            cols.update(path_id=path_id, folder=folder, monitored=0, monitor_new_seasons=0, added_at=now)
            keys = ', '.join(cols)
            cur = conn.execute(f'INSERT INTO series ({keys}) VALUES ({", ".join("?" * len(cols))})', tuple(cols.values()))
            sid = int(cur.lastrowid)
        else:
            old = conn.execute('SELECT tmdb_id, title FROM series WHERE id = ?', (series_id,)).fetchone()
            conn.execute('DELETE FROM episodes WHERE series_id = ?', (series_id,))
            conn.execute('DELETE FROM seasons WHERE series_id = ?', (series_id,))
            conn.execute(f'UPDATE series SET {", ".join(f"{k} = ?" for k in cols)} WHERE id = ?',
                         (*cols.values(), series_id))
            sid = series_id
        _store_episodes(conn, sid, seasons)
        _store_group(conn, sid, data.get('group'))
        detail = {'via': via, 'folder': folder, 'path_id': path_id, 'tmdb_id': tmdb_id, 'title': fields['title']}
        if event == 'rematched' and old:
            detail.update(from_tmdb_id=old['tmdb_id'], from_title=old['title'])
        conn.execute('INSERT INTO history (ts, series_id, episode_ids, event, detail) VALUES (?, ?, ?, ?, ?)',
                     (now, sid, '[]', event, json.dumps(detail)))
        conn.execute('DELETE FROM unmatched WHERE path_id = ? AND folder = ?', (path_id, folder))
    return sid


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
                ' air_date, runtime, monitored) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)'
                ' ON CONFLICT(series_id, season_number, episode_number) DO UPDATE SET'
                ' tmdb_episode_id = excluded.tmdb_episode_id, title = excluded.title, overview = excluded.overview,'
                ' air_date = excluded.air_date, runtime = excluded.runtime',
                (sid, n, num, ep.get('id') if isinstance(ep.get('id'), int) else None,
                 str(ep.get('name') or '')[:300] or None, str(ep.get('overview') or '')[:2000] or None,
                 ep.get('air_date') if isinstance(ep.get('air_date'), str) else None,
                 ep.get('runtime') if isinstance(ep.get('runtime'), int) else None))


def refresh_episodes(sid: int, tmdb_id: int, now: float, data: Optional[Dict[str, Any]] = None) -> None:
    if data is None:
        with tv_store.connect() as conn:
            row = conn.execute('SELECT episode_group_id FROM series WHERE id = ?', (sid,)).fetchone()
        data = fetch_show(tmdb_id, group_id=row['episode_group_id'] if row else None)
    if not data:
        return
    with tv_store.write() as conn:
        _store_episodes(conn, sid, data['seasons'])
        if data.get('group') is not None:
            _store_group(conn, sid, data['group'])
        conn.execute('UPDATE series SET tmdb_refreshed_at = ? WHERE id = ?', (now, sid))


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


def scan_files(sid: int, path_id: str, folder: str, root_real: str, now: float) -> Dict[str, int]:
    show_dir = os.path.join(root_real, folder)
    found = list(video_files(show_dir, root_real))
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
                ' mtime = excluded.mtime, dev = excluded.dev, ino = excluded.ino, resolution = excluded.resolution,'
                ' release_group = excluded.release_group, proper = excluded.proper, state = excluded.state,'
                ' parsed = excluded.parsed',
                (sid, path_id, rel_path, st.st_size, st.st_mtime, st.st_dev, st.st_ino, r.resolution or None,
                 r.group or None, int(r.proper or 0), now, state, parsed))
            fid = conn.execute('SELECT id FROM episode_files WHERE path_id = ? AND rel_path = ?',
                               (path_id, rel_path)).fetchone()['id']
            conn.execute('DELETE FROM episode_file_links WHERE file_id = ?', (fid,))
            for eid in ids:
                conn.execute('INSERT OR IGNORE INTO episode_file_links (file_id, episode_id) VALUES (?, ?)', (fid, eid))
        for row in conn.execute('SELECT id, rel_path FROM episode_files WHERE series_id = ?', (sid,)).fetchall():
            if row['rel_path'] not in keep:
                conn.execute('DELETE FROM episode_files WHERE id = ?', (row['id'],))
        conn.execute('UPDATE series SET last_scan_at = ? WHERE id = ?', (now, sid))
    return counts


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


def detach_path(path_id: str, now: Optional[float] = None) -> int:
    """A Library path now points at another folder: its shows are hidden with
    their history kept (detach), its unmatched rows go. The next scan matches
    the new folder's shows, taking back any it already knew."""
    now = time.time() if now is None else now
    with tv_store.write() as conn:
        ids = [r['id'] for r in conn.execute('SELECT id FROM series WHERE path_id = ?', (path_id,))]
        for sid in ids:
            detach(conn, sid, now, 'repointed')
        conn.execute('DELETE FROM unmatched WHERE path_id = ?', (path_id,))
    return len(ids)


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
    A show whose library is no longer selected (or no longer a Library path)
    moves here outright, keeping its tv.db row; its file rows from that
    library are dropped (rows only)."""
    if owner['path_id'] is None or owner['folder'] is None or _path_of(owner['path_id']) is None:
        # hidden, added from Search (no folder yet), or its library is gone: the folder takes it
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


def _path_of(path_id: str) -> Optional[str]:
    from app import tv_settings
    return next((p['path'] for p in tv_settings.libraries() if p['id'] == path_id), None)


# --------------------------------------------------------------------------- scan

def _hints(root_real: str, folder: str) -> tv_match.Hints:
    return tv_match.file_hints(rel for rel, _ in video_files(os.path.join(root_real, folder), root_real))


def _tick(tier: Optional[str]) -> None:
    with _progress_lock:
        _progress['done'] += 1
        key = {'id': 'matched', 'high': 'matched', 'known': 'matched', 'likely': 'review',
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
            m = tv_match.match(folder, hints)
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


def scan_path(rec: Dict[str, Any], now: float, retry_unmatched: bool = True) -> Dict[str, int]:
    from app import file_management as fm
    root_real = os.path.realpath(rec['path'])
    if fm.blocked(root_real) or not os.path.isdir(root_real):
        raise FileNotFoundError(MISSING)
    pid = rec['id']
    folders = show_folders(root_real)
    out = {'shows': 0, 'unmatched': 0, 'linked': 0, 'mismatch': 0, 'unrecognized': 0}
    with tv_store.connect() as conn:
        known = {r['folder']: dict(r) for r in conn.execute(
            'SELECT id, folder, tmdb_id, tmdb_refreshed_at, episode_group_id FROM series WHERE path_id = ?'
            ' AND folder IS NOT NULL', (pid,))}       # shows added from Search have no folder to lose
        unmatched = {r['folder'] for r in conn.execute('SELECT folder FROM unmatched WHERE path_id = ?', (pid,))}
    with tv_store.write() as conn:            # folders that are gone: rows only, history kept
        for f in set(known) - set(folders):
            detach(conn, known[f]['id'], now, 'folder gone')
        for f in unmatched - set(folders):
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
            sid = None
            if m['status'] == 'matched':
                with tv_store.connect() as conn:
                    owner = conn.execute('SELECT * FROM series WHERE tmdb_id = ?', (m['tmdb_id'],)).fetchone()
                if owner is not None:
                    sid = _resolve_duplicate(owner, pid, folder, root_real, now, via=m['via'])
                    if sid is not None and owner['path_id'] == pid:
                        demoted.add(owner['folder'])
                        _move('matched', 'review')
                        if owner['folder'] in done:   # already counted as a show
                            out['shows'] -= 1
                            out['unmatched'] += 1
                elif res.get('data'):
                    sid = store_show(pid, folder, m['tmdb_id'], now, via=m['via'], data=res['data'], tier=m['tier'])
                if sid is None and owner is None:     # TMDB lost the show between the two reads
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


def refresh_added(now: Optional[float] = None, folders: bool = False) -> int:
    """Shows added from Search (a library, no folder) keep their episodes
    current like folder shows: once REFRESH_EPISODES_S has passed, on the next
    scan (Import) or Calendar open (folders=True: every show of the selected
    libraries). TMDB reads on the workers through the shared rate budget; a
    TMDB error leaves the rest for next time. Returns how many were refreshed."""
    from app import tv_settings
    now = time.time() if now is None else now
    pids = [p['id'] for p in tv_settings.libraries()]
    if not pids:
        return 0
    with tv_store.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            'SELECT id, tmdb_id, episode_group_id, tmdb_refreshed_at FROM series WHERE'
            + ('' if folders else ' folder IS NULL AND') + ' tmdb_id IS NOT NULL AND path_id IN'
            f' ({",".join("?" * len(pids))})', pids)
            if now - (r['tmdb_refreshed_at'] or 0) >= REFRESH_EPISODES_S]

    def work(r):
        try:
            with tv_tmdb.fresh():
                return {'data': fetch_show(r['tmdb_id'], None, None, r['episode_group_id'])}
        except tv_tmdb.TmdbError as e:
            return {'error': e}
    n = 0
    for r, res in zip(rows, run_pool(rows, work)):
        if res.get('data'):
            refresh_episodes(r['id'], r['tmdb_id'], now, res['data'])
            n += 1
    return n


_refresh_lock = threading.Lock()
_RefreshThread = threading.Thread    # the Calendar-open refresh (tests run it inline)


def refresh_on_open() -> bool:
    """Calendar open: refresh stale air dates (24h) in the background, once at
    a time and never during an Import (which refreshes them itself). The open
    answers from tv.db right away; the next open shows what changed. False
    when nothing started."""
    if _scan_lock.locked() or not _refresh_lock.acquire(blocking=False):
        return False

    def work():
        try:
            refresh_added(folders=True)
        except Exception:                  # noqa: BLE001 - a failed refresh waits for the next open
            logger.exception('TV calendar refresh failed')
        finally:
            _refresh_lock.release()
    try:
        _RefreshThread(target=work, daemon=True, name='tv-calendar-refresh').start()
    except Exception:
        _refresh_lock.release()
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
           series_id: Optional[int], data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    with tv_store.connect() as conn:
        other = conn.execute('SELECT id FROM series WHERE tmdb_id = ?', (tmdb_id,)).fetchone()
    if other and other['id'] != series_id:
        # The show is in another folder (a duplicate): the owner's pick moves
        # it here and the other folder becomes the unmatched duplicate.
        sid = take_over(other['id'], path_id, folder, now, via=via, event=event, drop_series_id=series_id)
        root = _root_for(path_id)
        if root is not None and folder:
            scan_files(sid, path_id, folder, root, now)
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
        scan_files(sid, path_id, folder, root, now)
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
    now = time.time() if now is None else now
    with tv_store.connect() as conn:
        rows = {r['id']: dict(r) for r in conn.execute('SELECT id, path_id, folder FROM unmatched')}
    jobs, failed, seen = [], [], set()
    for uid, tid in items:
        row = rows.get(uid)
        if row is None:
            failed.append({'id': uid, 'error': NOT_FOUND})
        elif tid in seen:
            failed.append({'id': uid, 'error': TAKEN})
        else:
            seen.add(tid)
            jobs.append((row, tid))

    def fetch(job):
        row, tid = job
        root = _root_for(row['path_id'])
        try:
            return {'data': fetch_show(tid, None, _hints(root, row['folder']) if root else None)}
        except tv_tmdb.TmdbError as e:
            return {'error': e}
    imported = []
    for (row, tid), res in zip(jobs, run_pool(jobs, fetch)):
        if 'error' in res or res.get('skipped'):
            failed.append({'id': row['id'], 'error': str(res.get('error') or tv_tmdb.BUSY)})
            continue
        if not res.get('data'):
            failed.append({'id': row['id'], 'error': SHOW_NOT_FOUND})
            continue
        out = _apply(row['path_id'], row['folder'], tid, now, via='manual', event='matched', series_id=None,
                     data=res['data'])
        if out['ok']:
            imported.append(row['id'])
        else:
            failed.append({'id': row['id'], 'error': out['error']})
    return {'ok': True, 'imported': imported, 'failed': failed}


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


def library_view() -> Dict[str, Any]:
    from app import tv_settings
    # Only the selected libraries' shows; a deselected or removed one's rows
    # stay in tv.db and show again when it is selected again.
    names = {p['id']: p['name'] for p in tv_settings.libraries()}
    with tv_store.connect() as conn:
        shows = []
        for r in conn.execute(
                'SELECT s.id, s.tmdb_id, s.title, s.year, s.path_id, s.folder, s.poster_path, s.monitored,'
                " SUM(f.state = 'linked') AS linked, SUM(f.state = 'mismatch') AS mismatch,"
                " SUM(f.state = 'unrecognized') AS unrecognized, COUNT(f.id) AS files,"
                ' (SELECT COUNT(DISTINCT l.episode_id) FROM episode_file_links l'
                '  JOIN episode_files lf ON lf.id = l.file_id WHERE lf.series_id = s.id) AS episodes'
                ' FROM series s LEFT JOIN episode_files f ON f.series_id = s.id'
                ' WHERE s.path_id IS NOT NULL GROUP BY s.id ORDER BY s.title COLLATE NOCASE'):
            if r['path_id'] not in names:
                continue
            shows.append({'id': r['id'], 'tmdb_id': r['tmdb_id'], 'title': r['title'], 'year': r['year'],
                          'path_id': r['path_id'], 'path_name': names.get(r['path_id'], ''), 'folder': r['folder'],
                          'poster_path': r['poster_path'], 'monitored': bool(r['monitored']),
                          'files': r['files'] or 0, 'episodes': r['episodes'] or 0,
                          'linked': r['linked'] or 0, 'mismatch': r['mismatch'] or 0,
                          'unrecognized': r['unrecognized'] or 0,
                          'review': review_lines(conn, r['id']) if (r['mismatch'] or r['unrecognized']) else []})
        unmatched = []
        for r in conn.execute('SELECT * FROM unmatched ORDER BY folder COLLATE NOCASE'):
            if r['path_id'] not in names:
                continue
            try:
                cands = json.loads(r['candidates'] or '[]')
            except ValueError:
                cands = []
            tier = 'duplicate' if r['reason'] == 'duplicate' else (r['tier'] or 'none')
            pick = r['pick'] if tier == 'likely' else (cands[0]['tmdb_id'] if tier == 'duplicate' and cands else None)
            unmatched.append({'id': r['id'], 'path_id': r['path_id'], 'path_name': names.get(r['path_id'], ''),
                              'folder': r['folder'], 'guess_title': r['guess_title'], 'guess_year': r['guess_year'],
                              'candidates': cands[:tv_match.TOP_N], 'reason': r['reason'], 'tier': tier,
                              'pick': pick})
    # Review order: no match, then duplicates, then likely (best guess picked)
    unmatched.sort(key=lambda u: ({'none': 0, 'duplicate': 1, 'likely': 2}.get(u['tier'], 0), u['folder'].lower()))
    return {'shows': shows, 'unmatched': unmatched, 'scan': status()}
