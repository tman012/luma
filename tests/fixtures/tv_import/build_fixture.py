"""Build tests/fixtures/tv_import/fixture.json: a deterministic, synthesized
TMDB for TV Import acceptance, from Taylor's real library as recorded by
Luma Tester (luma-qa/tv-import-analysis):

* tv_folders.txt: the 687 real folder names.
* tvdb_dump2.json: the 562 folders Luma matched (folder -> show title and
  year) and the 125 it didn't, each with TMDB's real top-5 search results
  (ids, titles, original titles, years, in TMDB's order).

Nothing here calls TMDB. What is synthesized (no recording exists):
* tmdb ids for the 562 matched shows (800000 + n; a recorded id when the same
  title and year was among the 125's results), and two non-exact distractors
  per show ('<title>: Unscripted', '<first word> Nights').
* vote_count: none. The recording has no vote counts, so every show has 0
  (popularity is neutral here; the matcher never decides by it anyway).
* origin_country for the few candidates where a country tag decides.
* season layouts (episode counts) and Taylor's files: the true show's layout
  in full; folders with no certain answer get only S01E01-E03.
* alternative titles only where a show's official full title is the
  folder's (Laguna Beach, The Jinx, Tiger King, Dateline NBC, WWE Raw,
  the two Animated Series, Pennyworth's original name).
* episode layouts for SpongeBob, Hard Knocks and Friends: TMDB's default
  layout and a TVDB-order episode group that fits Sonarr-named files, built
  to reproduce the 54/48/8 flagged files Tester recorded.

Run: python3 tests/fixtures/tv_import/build_fixture.py <analysis dir>
"""
import hashlib
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))


def norm(t):
    s = unicodedata.normalize('NFKD', str(t or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c)).casefold()
    s = s.replace('&', ' and ')
    s = re.sub(r"['\u2018\u2019*#!?\u2026]", '', s)
    s = re.sub(r'[^0-9a-z]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def h(*parts):
    return int(hashlib.sha256('|'.join(map(str, parts)).encode()).hexdigest()[:8], 16)


# The 125 unmatched folders: the true show (tmdb id).
# 'accept': several plausible answers (any auto-pick outside is wrong; review is right).
# None: no such show on TMDB (stays "None").
TRUTH = {
    '24': 1973, '48 Hours': 3719, 'Addiction Unplugged': None, 'All or Nothing': 66943,
    'Alone': 63726, 'American Sports Story': 131141, 'Ballers': 62704,
    'Band of Brothers': 4613, 'Below': {'accept': [285322, 207783]}, 'Boy Meets World': 1777,
    'Brothers': {'accept': [250203, 66515, 12635]}, 'Cheat (2019)': {'accept': [87356, 94828]},
    'Cheers': 141, 'Chernobyl': 87108, 'Dancing with the Stars': 1508,
    'Daredevil - Born Again': 202555, 'Dateline NBC': 78,
    'Dateline Secrets Uncovered': {'duplicate': 74284}, 'Diagnosis': {'accept': [73932, 91701]},
    'Disappeared': 26728, 'Disclaimer': 147050,
    "Don't Fk with Cats- Hunting an Internet Killer": 96129,
    'East of Eden': {'accept': [258165, 27291]}, 'Echo': 122226, 'Echoes': 129612,
    'Entourage': 1940, 'Euphoria (US)': 85552, 'Fallout': 106379,
    'Goliath': 67384, 'Gomorra': 61068, 'Gossip Girl': 1395, 'Homeland': 1407,
    'House of Cards (US)': 1425, 'House of Lies': 39702, 'How I Met Your Mother': 1100,
    'How to Get Rich': {'accept': [222839, 236340]}, 'Humans': 62822, 'Impractical Jokers': 59186,
    'Into the Dark': {'accept': [152478, 335646]}, 'Jury Duty': 222023, 'Killing Eve': 72750,
    'Laguna Beach - The Real Orange County': 3605, 'Last Chance U': 67027,
    'License To Kill': {'accept': [90626, 96018]},
    'Life, Larry and the Pursuit of Unhappiness': 295780, 'Little House on the Prairie': 1781,
    'Little Miss Innocent- Passioni Poison Prison': None, 'Lonesome Dove': 41692, 'Lost': 4607,
    'Love': 9000001, 'Love Is Blind': 99353, 'Love on the Spectrum (US)': 200731,
    'Malcolm in the Middle': 2004, 'Man v. Food': 8519, 'Maniac (US)': 9000002,
    'Medal of Honor': 83109, 'Modern Family': 1421, 'NFL Films Presents': None, 'Oz': 3322,
    'Painkiller': 158083, 'Pennyworth': 79588, 'Peppa Pig': 12225,
    'Planet Earth': 1044, 'Press': 82143, 'Reno 911!': 2187, 'Requiem': 76819,
    'Saturday Night at the Movies': {'accept': [128175, 11807]}, 'Saturday Night Live': 1667,
    'Scrubs': 4556, 'Shameless (US)': 34307, 'Shark Tank': 30703, 'Silo': 125988,
    'Sister Wives': 34045, 'Snoopy in Space': 91249, 'So You Think You Can Dance': 3976,
    'Spider-Man - The Animated Series': 888, 'Spider-Man- The Animated Series': 888,
    'Star Trek': 253, 'Step by Step': 2617, 'Super Pumped': 126301,
    'Survivor': 14658, 'Taxi': 2251, 'Teen Mom Girls’ Night In': {'duplicate': 153525},
    'The Affair': 61463, 'The Bachelorette': 1562, 'The Boys': 76479,
    'The Comedy Store': 109332, 'The Crown': 65494, 'The Curse': 114655,
    'The Hawk': {'accept': [254528, 109734, 169597]}, 'The IT Crowd': 2490,
    'The Jinx - The Life and Deaths of Robert Durst': 61929, 'The Keepers': 26101,
    'The Leftovers': 54344, 'The Morning Show': 90282, 'The Night Manager': 61859,
    'The Office': {'accept': [2316, 2996]}, 'The Office (US)': 2316, 'The Perfect Couple': 220564,
    'The Reagans': 110334, 'The Rehearsal': 204284, 'The Sinner': 39852,
    'The Son': 67232, 'The Staircase': {'accept': [122196, 21421]}, 'The Studio': 247767,
    'The Twilight Zone': 6357, 'The Wonder Years': 4500,
    'The Zen Diaries of Gary Shandling': None, 'This Is Us': 67136,
    'Tiger King- Murder, Mayhem and Madness': 100698, 'Top Chef': 41822,
    'Truth Be Told': 80054, 'Unbelievable': 91275, 'Unexpected': 75035,
    'Unsolved Mysteries': {'accept': [126, 105177]}, 'Wasteland': {'accept': [13927, 68406, 158507]},
    'What If…': 91363, 'What on Earth': {'accept': [70189, 37253, 44902, 127275]},
    'Will & Grace': 4454, 'WWE - The True Story of the Royal Rumble': None, 'WWE Raw': 4656,
    'WWE WrestleMania': None, 'X-Men- The Animated Series': 4574, 'You': 78191,
    'Your Honor (US)': 86430,
}
# Shows the 125's recorded top 5 didn't include (TMDB lists them lower).
EXTRA = {9000001: {'name': 'Love', 'year': 2016, 'country': ['US'], 'seasons': {1: 10, 2: 12, 3: 12}},
         9000002: {'name': 'Maniac', 'year': 2018, 'country': ['US'], 'seasons': {1: 10}},
         9000003: {'name': 'Maniac', 'year': 2014, 'country': ['NO'], 'seasons': {1: 6}}}
COUNTRY = {2316: ['US'], 2996: ['GB'], 256480: ['AU'], 90762: ['IN'], 128822: ['PL'], 1425: ['US'],
           85552: ['US'], 34307: ['US'], 86430: ['US'], 200731: ['US']}
ALIASES = {3605: ['Laguna Beach: The Real Orange County'], 61929: ['The Jinx: The Life and Deaths of Robert Durst'],
           100698: ['Tiger King: Murder, Mayhem and Madness'], 78: ['Dateline NBC'], 4656: ['WWE Raw'],
           888: ['Spider-Man: The Animated Series'], 4574: ['X-Men: The Animated Series'], 79588: ['Pennyworth']}
# Real season counts for the true shows where same-name rivals exist (files then decide).
LAYOUT = {141: [22, 22, 25, 26, 26, 25, 25, 26, 26, 26, 28], 1407: [12] * 8, 1940: [8, 12, 20, 12, 12, 12, 10, 8],
          1395: [18, 25, 22, 22, 24, 10], 1100: [22, 22, 20, 24, 24, 24, 24, 24, 24], 1421: [24] * 11,
          4556: [24, 22, 22, 25, 24, 22, 11, 19, 13], 4454: [22, 24, 25, 27, 24, 24, 24, 24, 18, 18, 18],
          6357: [36, 29, 37, 18, 36], 4500: [6, 22, 23, 23, 26, 24], 1777: [22, 23, 22, 22, 24, 22, 23],
          2004: [16, 25, 22, 22, 22, 22, 22], 67136: [18, 18, 18, 18, 16, 18], 1973: [24] * 8 + [12],
          54344: [10, 10, 8], 2490: [6, 6, 6, 6, 1], 72750: [8, 8, 8, 8], 3322: [8, 8, 8, 8, 8, 8],
          9000001: [10, 12, 12], 2316: [6, 22, 22, 19, 28, 26, 26, 24, 25], 1667: [20] * 49,
          87108: [5], 76479: [8, 8, 8, 8], 4613: [10], 1044: [11], 85552: [8, 8, 8],
          3719: [20] * 37, 30703: [22] * 16, 3976: [20] * 18, 1562: [10] * 21, 2187: [16] * 8, 80054: [8, 8, 10],
          67027: [8] * 5, 26728: [6] * 10, 109332: [5], 110334: [4], 59186: [16] * 10, 14658: [14] * 47,
          41822: [16] * 22, 12225: [52] * 8, 2617: [22] * 7, 2251: [22] * 5, 39702: [12] * 5, 41692: [4],
          1781: [24] * 9, 4607: [24] * 6, 253: [29, 26, 24]}
EPISODE_SHOWS = {
    # name: (default layout, TVDB group layout, multi-episode files in the TVDB layout)
    'Friends': ([24, 24, 25, 23, 23, 23, 23, 23, 23, 17], [24, 24, 25, 24, 24, 25, 24, 24, 24, 18],
                [(9, 23, 24), (10, 17, 18)]),
    'SpongeBob SquarePants': ([20, 20, 20, 20, 20, 26, 26, 26, 26, 20, 51, 26, 26],
                              [20, 20, 20, 20, 20, 26, 26, 26, 26, 71, 55, 26, 26], [(11, 54, 55)]),
    'Hard Knocks (2001)': ([5, 6, 5, 5, 5, 5, 5, 5, 6, 5, 5, 5, 5, 5, 5, 5, 6, 5, 5, 5],
                           [5, 6, 5, 5, 5, 5, 5, 5, 6, 5, 5, 5, 5, 5, 5, 5, 9, 5, 8, 5, 9, 5, 5, 8, 5, 7, 5], []),
}


def _fix(name):
    """tv_folders.txt came through a Windows console: UTF-8 shown as cp437."""
    try:
        return name.encode('cp437').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def main(src):
    folders = [_fix(l.rstrip('\n')) for l in open(os.path.join(src, 'tv_folders.txt'), encoding='utf-8-sig')
               if l.strip()]
    dump = json.load(open(os.path.join(src, 'tvdb_dump2.json'), encoding='utf-8-sig'))
    shows, searches, truth, files = {}, {}, {}, {}

    def put(tid, name, year, orig=None, country=None, seasons=None):
        s = shows.setdefault(str(tid), {'name': name, 'original_name': orig or name,
                                        'first_air_date': f'{year}-01-15' if year else '',
                                        'origin_country': country or [], 'vote_count': 0,
                                        'aliases': ALIASES.get(tid, [])})
        if seasons:
            s['seasons'] = seasons
        return s

    known = {r['folder'] for r in dump['folder_title_vs_show']} | {u['folder'] for u in dump['unmatched']}
    assert set(folders) == known and len(folders) == 687, set(folders) ^ known
    by_title = {}
    for u in dump['unmatched']:
        cands = json.loads(u['candidates'] or '[]')
        searches[norm(u['guess_title'])] = [c['tmdb_id'] for c in cands]
        for c in cands:
            put(c['tmdb_id'], c['title'], c['year'], c.get('original_title'), COUNTRY.get(c['tmdb_id']))
            by_title.setdefault((norm(c['title']), c['year']), c['tmdb_id'])
    for tid, e in EXTRA.items():
        if norm(e['name']) in searches:          # listed right after the recorded top 5
            searches[norm(e['name'])].append(tid)
        put(tid, e['name'], e['year'], None, e['country'],
            {str(k): v for k, v in e['seasons'].items()})
    # the 562 matched: the show, plus two non-exact distractors
    matched = {}
    for n, r in enumerate(dump['folder_title_vs_show']):
        tid = by_title.get((norm(r['title']), r['year'])) or 800000 + n
        put(tid, r['title'], r['year'])
        matched[r['folder']] = tid
        put(700000 + n, f"{r['title']}: Unscripted", (r['year'] or 2000) + 5)
        first = r['title'].split()[0] if r['title'].split() else 'Show'
        put(750000 + n, f'{first} Nights', 2011 + n % 12)
    # season layouts for every show (synthesized unless LAYOUT knows it)
    for sid, s in shows.items():
        if 'seasons' in s:
            continue
        lay = LAYOUT.get(int(sid))
        if lay is None:
            k = h('s', sid)
            lay = [6 + (k >> (4 * i)) % 17 for i in range(1 + k % 3)] if int(sid) < 700000 or int(sid) >= 800000 \
                else [8]
        s['seasons'] = {str(i + 1): c for i, c in enumerate(lay)}
    for name, (default, tvdb, _multi) in EPISODE_SHOWS.items():
        tid = matched[name]
        # TMDB holds every episode; those its seasons don't number like TVDB sit in Specials
        extra = sum(max(t - (default[i] if i < len(default) else 0), 0) for i, t in enumerate(tvdb))
        shows[str(tid)]['seasons'] = {'0': extra, **{str(i + 1): c for i, c in enumerate(default)}}
        shows[str(tid)]['episode_groups'] = [
            {'id': f'eg{tid}a', 'name': 'Aired Order (TVDB)', 'type': 1,
             'seasons': {str(i + 1): c for i, c in enumerate(tvdb)}},
            {'id': f'eg{tid}d', 'name': 'DVD', 'type': 3, 'seasons': {'1': sum(tvdb[:3])}}]
    # truth and Taylor's files
    for f in folders:
        if f in matched:
            truth[f] = {'id': matched[f]}
            tid = matched[f]
        else:
            t = TRUTH[f]
            if t is None:
                truth[f] = {'id': None}
                tid = None
            elif isinstance(t, int):
                truth[f] = {'id': t}
                tid = t
            elif 'duplicate' in t:
                truth[f] = {'id': t['duplicate'], 'duplicate': True}
                tid = t['duplicate']
            else:
                truth[f] = {'id': None, 'accept': t['accept']}
                tid = None
        if f in EPISODE_SHOWS:
            files[f] = {'tvdb': True}
        elif tid is not None and str(tid) in shows:
            files[f] = {'seasons': shows[str(tid)]['seasons']}
        else:
            files[f] = {'seasons': {'1': 3}}
    out = {'about': __doc__.strip().splitlines()[0], 'folders': folders, 'shows': shows, 'searches': searches,
           'truth': truth, 'files': files,
           'episode_shows': {k: {'multi': v[2]} for k, v in EPISODE_SHOWS.items()}}
    with open(os.path.join(HERE, 'fixture.json'), 'w') as fh:
        json.dump(out, fh, sort_keys=True, separators=(',', ':'))
    print(len(folders), 'folders,', len(shows), 'shows,', len(searches), 'recorded searches')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '/workspace/luma-qa/tv-import-analysis')
