"""v173 (D): Movie Night and Genre Roulette no longer label a pushed release 'Prowlarr' when it has
no tracker name; their searches run on Luma's own trackers (v172)."""
import ast
import pathlib

import pytest

from app import genre_roulette, movie_night

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('mod', [movie_night, genre_roulette])
def test_the_fallback_label_is_neutral(mod):
    assert mod.PUSH_INDEXER_FALLBACK == 'Luma'


@pytest.mark.parametrize('name', ['app/movie_night.py', 'app/genre_roulette.py'])
def test_no_prowlarr_string_reaches_a_payload_or_message(name):
    tree = ast.parse((ROOT / name).read_text())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    hits = [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and 'prowlarr' in n.value.lower()
            and id(n) not in docs]
    assert hits == [], f'{name}: Prowlarr in a string at lines {hits}'


@pytest.mark.parametrize('iso,want', [('2026-01-09', 'Jan 9'), ('2026-10-05T00:00:00Z', 'Oct 5'), ('2026-05-31', 'May 31'),
                                      ('', None), ('bad-date-x', None)])
def test_the_digital_release_label_is_sentence_case(iso, want):
    """v173 (Designer): the poster label read 'OCT 5' (server upper-cased it); now 'Oct 5'."""
    from app.digital_release_cache import format_digital_label
    assert format_digital_label(iso) == want
