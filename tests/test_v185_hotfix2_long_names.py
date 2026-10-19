"""v185 hotfix 2 (Reviewer/PM, REQUIRED, FF1): a ~215-character episode title (the Severance S02E03 repro)
made '.<name>.<hex8>.luma-partial' longer than 255 bytes: ENAMETOOLONG, the import stayed 'waiting'
and retried forever, the grab never closed. Now the partial is a short fixed name, every final name
(sidecars too) is at most 240 bytes with only the title part shortened (SxxEyy, quality and the
extension kept), and a persistent OSError backs off and then fails with Retry.
Temp folders and a fake qBittorrent only."""
import errno
import logging
import os

import pytest

from app import library_mover as lm
from app import tv_downloads, tv_naming, tv_store
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_download_watch_v158a import H1, dl, grab  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.test_file_management import HDR, minimal as minimal_app  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

D = tv_naming.DEFAULTS
LONG = ('Who Is Alive? A Very Long Episode Title That Keeps Going Past Every Sensible Limit For A File Name, '
        'Because Some Shows Really Do This, And Then Some More Words To Make Absolutely Sure It Is Over Two Hundred Fifteen Characters')


def _b(s):
    return len(s.encode('utf-8'))


def test_the_repro_title_is_long_enough():
    assert len(LONG) >= 215


def test_a_long_title_fits_with_room_for_any_sidecar():
    c = {'title': 'Severance', 'season': 2, 'episodes': [3], 'titles': [LONG]}
    name = tv_naming.episode_name(c, '.mkv', 'standard', D)
    assert name.startswith('Severance - S02E03 - Who Is Alive') and name.endswith('.mkv')
    an = tv_naming.episode_name(c, '.mkv', 'anime', D)                     # SxxEyy and quality stay; the title is cut
    assert ' - S02E03 - Who Is Alive' in an and an.endswith('.mkv')
    assert _b(an) <= tv_naming.NAME_MAX_BYTES
    assert _b(name) <= tv_naming.NAME_MAX_BYTES
    stem = name[:-4]
    for suffix in ('.en.srt', '.en.forced.srt', '.eng.sdh.forced.srt'):
        side = tv_naming._fit(stem, suffix)
        assert side == stem + suffix and _b(side) <= tv_naming.NAME_MAX_BYTES     # the very same stem


def test_multi_episode_joined_titles_fit():
    c = {'title': 'The IT Crowd', 'season': 1, 'episodes': [1, 2, 3], 'titles': [LONG, LONG + ' II', LONG + ' III']}
    for style in ('extend', 'duplicate', 'repeat', 'scene', 'range', 'prefixed_range'):
        n = tv_naming.episode_name(c, '.mkv', 'standard', {**D, 'multi_episode_style': style})
        assert n.endswith('.mkv') and _b(n) <= tv_naming.NAME_MAX_BYTES, style
        assert n.startswith('The IT Crowd - S01E01'), (style, n)               # SxxEyy kept


def test_multibyte_titles_are_cut_on_a_character_boundary():
    c = {'title': 'Ōoku', 'season': 1, 'episodes': [1], 'titles': ['大奥' * 80]}
    n = tv_naming.episode_name(c, '.mkv', 'standard', D)
    assert _b(n) <= tv_naming.NAME_MAX_BYTES and n.encode('utf-8').decode('utf-8') == n


def test_the_partial_name_is_short_and_fixed(tmp_path):
    fd = os.open(str(tmp_path), os.O_RDONLY)
    try:
        tmp, wfd, _ = lm._new_partial(fd, 'x' * 250 + '.mkv')
        os.close(wfd)
    finally:
        os.close(fd)
    assert tmp.startswith('.luma-') and tmp.endswith(lm.PARTIAL_SUFFIX) and len(tmp) == len('.luma-') + 16 + len(lm.PARTIAL_SUFFIX)


def test_the_long_title_import_lands_and_the_grab_closes(dl, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tv_downloads, '_naming', lambda: dict(D))
    with tv_store.write() as conn:
        sid = conn.execute("SELECT id FROM series WHERE title = 'Severance'").fetchone()['id']
        row = conn.execute('SELECT id FROM episodes WHERE series_id = ? AND season_number = 2 AND episode_number = 3', (sid,)).fetchone()
        if row:
            conn.execute('UPDATE episodes SET title = ?, monitored = 1 WHERE id = ?', (LONG, row['id']))
        else:
            conn.execute('INSERT INTO episodes (series_id, season_number, episode_number, title, air_date, monitored)'
                         " VALUES (?, 2, 3, ?, '2025-01-31', 1)", (sid, LONG))
    rel = 'Severance.S02E03.1080p.WEB-DL-GRP'
    d = grab(dl, H1, rel, [rel + '.mkv'])
    (d / (rel + '.en.forced.srt')).write_text('1\n00:00:01,000 --> 00:00:02,000\nhi\n')
    assert dl.tick()['imported'] == 1
    st = tv_downloads.status(H1)
    assert st['state'] == 'imported'
    names = [os.path.basename(f['path']) for f in st['files']] + [os.path.basename(x['path']) for f in st['files'] for x in f.get('sidecars', [])]
    assert names and all(_b(n) <= tv_naming.NAME_MAX_BYTES for n in names)
    assert any(n.endswith('.en.forced.srt') for n in names)
    for f in st['files']:
        assert os.path.isfile(f['path'])
        assert not [n for n in os.listdir(os.path.dirname(f['path'])) if n.endswith(lm.PARTIAL_SUFFIX)]


def test_a_persistent_oserror_backs_off_logs_once_and_then_fails_with_retry(dl, monkeypatch, caplog):  # noqa: F811
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise OSError(errno.ENAMETOOLONG, 'File name too long')
    monkeypatch.setattr(tv_downloads, 'import_torrent', boom)
    grab(dl, H1, 'Severance.S01E01.1080p.WEB-DL-GRP', ['Severance.S01E01.1080p.WEB-DL-GRP.mkv'])
    t0 = 1_000_000.0
    with caplog.at_level(logging.WARNING, logger='app.tv_downloads'):
        dl.tick(now=t0)
        dl.tick(now=t0 + 10)                                  # in the backoff: not tried
        assert len(calls) == 1
        st = tv_downloads.status(H1)
        assert (st['state'], st['error'], st['os_errors']) == ('waiting', 'file error (ENAMETOOLONG)', 1)
        now = t0
        for _ in range(10):
            now = float(tv_downloads.status(H1).get('next_try') or now) + 1
            dl.tick(now=now)
            if tv_downloads.status(H1)['state'] == 'failed':
                break
    st = tv_downloads.status(H1)
    assert st['state'] == 'failed' and len(calls) == tv_downloads.OS_ERROR_MAX
    hits = [r for r in caplog.records if 'backing off' in r.getMessage()]
    assert len(hits) == 1                                     # logged once, not every pass
    dl.tick(now=now + 10 ** 6)
    assert len(calls) == tv_downloads.OS_ERROR_MAX            # failed: no more tries until Retry
    f = [x for x in tv_downloads.failures(now) if x['hash'] == H1]
    assert f and f[0]['retry'] is True
    assert tv_downloads.retry(H1)
    dl.tick(now=now + 10 ** 6 + 1)
    assert len(calls) == tv_downloads.OS_ERROR_MAX + 1


def test_only_the_title_part_is_shortened_quality_and_group_stay():
    c = {'title': 'Severance', 'season': 2, 'episodes': [3], 'titles': [LONG], 'quality': '1080p-web', 'group': 'NTb'}
    fmt = {**D, 'standard': '{Series Title} - S{season:00}E{episode:00} - {Episode Title} {Quality Full}-{Release Group}'}
    n = tv_naming.episode_name(c, '.mkv', 'standard', fmt)
    assert n.startswith('Severance - S02E03 - Who Is Alive') and n.endswith(' WEBDL-1080p-NTb.mkv'), n
    assert _b(n) <= tv_naming.NAME_MAX_BYTES - tv_naming.SIDECAR_ROOM + 4


def test_a_very_long_series_title_is_cut_after_the_episode_titles():
    c = {'title': 'A' * 300, 'season': 1, 'episodes': [1], 'titles': ['B' * 50]}
    n = tv_naming.episode_name(c, '.mkv', 'standard', D)
    assert ' - S01E01' in n and n.endswith('.mkv') and _b(n) <= tv_naming.NAME_MAX_BYTES


def test_short_names_are_unchanged():
    c = {'title': 'Time', 'season': 3, 'episodes': [3], 'titles': ['Episode 3']}
    assert tv_naming.episode_name(c, '.mkv', 'standard', D) == 'Time - S03E03 - Episode 3.mkv'
