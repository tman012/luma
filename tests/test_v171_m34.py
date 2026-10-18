"""v171 M34 (Reviewer, on ab030b7) + PM coverage guard.

A dropped mount with show folders present is scanned (and re-recorded) ONLY when at least one folder
the path already has series or review rows for is present; otherwise it is skipped as dropped
(Unavailable). Under half of the known folders present is Unavailable too (coverage guard): rows kept,
nothing detached, nothing new matched. Fake TMDB, temp folders only."""
import os
import shutil

import pytest

from app import tv_scan, tv_store
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401
from tests.test_tv_m34 import _state, _visible
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401


@pytest.fixture
def mount(lib, monkeypatch):  # noqa: F811
    """*lib* is its own mount point until mount['on'] goes False."""
    real = os.path.ismount
    m = {'on': True}
    root = os.path.realpath(lib)
    monkeypatch.setattr(os.path, 'ismount', lambda p: (m['on'] and os.path.realpath(p) == root) or real(p))
    return m


def _known():
    return ({r['folder'] for r in rows('SELECT folder FROM series WHERE path_id = ? AND folder IS NOT NULL', PID)}
            | {r['folder'] for r in rows('SELECT folder FROM unmatched WHERE path_id = ?', PID)})


def test_a_dropped_mount_with_only_a_stray_show_is_skipped_no_detach_no_takeover(lib, mount, tmp_path):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    assert tv_store.get_meta(tv_scan.MOUNT_META + PID) == os.path.realpath(lib)
    before, seen, known = _state(), _visible(), _known()
    shutil.move(str(lib), str(tmp_path / 'share'))
    lib.mkdir()                                                   # the bare mount point...
    (lib / 'Leftover Show (2001)').mkdir()                        # ...with a stray folder on it
    (lib / 'Leftover Show (2001)' / 'Leftover.Show.S01E01.mkv').write_bytes(b'x' * 64)
    mount['on'] = False
    for t in (2000.0, 3000.0):
        out = tv_scan.scan_all(now=t)
        assert out['missing'] == [PID]
    assert _state() == before and _visible() == seen              # no detach
    assert 'Leftover Show (2001)' not in _known() and _known() == known   # no takeover
    assert tv_scan.skipped(PID)                                    # Settings: 'Unavailable'
    assert tv_store.get_meta(tv_scan.MOUNT_META + PID) == os.path.realpath(lib)   # not re-recorded


def test_a_retired_mount_with_the_originals_copied_locally_scans_and_finds_the_new_show(lib, mount, tmp_path):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    before = _state()
    share = tmp_path / 'share'
    shutil.move(str(lib), str(share))
    shutil.copytree(str(share), str(lib))                         # copied onto the local disk
    (lib / 'Brand New Show (2026)').mkdir()
    mount['on'] = False                                           # the mount is retired
    out = tv_scan.scan_all(now=2000.0)
    assert out['missing'] == [] and not tv_scan.skipped(PID)
    assert 'Brand New Show (2026)' in _known()                    # scanned: the new one is found
    assert _state()[0] == before[0]                               # nothing detached
    assert tv_store.get_meta(tv_scan.MOUNT_META + PID) == tv_scan._mount_of(str(lib)) != os.path.realpath(lib)


def test_a_dropped_mount_with_junk_only_is_skipped(lib, mount, tmp_path):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    before = _state()
    shutil.move(str(lib), str(tmp_path / 'share'))
    lib.mkdir()
    for j in ('lost+found', '@eaDir', '#recycle', '.Trash-1000'):
        (lib / j).mkdir()
    mount['on'] = False
    assert tv_scan.scan_all(now=2000.0)['missing'] == [PID]
    assert _state() == before and tv_scan.skipped(PID)


def test_a_dropped_mount_with_one_known_folder_still_needs_half(lib, mount, tmp_path):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    before, known = _state(), sorted(_known())
    share = tmp_path / 'share'
    shutil.move(str(lib), str(share))
    lib.mkdir()
    shutil.copytree(str(share / known[0]), str(lib / known[0]))   # one known folder of many
    mount['on'] = False
    assert tv_scan.scan_all(now=2000.0)['missing'] == [PID]
    assert _state() == before                                     # the coverage guard holds


@pytest.mark.parametrize('frac_back,unavailable', [(0, True), (0.25, True), (0.5, False), (1, False)])
def test_coverage_guard_on_a_mounted_path(lib, tmp_path, frac_back, unavailable):  # noqa: F811
    """PM: under half of the prior shows present is Unavailable, no detach; half or more is a real scan."""
    tv_scan.scan_all(now=1000.0)
    before, known = _state(), sorted(_known())
    n = int(len(known) * frac_back + 0.5) if frac_back else 0
    if frac_back == 0.5:
        n = (len(known) + 1) // 2
    away = tmp_path / 'away'
    away.mkdir()
    for f in os.listdir(lib):
        if f in known[n:]:
            shutil.move(str(lib / f), str(away / f))
    (lib / 'Unrelated New Show (2026)').mkdir()
    out = tv_scan.scan_all(now=2000.0)
    if unavailable:
        assert out['missing'] == [PID] and _state() == before and tv_scan.skipped(PID)
        assert 'Unrelated New Show (2026)' not in _known()
    else:
        assert out['missing'] == [] and not tv_scan.skipped(PID)
        assert 'Unrelated New Show (2026)' in _known()


def test_a_repointed_path_is_not_held_by_the_old_folders_rows(lib, tmp_path):  # noqa: F811
    from app import file_management as fm
    from app import tv_settings  # noqa: F401 - registers the path hooks
    tv_scan.scan_all(now=1000.0)
    new = tmp_path / 'TV2'
    (new / 'Other Show (2020)').mkdir(parents=True)
    select(PID, new)
    fm._path_repointed(PID)
    assert tv_store.get_meta(tv_scan.ROOT_META + PID) == tv_scan.REPOINTED
    assert tv_scan._unavailable(PID, os.path.realpath(new), ['Other Show (2020)']) is None


# ------------------------------------------------------------------ v171 (Reviewer): AVZ_ID_REJECTED_RE

@pytest.mark.parametrize('body', [
    'Rate limit exceeded for imdb lookups, unknown client',
    'Invalid request: too many IMDb searches',
    'Invalid token for imdb search',
    'Invalid token ... imdb',
    'imdb id invalid: slow down',
    'Unknown imdb id; throttled',
    'Invalid session (imdb)',
    'Login required: imdb id unknown',
    'Auth failed, imdb invalid',
    'Too many attempts: imdb not valid',
    'IMDB_ID invalid. Limit reached',
])
def test_rate_or_sign_in_words_are_never_an_invalid_id(body):
    from app import tracker_clients
    assert tracker_clients.AVZ_ID_REJECTED_RE.search(body) is None


@pytest.mark.parametrize('body', ['The imdb id is invalid', 'Invalid IMDb ID', '{"message":"tmdb_id not found"}',
                                  'imdb: malformed value tt12'])
def test_a_plain_invalid_id_body_still_counts(body):
    from app import tracker_clients
    assert tracker_clients.AVZ_ID_REJECTED_RE.search(body) is not None


# ------------------------------------------------------------------ v171 (Reviewer): the XML guard, by AST

import ast  # noqa: E402
import pathlib  # noqa: E402

PARSE_FNS = {'fromstring', 'fromstringlist', 'parse', 'XML', 'XMLID', 'XMLParser', 'XMLPullParser', 'iterparse',
             'parseString', 'make_parser', 'ParserCreate', 'TreeBuilder'}
ET_MODS = {'xml.etree.ElementTree', 'xml.etree.cElementTree', 'xml.etree'}
NEVER_MODS = ('xml.dom', 'xml.sax', 'xml.parsers', 'lxml', 'xmlrpc')      # no direct use at all outside safe_xml


def xml_offenders(src: str, name: str = 'x.py') -> list:
    """Every way *src* could parse XML without safe_xml: a from-import of a parser function (aliased
    or not), any import of minidom/sax/expat/lxml, or a parser call on a name bound to ElementTree
    (import ... as X, from xml.etree import ElementTree as X, or the full dotted path)."""
    tree = ast.parse(src)
    out, et_names = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith(NEVER_MODS):
                    out.append(f'{name}:{node.lineno} import {a.name}')
                elif a.name in ET_MODS:
                    et_names.add(a.asname or a.name)
                elif a.name == 'xml' or a.name.startswith('xml.'):
                    et_names.add(a.asname or a.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            if mod.startswith(NEVER_MODS):
                out.append(f'{name}:{node.lineno} from {mod}')
                continue
            for a in node.names:
                full = f'{mod}.{a.name}'
                if full.startswith(NEVER_MODS) or (mod == 'xml' and a.name in ('dom', 'sax', 'parsers')):
                    out.append(f'{name}:{node.lineno} from {mod} import {a.name}')
                elif mod in ET_MODS and a.name in PARSE_FNS:
                    out.append(f'{name}:{node.lineno} from {mod} import {a.name}')
                elif full in ET_MODS or (mod in ET_MODS and a.name in ('ElementTree', 'cElementTree')):
                    et_names.add(a.asname or a.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in PARSE_FNS:
            base = ast.unparse(node.value)
            if base in et_names or any(base == n or base.startswith(n + '.') for n in et_names if '.' in n or n == 'xml'):
                out.append(f'{name}:{node.lineno} {base}.{node.attr}')
    return sorted(set(out))


@pytest.mark.parametrize('src', [
    'from xml.etree.ElementTree import fromstring\nfromstring(b)',
    'from xml.etree.ElementTree import fromstring as fs\nfs(b)',
    'from xml.etree.ElementTree import XMLParser as P',
    'import xml.dom.minidom as m\nm.parseString(b)',
    'from xml.dom import minidom\nminidom.parseString(b)',
    'from xml.dom.minidom import parseString',
    'import xml.sax\nxml.sax.parseString(b, h)',
    'from xml.parsers import expat',
    'import lxml.etree as le\nle.fromstring(b)',
    'from xml.etree import ElementTree as E\nE.fromstring(b)',
    'from xml.etree import cElementTree as C\nC.parse(f)',
    'import xml.etree.ElementTree as Tree\nTree.iterparse(f)',
    'import xml.etree.ElementTree\nxml.etree.ElementTree.parse(f)',
    'def f():\n    import xml.etree.ElementTree as X\n    return X.XML(b)',
])
def test_the_xml_guard_catches_aliased_and_from_imports(src):
    assert xml_offenders(src) != []


@pytest.mark.parametrize('src', [
    'import xml.etree.ElementTree as ET\ne = ET.Element("a")\ntry:\n    pass\nexcept ET.ParseError:\n    pass',
    'from app import safe_xml\nsafe_xml.fromstring(b)',
    'from app import safe_xml as _safe_xml\n_safe_xml.fromstring(b)',
    'import json\njson.loads(s)',
])
def test_the_xml_guard_leaves_safe_uses_alone(src):
    assert xml_offenders(src) == []


def test_no_xml_parse_outside_safe_xml_by_ast():
    root = pathlib.Path(__file__).resolve().parents[1] / 'app'
    found = []
    for f in sorted(root.rglob('*.py')):
        if f.name == 'safe_xml.py':
            continue
        found += xml_offenders(f.read_text(encoding='utf-8'), str(f.relative_to(root)))
    assert found == []
