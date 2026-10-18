"""PM rule: scan, import and match are STRICTLY read-only on library files.

1. Scan a fixture tree and assert that every file's bytes, mtime and mode and
   every folder's listing (and its mtime and mode) are unchanged, also on a
   second scan, a rescan after a fix-match, and over a read-only tree.
2. While scanning, any write-type filesystem call that touches the library
   fails the test.
3. Read the scanner modules: they may only list and stat (allowlist of os
   calls; no open(), shutil, tempfile, pathlib writes, subprocess)."""
import ast
import builtins
import os
import shutil
import stat
from pathlib import Path

import pytest

from app import tv_scan, tv_settings
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.test_tv_scan import select
from tests.tv_tree import build, snapshot

PID = 'p_0000bbbb'
APP = Path(__file__).resolve().parents[1] / 'app'
SCANNER_MODULES = ('tv_scan.py', 'tv_match.py', 'tv_library_routes.py')


@pytest.fixture
def lib(tv_env, fake_tmdb, tmp_path):  # noqa: F811
    root = build(tv_env / 'TV', outside=tmp_path / 'outside')
    os.chmod(root / 'Dark [tmdbid-70523]' / 'Dark.S01E01.mkv', 0o640)        # a mix of modes
    os.utime(root / 'Severance (2022)' / 'Season 01', ns=(1_600_000_000_000_000_000,) * 2)
    select(PID, root)
    return root


@pytest.fixture
def no_writes(lib, monkeypatch, tmp_path):
    """Fail on any write-type call that names a path in the library (or the
    outside folder a symlink points to)."""
    guarded = (os.path.realpath(lib), os.path.realpath(tmp_path / 'outside'))
    hits = []

    def inside(p):
        try:
            rp = os.path.realpath(os.fsdecode(p))
        except TypeError:
            return False
        return any(rp == g or rp.startswith(g + os.sep) for g in guarded)

    def guard(name, orig, n_paths=1):
        def wrapped(*a, **k):
            if any(inside(x) for x in a[:n_paths] if isinstance(x, (str, bytes, os.PathLike))):
                hits.append((name, a[:n_paths]))
                raise AssertionError(f'{name} on a library path: {a[:n_paths]}')
            return orig(*a, **k)
        return wrapped

    for name, n in (('rename', 2), ('replace', 2), ('remove', 1), ('unlink', 1), ('rmdir', 1), ('mkdir', 1),
                    ('makedirs', 1), ('chmod', 1), ('chown', 1), ('utime', 1), ('symlink', 2), ('link', 2),
                    ('truncate', 1), ('removedirs', 1), ('renames', 2), ('mkfifo', 1), ('mknod', 1)):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, guard(f'os.{name}', getattr(os, name), n))
    for name in ('move', 'copy', 'copy2', 'copyfile', 'copytree', 'rmtree'):
        monkeypatch.setattr(shutil, name, guard(f'shutil.{name}', getattr(shutil, name), 2))

    real_open, real_os_open = builtins.open, os.open

    def g_open(file, mode='r', *a, **k):
        writing = any(c in str(mode) for c in 'wax+')
        if writing and isinstance(file, (str, bytes, os.PathLike)) and inside(file):
            hits.append(('open', file, mode))
            raise AssertionError(f'open() on a library path: {file} {mode}')
        return real_open(file, mode, *a, **k)

    def g_os_open(path, flags, *a, **k):
        if inside(path) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            hits.append(('os.open', path, flags))
            raise AssertionError(f'os.open for writing on a library path: {path}')
        return real_os_open(path, flags, *a, **k)

    monkeypatch.setattr(builtins, 'open', g_open)        # reads: test_scan_reads_no_file_contents
    monkeypatch.setattr(os, 'open', g_os_open)
    return hits


def test_scan_leaves_every_file_and_folder_unchanged(lib, tmp_path):
    before = snapshot(lib), snapshot(tmp_path / 'outside')
    out = tv_scan.scan_all(now=1000.0)
    assert out['shows'] == 5 and out['unmatched'] == 3            # it really scanned
    assert (snapshot(lib), snapshot(tmp_path / 'outside')) == before
    tv_scan.scan_all(now=2000.0)                                     # rescan
    tv_scan.scan_all(now=3000.0 + tv_scan.REFRESH_EPISODES_S)        # with episode refresh
    assert (snapshot(lib), snapshot(tmp_path / 'outside')) == before


def test_no_write_calls_reach_the_library(lib, no_writes):
    tv_scan.scan_all(now=1000.0)
    tv_scan.scan_all(now=2000.0)
    assert no_writes == []


def test_fix_match_touches_no_files(lib, no_writes):
    tv_scan.scan_all(now=1000.0)
    before = snapshot(lib)
    from app import tv_store
    with tv_store.connect() as conn:
        sid = conn.execute("SELECT id FROM series WHERE folder = 'Severance (2022)'").fetchone()['id']
        un = conn.execute("SELECT id FROM unmatched WHERE folder = 'Mystery Show'").fetchone()['id']
    assert tv_scan.rematch(sid, 5000, now=2000.0)['ok']
    assert tv_scan.match_unmatched(un, 1001, now=2000.0)['ok']
    assert snapshot(lib) == before and no_writes == []


@pytest.mark.skipif(hasattr(os, 'geteuid') and os.geteuid() == 0, reason='root ignores file modes')
def test_scan_works_over_a_read_only_tree(lib, tmp_path):
    for dirpath, dirnames, filenames in os.walk(lib):
        for f in filenames:
            p = os.path.join(dirpath, f)
            if not os.path.islink(p):
                os.chmod(p, 0o444)
    for dirpath, _, _ in sorted(os.walk(lib), key=lambda t: -len(t[0])):
        os.chmod(dirpath, 0o555)
    try:
        before = snapshot(lib)
        out = tv_scan.scan_all(now=1000.0)
        assert out['shows'] == 5 and out['missing'] == []
        assert snapshot(lib) == before
    finally:
        for dirpath, _, _ in os.walk(lib):
            os.chmod(dirpath, 0o755)


# --------------------------------------------------------------------------- the code itself

OS_ALLOWED = {'scandir', 'path', 'stat_result', 'fsdecode', 'sep', 'environ', 'stat'}  # stat: read-only, for (dev, inode) dedupe
OS_PATH_ALLOWED = {'realpath', 'commonpath', 'join', 'splitext', 'isdir', 'basename', 'dirname', 'normpath',
                   'abspath', 'exists'}
# str.replace is fine (pathlib is banned, so there is no Path.replace to reach)
WRITE_ATTRS = {'write', 'write_text', 'write_bytes', 'writelines', 'unlink', 'rename', 'touch',
               'mkdir', 'rmdir', 'chmod', 'symlink_to', 'hardlink_to', 'truncate', 'move', 'copy', 'remove'}
DB_WRITE = ('tv_store', 'write')      # tv.db in the data dir: where everything the scan learns goes
BANNED_MODULES = {'shutil', 'tempfile', 'subprocess', 'pathlib', 'io', 'glob'}
BANNED_NAMES = {'open', 'writable', 'check_library_path', 'exec', 'eval', '__import__'}


def _tree(name):
    return ast.parse((APP / name).read_text(), filename=name)


@pytest.mark.parametrize('name', SCANNER_MODULES)
def test_scanner_code_only_lists_and_stats(name):
    tree = _tree(name)
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            problems += [a.name for a in node.names if a.name.split('.')[0] in BANNED_MODULES]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or '').split('.')[0] in BANNED_MODULES:
                problems.append(node.module)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'os':
            if node.attr not in OS_ALLOWED:
                problems.append(f'os.{node.attr}')
        elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
              and isinstance(node.value.value, ast.Name) and node.value.value.id == 'os' and node.value.attr == 'path'):
            if node.attr not in OS_PATH_ALLOWED:
                problems.append(f'os.path.{node.attr}')
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in BANNED_NAMES:
                problems.append(f'{f.id}()')
            elif isinstance(f, ast.Attribute) and f.attr in WRITE_ATTRS | BANNED_NAMES:
                if (getattr(f.value, 'id', None), f.attr) != DB_WRITE:
                    problems.append(f'.{f.attr}()')
    assert problems == [], f'{name} must stay read-only on library files: {problems}'


@pytest.mark.parametrize('src', ['import shutil', 'from pathlib import Path', 'open(p, "w")', 'os.rename(a, b)',
                                 'os.path.getsize(p)', 'p.write_text("x")', 'fm.writable(p)', 'os.unlink(p)',
                                 'other.write()', 'import tempfile', 'os.makedirs(p)'])
def test_the_code_check_catches_writes(src, tmp_path, monkeypatch):
    """The checker itself works (each snippet is flagged)."""
    (tmp_path / 'probe.py').write_text(f'import os\n{src}\n')
    monkeypatch.setitem(globals(), 'APP', tmp_path)
    with pytest.raises(AssertionError):
        test_scanner_code_only_lists_and_stats('probe.py')


def test_scan_reads_no_file_contents(lib, monkeypatch):
    """Only names and stat(): the scan never opens a library file at all."""
    opened = []
    real = builtins.open
    monkeypatch.setattr(builtins, 'open', lambda f, *a, **k: (opened.append(str(f)), real(f, *a, **k))[1])
    tv_scan.scan_all(now=1000.0)
    assert not [p for p in opened if p.startswith(str(lib))]
    assert stat.S_ISDIR(os.stat(lib).st_mode)


@pytest.fixture(autouse=True)
def _review_rows_stay(monkeypatch):
    """These tests drive the review flows (or count review rows): keep the pre-v157 rule so the small tree
    still has same-name rows in review. v157's auto-import is covered in test_tv_scan and the 687 fixture."""
    from app import tv_scan as _ts
    monkeypatch.setattr(_ts, 'AUTO_IMPORT', False)
