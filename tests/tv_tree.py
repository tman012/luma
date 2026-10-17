"""A fixture TV library tree for scan tests."""
import os

FILES = {
    'Severance (2022)/Season 01/Severance - S01E01 - Good News About Hell.mkv': b'a' * 64,
    'Severance (2022)/Season 01/Severance.S01E02.1080p.WEB-DL-GRP.mkv': b'b' * 64,
    'Severance (2022)/Season 01/Severance.S02E05.mkv': b'c' * 64,           # folder says 1: mismatch
    'Severance (2022)/Season 02/Severance.S02E01E02.2160p.mkv': b'd' * 64,
    'Severance (2022)/Season 02/Severance.S02E40.mkv': b'e' * 64,           # no such episode: mismatch
    'Severance (2022)/Extras/Behind the scenes.mkv': b'f' * 64,             # unrecognized
    'Severance (2022)/Season 01/folder.jpg': b'jpg',                         # not a video
    'Severance (2022)/Season 01/Severance.S01E01.nfo': b'<nfo/>',
    'The Office (US) {tvdb-73244}/Season 2/05.mkv': b'g' * 64,             # episode only, Season folder
    'Dark [tmdbid-70523]/Dark.S01E01.mkv': b'h' * 64,
    'Taskmaster [imdbid-tt4934214]/Taskmaster.S01E03.mkv': b'i' * 64,
    'Mystery Show/Mystery.Show.S01E01.mkv': b'j' * 64,                     # two hits: unmatched
    'Zzz Nothing/x.mkv': b'k' * 64,                                           # no hits: unmatched
    'The Daily Show/The.Daily.Show.2024.10.03.mkv': b'l' * 64,               # daily: linked
    '.hidden/whatever.mkv': b'm' * 64,
}


def build(root, outside=None):
    for rel, data in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (root / 'Empty Show (2020)').mkdir()
    (root / 'readme.txt').write_text('top-level file, not a show')
    if outside is not None:
        (outside / 'Escaped (2010)').mkdir(parents=True)
        (outside / 'Escaped (2010)' / 'Escaped.S01E01.mkv').write_bytes(b'z' * 64)
        os.symlink(outside / 'Escaped (2010)', root / 'Escaped (2010)')
        os.symlink(outside / 'Escaped (2010)' / 'Escaped.S01E01.mkv', root / 'Dark [tmdbid-70523]' / 'link.S01E02.mkv')
    return root


def snapshot(root):
    """Every entry's bytes, mtime, mode, and every folder's listing."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        st = os.lstat(dirpath)
        out[dirpath] = ('dir', sorted(dirnames + filenames), st.st_mtime_ns, st.st_mode)
        for f in filenames:
            p = os.path.join(dirpath, f)
            st = os.lstat(p)
            data = open(p, 'rb').read() if not os.path.islink(p) else os.readlink(p).encode()
            out[p] = ('file', data, st.st_mtime_ns, st.st_mode, st.st_size)
    return out
